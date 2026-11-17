"""Opt-in Keycloak authorization-code + PKCE login for existing CRM staff.

No auto-provision, no auto-link by email, and no local password changes.
Existing CRM roles and project memberships remain authoritative.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import json
import os
import secrets
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import auth
from app.crm_deps import Session, set_session_cookie
from app.crm_models import Account, OidcLink
from app.db import Admin

router = APIRouter(prefix="/oidc", tags=["CRM OIDC"])

_STATE_COOKIE = "crm_oidc_state"
_STATE_TTL = 600


def _config() -> tuple[str, str, str, str]:
    issuer = os.getenv("KEYCLOAK_ISSUER", "").rstrip("/")
    client = os.getenv("CRM_OIDC_CLIENT_ID", "")
    secret = os.getenv("CRM_OIDC_CLIENT_SECRET", "")
    redirect_uri = os.getenv("CRM_OIDC_REDIRECT_URI", "")
    public_url = os.getenv("CRM_PUBLIC_URL", "").rstrip("/")
    if not issuer.startswith("https://") or not client or not secret:
        raise HTTPException(503, "Единый вход CRM ещё не настроен")
    if not redirect_uri.startswith(public_url + "/") or not redirect_uri.startswith("https://"):
        raise HTTPException(503, "Некорректный адрес возврата CRM")
    if not issuer.endswith("/realms/solo-pr"):
        raise HTTPException(503, "Неверный realm Keycloak")
    return issuer, client, secret, redirect_uri


def _discovery(issuer: str) -> dict:
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            response = client.get(issuer + "/.well-known/openid-configuration")
            response.raise_for_status()
            info = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Keycloak недоступен") from exc
    if info.get("issuer") != issuer:
        raise HTTPException(502, "Идентификатор Keycloak не совпал")
    for endpoint in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        url = str(info.get(endpoint, ""))
        if not url.startswith(issuer + "/protocol/openid-connect/"):
            raise HTTPException(502, "Некорректный OIDC endpoint")
    return info


@router.get("/status")
def status():
    try:
        _config()
        enabled = True
    except HTTPException:
        enabled = False
    return {"enabled": enabled, "mode": "existing-linked-staff-only", "local_login_unchanged": True}


@router.get("/login")
def login():
    issuer, client_id, _, redirect_uri = _config()
    info = _discovery(issuer)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = __import__("base64").urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    # One-time server-side PKCE verifier. No OAuth tokens in JS or cookies.
    auth.cache.setex("crm:oidc:" + state, _STATE_TTL, json.dumps({"verifier": verifier}))
    params = {
        "response_type": "code", "client_id": client_id,
        "redirect_uri": redirect_uri, "scope": "openid profile email",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
    }
    response = RedirectResponse(info["authorization_endpoint"] + "?" + urlencode(params), status_code=302)
    response.set_cookie(_STATE_COOKIE, state, max_age=_STATE_TTL, secure=True,
                        httponly=True, samesite="lax", path="/api/crm/oidc")
    return response


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        raise HTTPException(401, "Вход через Keycloak отменён")
    issuer, client_id, client_secret, redirect_uri = _config()
    cookie_state = request.cookies.get(_STATE_COOKIE, "")
    if not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        raise HTTPException(401, "Неверное состояние авторизации")
    # Atomically consume state to prevent callback replay.
    saved = auth.cache.getdel("crm:oidc:" + state)
    if not saved:
        raise HTTPException(401, "Ссылка входа истекла или уже использована")
    verifier = json.loads(saved)["verifier"]
    info = _discovery(issuer)
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            token_response = client.post(info["token_endpoint"], data={
                "grant_type": "authorization_code", "client_id": client_id,
                "client_secret": client_secret, "code": code,
                "redirect_uri": redirect_uri, "code_verifier": verifier,
            })
            token_response.raise_for_status()
            access_token = str(token_response.json().get("access_token") or "")
            if not access_token:
                raise ValueError("missing access token")
            identity_response = client.get(info["userinfo_endpoint"],
                                           headers={"Authorization": "Bearer " + access_token})
            identity_response.raise_for_status()
            identity = identity_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(401, "Не удалось подтвердить вход в Keycloak") from exc
    sub = identity.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 250:
        raise HTTPException(401, "Keycloak не предоставил идентификатор")
    # Optional SIGNAL application grant gate. Never enabled implicitly.
    # Only the verified Keycloak issuer/sub (not a claimed email) is checked.
    if os.getenv("CRM_APP_ACCESS_ENFORCE", "0") == "1":
        access_url = os.getenv("SIGNAL_ACCESS_RESOLVE_URL", "")
        signing_key = os.getenv("SIGNAL_ACCESS_SIGNING_KEY", "")
        if not access_url.startswith("https://") or len(signing_key) < 32:
            raise HTTPException(503, "Проверка права доступа CRM не настроена")
        ts = str(int(time.time()))
        signature = hmac.new(
            signing_key.encode(), "\\n".join((ts, "voronka", issuer, sub)).encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            with httpx.Client(timeout=5.0, follow_redirects=False) as client:
                gate = client.post(access_url, json={"issuer": issuer, "subject": sub},
                    headers={
                        "X-Signal-Service": "voronka",
                        "X-Signal-Timestamp": ts,
                        "X-Signal-Signature": signature,
                    })
                gate.raise_for_status()
                if gate.json().get("allowed") is not True:
                    raise HTTPException(403, "Доступ к CRM не разрешён в SIGNAL")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "Не удалось проверить разрешение CRM") from exc
    with Session() as db:
        link = db.scalar(select(OidcLink).where(
            OidcLink.issuer == issuer, OidcLink.subject == sub,
            OidcLink.provisioning_status == "linked"))
        if not link or not link.account_id:
            raise HTTPException(403, "Аккаунт ещё не привязан к сотруднику CRM")
        account = db.get(Account, link.account_id)
        admin = db.get(Admin, account.admin_id) if account else None
        if not admin or not admin.active or admin.role not in {"owner", "editor", "manager"}:
            raise HTTPException(403, "Доступ к CRM не предоставлен")
        session = auth.issue_session(admin.telegram_id)
    response = RedirectResponse("/crm/", status_code=303)
    set_session_cookie(response, session)
    response.delete_cookie(_STATE_COOKIE, path="/api/crm/oidc")
    return response
