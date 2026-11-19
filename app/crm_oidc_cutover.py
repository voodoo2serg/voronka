"""AUTH-203 cutover helpers: local CRM passwords become break-glass only."""
from __future__ import annotations

import os

# Marker stored in crm_accounts.password_hash when no local password exists.
# password_ok() rejects it (wrong algorithm segment).
UNUSABLE_PASSWORD_HASH = "disabled$unusable$oidc-only"


def oidc_configured() -> bool:
    issuer = os.getenv("KEYCLOAK_ISSUER", "").rstrip("/")
    client = os.getenv("CRM_OIDC_CLIENT_ID", "")
    secret = os.getenv("CRM_OIDC_CLIENT_SECRET", "")
    return bool(issuer.startswith("https://") and client and secret and issuer.endswith("/realms/solo-pr"))


def cutover_enabled() -> bool:
    """When true: no routine local password creation; local login is audited break-glass."""
    return os.getenv("CRM_OIDC_CUTOVER", "0") == "1" and oidc_configured()


def status_payload(enabled: bool) -> dict:
    cutover = cutover_enabled()
    return {
        "enabled": enabled,
        "mode": "existing-linked-staff-only",
        "cutover": cutover,
        "local_login_unchanged": not cutover,
        "local_login": "break-glass-audited" if cutover else "primary",
        "password_create": "break-glass-audited" if cutover else "allowed",
        # BFF-mediated Keycloak reset (client + PKCE), not local CRM recovery codes.
        "recovery_path": "/api/crm/oidc/login?recover=1" if enabled else None,
    }
