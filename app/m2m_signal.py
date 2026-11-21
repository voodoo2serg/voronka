"""Signal M2M auth: HMAC wire v1 + scopes + jti replay + project allowlist.

Separate from solo_pr/LMS integration secrets and from crm_session.
See signal docs/ADR-002-M2M-AUTH.md and docs/M2M_COMMUNICATIONS_OPENAPI.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import HTTPException, Request

from app import auth
from app.integrations import (
    format_signature,
    sign_payload,
    verify_signed_request,
    _header_ci,
)

DEFAULT_SCOPES = (
    "campaign:*",
    "asset:read",
    "asset:import",
    "contact:segment:read",
    "connection:hub:read",
    # Wave C additive (Signal Wave D may request these; existing scopes unchanged)
    "person:read",
    "person:write",  # ARCH-L2: ensure Person↔OIDC on first learner/product SSO
    "timeline:read",
    "timeline:write",  # CJ-108: Signal CE → Voronka Communication/DeliveryEvent ingest
    "consent:write",
    # PDialog P0 — Signal publish façade → Voronka Version SoR
    "scenario:*",
    # OPS-104 → OPS-105 aggregated integration/DLQ status (read-only)
    "integrations:status:read",
)

CAMPAIGN_STAR = {
    "campaign:create",
    "campaign:preflight",
    "campaign:launch",
    "campaign:cancel",
    "campaign:read",
}

SCENARIO_STAR = {
    "scenario:publish",
    "scenario:pause",
    "scenario:status",
}

JTI_TTL_SECONDS = 600  # ≥ 2× skew (300s)
RATE_WINDOW_SECONDS = 60
RATE_MAX_CREATE_LAUNCH = 30


def signal_m2m_secret() -> str:
    return (os.getenv("SIGNAL_M2M_SECRET") or "").strip()


def granted_scopes() -> set[str]:
    raw = os.getenv("SIGNAL_M2M_SCOPES", ",".join(DEFAULT_SCOPES))
    return {s.strip() for s in raw.split(",") if s.strip()}


def project_allowlist() -> set[str] | None:
    """None means allow any project (local/dev). Empty set after filter means deny all if env set blank intentionally — use '*' for any."""
    raw = os.getenv("SIGNAL_M2M_PROJECT_ALLOWLIST")
    if raw is None or raw.strip() == "":
        return None
    if raw.strip() == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def expand_scopes(scopes: set[str]) -> set[str]:
    out = set(scopes)
    if "campaign:*" in out:
        out |= CAMPAIGN_STAR
    if "scenario:*" in out:
        out |= SCENARIO_STAR
    return out


def parse_scope_header(value: str) -> set[str]:
    return {s.strip() for s in (value or "").split(",") if s.strip()}


@dataclass(frozen=True)
class SignalM2MPrincipal:
    service_id: str
    scopes: set[str]
    jti: str


def _require_hmac(headers: dict, body: bytes) -> None:
    secret = signal_m2m_secret()
    if not secret:
        raise HTTPException(503, "Signal M2M not configured")
    ts = _header_ci(headers, "x-signal-timestamp", "x-voronka-timestamp")
    sig = _header_ci(headers, "x-signal-signature", "x-voronka-signature")
    if not verify_signed_request(secret, body, ts, sig):
        raise HTTPException(401, "Неверная подпись Signal M2M")


def _consume_jti(jti: str) -> None:
    if not jti or len(jti) > 120:
        raise HTTPException(401, "X-Signal-Jti обязателен")
    key = f"m2m-jti:signal:{jti}"
    # SET NX — first wins; replay rejected.
    created = auth.cache.set(key, "1", nx=True, ex=JTI_TTL_SECONDS)
    if not created:
        raise HTTPException(409, "Повторный jti (replay)")


def _rate_limit(service_id: str, purpose: str, maximum: int = RATE_MAX_CREATE_LAUNCH) -> None:
    key = f"m2m-rate:signal:{service_id}:{purpose}"
    count = auth.cache.incr(key)
    if count == 1:
        auth.cache.expire(key, RATE_WINDOW_SECONDS)
    if count > maximum:
        raise HTTPException(429, "Слишком много M2M запросов")


def require_scopes(principal: SignalM2MPrincipal, *needed: str) -> None:
    granted = expand_scopes(principal.scopes)
    for scope in needed:
        if scope not in granted:
            raise HTTPException(403, f"Недостаточно scope: нужен {scope}")


def assert_project_allowed(pid: str) -> None:
    allow = project_allowlist()
    if allow is not None and pid not in allow:
        raise HTTPException(403, "Проект не в allowlist M2M")


async def require_signal_m2m(request: Request, *needed_scopes: str) -> tuple[bytes, SignalM2MPrincipal]:
    """FastAPI dependency factory usage: Depends(lambda r: require_signal_m2m(r, 'campaign:create')) is awkward;
    prefer calling from route after reading body, or use make_m2m_dep.
    """
    body = await request.body()
    if len(body) > 256 * 1024:
        raise HTTPException(413, "Тело M2M слишком большое")
    headers = {k: v for k, v in request.headers.items()}
    _require_hmac(headers, body)
    jti = _header_ci(headers, "x-signal-jti", "x-voronka-event-id")
    _consume_jti(jti)
    service_id = _header_ci(headers, "x-signal-service") or "signal"
    expected_service = (os.getenv("SIGNAL_M2M_SERVICE_ID") or "signal").strip()
    if service_id != expected_service:
        raise HTTPException(403, "Неизвестный service id")
    requested = parse_scope_header(_header_ci(headers, "x-signal-scopes"))
    if not requested:
        raise HTTPException(401, "X-Signal-Scopes обязателен")
    allowed = expand_scopes(granted_scopes())
    if not requested <= allowed:
        raise HTTPException(403, "Запрошенные scopes не выданы сервису")
    principal = SignalM2MPrincipal(service_id=service_id, scopes=requested, jti=jti)
    if needed_scopes:
        require_scopes(principal, *needed_scopes)
    # Rate-limit mutating campaign / scenario ops
    path = request.url.path
    if path.endswith("/campaigns") and request.method == "POST":
        _rate_limit(service_id, "create")
    if path.endswith("/launch") and request.method == "POST":
        _rate_limit(service_id, "launch")
    if path.endswith("/scenarios/versions") and request.method == "POST":
        _rate_limit(service_id, "scenario_publish")
    return body, principal


def make_m2m_dep(*needed_scopes: str):
    async def _dep(request: Request):
        return await require_signal_m2m(request, *needed_scopes)

    return _dep


# Re-export for tests / outbound delivery helpers
__all__ = [
    "SignalM2MPrincipal",
    "assert_project_allowed",
    "format_signature",
    "make_m2m_dep",
    "require_scopes",
    "require_signal_m2m",
    "sign_payload",
    "signal_m2m_secret",
]
