"""Opt-in staff SSO: safety tests without contacting Keycloak or changing passwords."""
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from test_operational import system  # noqa: F401 — shared isolated app fixture
from app.crm_routers import oidc_staff


def configure(monkeypatch):
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://auth.solo-pr.ru/realms/solo-pr")
    monkeypatch.setenv("CRM_OIDC_CLIENT_ID", "voronka-crm")
    monkeypatch.setenv("CRM_OIDC_CLIENT_SECRET", "test-only-not-production")
    monkeypatch.setenv(
        "CRM_OIDC_REDIRECT_URI",
        "https://crm.solo-pr.ru/api/crm/oidc/callback",
    )
    monkeypatch.setenv("CRM_PUBLIC_URL", "https://crm.solo-pr.ru")


def test_sso_is_disabled_without_client_configuration(system, monkeypatch):
    for key in ("KEYCLOAK_ISSUER", "CRM_OIDC_CLIENT_ID", "CRM_OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    status = system.client.get("/api/crm/oidc/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    response = system.client.get("/api/crm/oidc/login", follow_redirects=False)
    assert response.status_code == 503


def test_configuration_rejects_redirect_to_other_host(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("CRM_OIDC_REDIRECT_URI", "https://evil.example/callback")
    with pytest.raises(HTTPException) as error:
        oidc_staff._config()
    assert error.value.status_code == 503


def test_pkce_login_uses_server_side_state(system, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(oidc_staff, "_discovery", lambda issuer: {
        "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
    })
    response = system.client.get("/api/crm/oidc/login", follow_redirects=False)
    assert response.status_code == 302
    url = urlparse(response.headers["location"])
    params = parse_qs(url.query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["openid profile email"]
    assert "code_verifier" not in response.headers["location"]
    assert params["state"][0]
    assert system.cache.get("crm:oidc:" + params["state"][0]) is not None
    assert "crm_oidc_state" in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]


def test_login_stores_allowed_return_to_for_ops(system, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(oidc_staff, "_discovery", lambda issuer: {
        "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
    })
    response = system.client.get(
        "/api/crm/oidc/login?return_to=/ops/",
        follow_redirects=False,
    )
    assert response.status_code == 302
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    import json
    payload = json.loads(system.cache.get("crm:oidc:" + state))
    assert payload["return_to"] == "/ops/"


def test_safe_return_to_rejects_open_redirect():
    assert oidc_staff._safe_return_to("/ops/") == "/ops/"
    assert oidc_staff._safe_return_to("/crm/") == "/crm/"
    assert oidc_staff._safe_return_to("https://evil.example/") == "/crm/"
    assert oidc_staff._safe_return_to("/crm/../ops/") == "/crm/"


def test_callback_rejects_missing_state_before_external_calls(system, monkeypatch):
    configure(monkeypatch)
    response = system.client.get("/api/crm/oidc/callback?code=unsafe&state=not-valid")
    assert response.status_code == 401


def test_callback_rejects_replayed_state(system, monkeypatch):
    configure(monkeypatch)
    state = "state-for-test"
    system.client.cookies.set("crm_oidc_state", state, path="/api/crm/oidc")
    response = system.client.get(
        "/api/crm/oidc/callback?code=unsafe&state=" + state,
    )
    assert response.status_code == 401
