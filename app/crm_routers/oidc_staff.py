"""Keycloak authorization-code + PKCE BFF for existing CRM staff (AUTH-203).

No auto-provision, no auto-link by email, and no local password changes.
Verified issuer+sub maps only via crm_oidc_links → existing Account/Admin.
Existing CRM roles and project memberships remain authoritative.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import auth
from app.crm_deps import Session, set_session_cookie
from app.crm_models import Account, OidcLink
from app.crm_oidc_cutover import cutover_enabled, status_payload
from app.crm_service import audit
from app.db import Admin

router = APIRouter(prefix="/oidc", tags=["CRM OIDC"])

_STATE_COOKIE = "crm_oidc_state"
_STATE_TTL = 600
_ALLOWED_RETURN_TO = frozenset({"/crm/", "/ops/"})


def _safe_return_to(raw: str | None) -> str:
    path = (raw or "").strip() or "/crm/"
    return path if path in _ALLOWED_RETURN_TO else "/crm/"


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


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@router.get("/status")
def status():
    try:
        _config()
        enabled = True
    except HTTPException:
        enabled = False
    return status_payload(enabled)


@router.get("/login")
def login(return_to: str = "/crm/"):
    issuer, client_id, _, redirect_uri = _config()
    info = _discovery(issuer)
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    # One-time server-side PKCE verifier + nonce. No OAuth tokens in JS or cookies.
    auth.cache.setex(
        "crm:oidc:" + state,
        _STATE_TTL,
        json.dumps({
            "verifier": verifier,
            "nonce": nonce,
            "return_to": _safe_return_to(return_to),
        }),
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": _pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    response = RedirectResponse(info["authorization_endpoint"] + "?" + urlencode(params), status_code=302)
    response.set_cookie(
        _STATE_COOKIE, state, max_age=_STATE_TTL, secure=True,
        httponly=True, samesite="lax", path="/api/crm/oidc",
    )
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
    payload = json.loads(saved)
    verifier = payload["verifier"]
    expected_nonce = payload.get("nonce") or ""
    return_to = _safe_return_to(payload.get("return_to"))
    info = _discovery(issuer)
    tokens: dict = {}
    identity: dict = {}
    try:
        with httpx.Client(timeout=8.0, follow_redirects=False) as client:
            token_response = client.post(info["token_endpoint"], data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            })
            token_response.raise_for_status()
            tokens = token_response.json()
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                raise ValueError("missing access token")
            identity_response = client.get(
                info["userinfo_endpoint"],
                headers={"Authorization": "Bearer " + access_token},
            )
            identity_response.raise_for_status()
            identity = identity_response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(401, "Не удалось подтвердить вход в Keycloak") from exc
    # userinfo iss is optional; when present it must match configured issuer.
    claimed_iss = identity.get("iss")
    if claimed_iss is not None and claimed_iss != issuer:
        raise HTTPException(401, "Идентификатор Keycloak не совпал")
    # Nonce is carried on the ID token; when Keycloak returns id_token, reject mismatch.
    id_token = tokens.get("id_token")
    if isinstance(id_token, str) and id_token.count(".") == 2 and expected_nonce:
        try:
            mid = id_token.split(".")[1]
            mid += "=" * (-len(mid) % 4)
            claims = json.loads(base64.urlsafe_b64decode(mid.encode("ascii")))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise HTTPException(401, "Некорректный идентификационный токен") from exc
        if claims.get("iss") != issuer:
            raise HTTPException(401, "Идентификатор Keycloak не совпал")
        if claims.get("nonce") != expected_nonce:
            raise HTTPException(401, "Неверное состояние авторизации")
        aud = claims.get("aud")
        aud_ok = aud == client_id or (isinstance(aud, list) and client_id in aud)
        if not aud_ok:
            raise HTTPException(401, "Неверный получатель токена")
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
            signing_key.encode(), "\n".join((ts, "voronka", issuer, sub)).encode(),
            hashlib.sha256,
        ).hexdigest()
        try:
            with httpx.Client(timeout=5.0, follow_redirects=False) as client:
                gate = client.post(
                    access_url,
                    json={"issuer": issuer, "subject": sub},
                    headers={
                        "X-Signal-Service": "voronka",
                        "X-Signal-Timestamp": ts,
                        "X-Signal-Signature": signature,
                    },
                )
                gate.raise_for_status()
                if gate.json().get("allowed") is not True:
                    raise HTTPException(403, "Доступ к CRM не разрешён в SIGNAL")
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "Не удалось проверить разрешение CRM") from exc
    with Session.begin() as db:
        link = db.scalar(select(OidcLink).where(
            OidcLink.issuer == issuer, OidcLink.subject == sub,
            OidcLink.provisioning_status == "linked"))
        if not link or not link.account_id:
            raise HTTPException(403, "Аккаунт ещё не привязан к сотруднику CRM")
        account = db.get(Account, link.account_id)
        admin = db.get(Admin, account.admin_id) if account else None
        if not admin or not admin.active or admin.role not in {"owner", "editor", "manager"}:
            raise HTTPException(403, "Доступ к CRM не предоставлен")
        # Email claim never elevates role or creates a second owner.
        audit(
            db, admin, "oidc.login",
            issuer=issuer, subject=sub, account_id=account.id, cutover=cutover_enabled(),
        )
        session = auth.issue_session(admin.telegram_id)
    response = RedirectResponse(return_to, status_code=303)
    set_session_cookie(response, session)
    response.delete_cookie(_STATE_COOKIE, path="/api/crm/oidc")
    return response
