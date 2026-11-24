"""Mailu admin REST client — create mailbox + SMTP app-token (B-04 / VR-473).

Platform env (VPS only; never ask owner in UI):
  MAILU_API_URL     — base e.g. https://mail.vodopetov.ru/api  or http://127.0.0.1:8080/api
  MAILU_API_TOKEN   — Mailu API_TOKEN (Authorization header; Bearer optional)
  MAILU_SMTP_HOST   — host stored on vault meta (default mail.vodopetov.ru)
  MAILU_SMTP_PORT   — port for vault meta (default 587)
  MAILU_ALLOWED_DOMAINS — comma list (default solo-pr.ru,vodopetov.ru)
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_DOMAINS = ("solo-pr.ru", "vodopetov.ru")
LOCAL_PART_RE = re.compile(r"^[a-z0-9]([a-z0-9._-]{0,62}[a-z0-9])?$", re.IGNORECASE)


class MailuError(Exception):
    """Raised when Mailu is unreachable or rejects the request."""

    def __init__(self, message_ru: str, *, status: int | None = None, detail: str | None = None):
        super().__init__(message_ru)
        self.message_ru = message_ru
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class MailboxCreated:
    email: str
    smtp_password: str
    smtp_host: str
    smtp_port: str
    mailu_user_created: bool
    mailu_token_created: bool


def allowed_domains() -> tuple[str, ...]:
    raw = (os.getenv("MAILU_ALLOWED_DOMAINS") or "").strip()
    if not raw:
        return DEFAULT_DOMAINS
    parts = tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return parts or DEFAULT_DOMAINS


def smtp_defaults() -> tuple[str, str]:
    host = (os.getenv("MAILU_SMTP_HOST") or os.getenv("SMTP_HOST") or "mail.vodopetov.ru").strip()
    port = (os.getenv("MAILU_SMTP_PORT") or os.getenv("SMTP_PORT") or "587").strip() or "587"
    return host, port


def api_configured() -> bool:
    return bool((os.getenv("MAILU_API_URL") or "").strip() and (os.getenv("MAILU_API_TOKEN") or "").strip())


def normalize_local_part(raw: str) -> str:
    local = (raw or "").strip().lower()
    if local.endswith("@"):
        local = local[:-1]
    if "@" in local:
        local = local.split("@", 1)[0]
    return local


def validate_local_part(local: str) -> str:
    local = normalize_local_part(local)
    if not local or not LOCAL_PART_RE.match(local):
        raise MailuError(
            "Некорректное имя ящика. Используйте латиницу, цифры, точку, дефис или подчёркивание."
        )
    if ".." in local or local.startswith(".") or local.endswith("."):
        raise MailuError("Некорректное имя ящика.")
    return local


def validate_domain(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d not in allowed_domains():
        raise MailuError(
            f"Домен недоступен. Разрешены только: {', '.join(allowed_domains())}."
        )
    return d


def _base_url() -> str:
    url = (os.getenv("MAILU_API_URL") or "").strip().rstrip("/")
    if not url:
        raise MailuError("Почта Mailu недоступна: не настроен MAILU_API_URL.")
    return url


def _headers() -> dict[str, str]:
    token = (os.getenv("MAILU_API_TOKEN") or "").strip()
    if not token:
        raise MailuError("Почта Mailu недоступна: не настроен MAILU_API_TOKEN.")
    # Mailu accepts raw token or Bearer-prefixed (removeprefix on server).
    return {
        "Authorization": token if not token.lower().startswith("bearer ") else token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _message_from_body(body: Any) -> str:
    if isinstance(body, dict):
        msg = body.get("message") or body.get("detail") or body.get("title")
        if msg:
            return str(msg)
    if isinstance(body, str) and body.strip():
        return body.strip()[:300]
    return ""


def _request(method: str, path: str, *, json: dict | None = None) -> tuple[int, Any]:
    url = f"{_base_url()}{path}"
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=8.0), follow_redirects=False) as client:
            r = client.request(method, url, headers=_headers(), json=json)
    except httpx.TimeoutException as exc:
        raise MailuError("Почта Mailu недоступна: таймаут соединения.", detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise MailuError("Почта Mailu недоступна. Проверьте сервер или обратитесь к ops.", detail=str(exc)) from exc

    body: Any
    try:
        body = r.json()
    except ValueError:
        body = r.text

    return r.status_code, body


def create_user(email: str, *, raw_password: str, comment: str = "crm-one-click") -> None:
    status, body = _request(
        "POST",
        "/v1/user",
        json={
            "email": email,
            "raw_password": raw_password,
            "comment": comment,
            "quota_bytes": 1_000_000_000,
            "global_admin": False,
            "enabled": True,
            "enable_imap": True,
            "enable_pop": True,
            "allow_spoofing": False,
            "forward_enabled": False,
            "spam_enabled": True,
            "spam_mark_as_read": True,
            "spam_threshold": 80,
            "displayed_name": email.split("@", 1)[0],
        },
    )
    if status in {200, 201}:
        return
    msg = _message_from_body(body)
    if status == 409:
        raise MailuError(f"Ящик {email} уже существует в Mailu.", status=409, detail=msg)
    if status == 404:
        raise MailuError(
            f"Домен для {email} не найден в Mailu. Ops должен добавить домен.",
            status=404,
            detail=msg,
        )
    if status in {401, 403}:
        raise MailuError("Почта Mailu недоступна: ошибка авторизации API.", status=status, detail=msg)
    raise MailuError(
        "Не удалось создать ящик в Mailu.",
        status=status,
        detail=msg or f"HTTP {status}",
    )


def create_app_token(email: str, *, comment: str = "crm-smtp") -> str | None:
    """Return SMTP app-token when Mailu exposes /v1/token; None if API lacks tokens (older builds)."""
    status, body = _request(
        "POST",
        "/v1/token",
        json={"email": email, "comment": comment},
    )
    if status in {200, 201} and isinstance(body, dict):
        token = str(body.get("token") or "").strip()
        if token:
            return token
    # Older Mailu (no token namespace in swagger) — caller falls back to account password.
    if status == 404:
        return None
    msg = _message_from_body(body)
    if status in {401, 403}:
        raise MailuError("Почта Mailu недоступна: ошибка авторизации API.", status=status, detail=msg)
    raise MailuError(
        "Ящик создан, но не удалось выпустить SMTP-токен Mailu.",
        status=status,
        detail=msg or f"HTTP {status}",
    )


def create_mailbox(local_part: str, domain: str) -> MailboxCreated:
    """Create Mailu user + app-token (or account password fallback); vault SMTP secret never logged."""
    if not api_configured():
        raise MailuError(
            "Почта Mailu недоступна: на VPS не заданы MAILU_API_URL и MAILU_API_TOKEN."
        )
    local = validate_local_part(local_part)
    dom = validate_domain(domain)
    email = f"{local}@{dom}"
    host, port = smtp_defaults()
    # Account password (webmail). Prefer Mailu app-token for SMTP when API supports it.
    account_password = secrets.token_urlsafe(24)
    create_user(email, raw_password=account_password, comment="crm-one-click-mailbox")
    app_token = create_app_token(email, comment="crm-smtp-vault")
    smtp_password = app_token or account_password
    return MailboxCreated(
        email=email,
        smtp_password=smtp_password,
        smtp_host=host,
        smtp_port=port,
        mailu_user_created=True,
        mailu_token_created=bool(app_token),
    )
