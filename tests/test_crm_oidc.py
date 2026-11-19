"""Opt-in staff SSO + AUTH-203 cutover safety tests (no live Keycloak)."""
import json
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_operational import system  # noqa: F401 — shared isolated app fixture
from app import crm_api, auth
from app.crm_models import Account, OidcLink
from app.crm_oidc_cutover import UNUSABLE_PASSWORD_HASH, cutover_enabled
from app.crm_routers import oidc_staff, auth_staff
from app.crm_service import password_hash, password_ok
from app.db import Event, Project, Connection


@pytest.fixture
def crm(system, monkeypatch):
    """CRM session fixture; also patches OIDC router Session for callback DB writes."""
    from app import crm_deps, observability
    from app.crm_routers import (
        contacts, commerce, campaigns, outreach, integrations, connections_hub,
        ai_config, llm_drafts, portal, m2m_communications, customer_graph,
        audience_import, company_setup,
    )
    from app.ops_routers import portal as ops_portal, wizards as ops_wizards
    for mod in (
        crm_api, crm_deps, auth_staff, oidc_staff, contacts, commerce, campaigns,
        outreach, integrations, connections_hub, ai_config, llm_drafts, portal,
        m2m_communications, customer_graph, audience_import, company_setup,
        observability, ops_portal, ops_wizards,
    ):
        monkeypatch.setattr(mod, "Session", system.db)
    with system.db.begin() as db:
        db.add(Project(id="p", name="Соло-PR", goal="Продажи"))
        db.add(Project(id="secret", name="Секрет", goal="Закрытый проект"))
        db.flush()
        db.get(Connection, "tg").project_id = "p"
        db.get(Connection, "other").project_id = "secret"
        db.add(
            Account(
                id="owner-account",
                admin_id=123,
                login="owner",
                password_hash=password_hash("owner-password-123"),
                email="owner@example.ru",
            )
        )
    return system


def configure(monkeypatch, *, cutover: bool = False):
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://auth.solo-pr.ru/realms/solo-pr")
    monkeypatch.setenv("CRM_OIDC_CLIENT_ID", "voronka-crm")
    monkeypatch.setenv("CRM_OIDC_CLIENT_SECRET", "test-only-not-production")
    monkeypatch.setenv(
        "CRM_OIDC_REDIRECT_URI",
        "https://crm.solo-pr.ru/api/crm/oidc/callback",
    )
    monkeypatch.setenv("CRM_PUBLIC_URL", "https://crm.solo-pr.ru")
    monkeypatch.setenv("CRM_OIDC_CUTOVER", "1" if cutover else "0")


def test_sso_is_disabled_without_client_configuration(system, monkeypatch):
    for key in ("KEYCLOAK_ISSUER", "CRM_OIDC_CLIENT_ID", "CRM_OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)
    status = system.client.get("/api/crm/oidc/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is False
    assert body["local_login_unchanged"] is True
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
    assert "nonce" in params
    assert "code_verifier" not in response.headers["location"]
    assert params["state"][0]
    payload = json.loads(system.cache.get("crm:oidc:" + params["state"][0]))
    assert payload["nonce"] == params["nonce"][0]
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


def test_status_reports_cutover_mode(system, monkeypatch):
    configure(monkeypatch, cutover=True)
    status = system.client.get("/api/crm/oidc/status").json()
    assert status["enabled"] is True
    assert status["cutover"] is True
    assert status["local_login_unchanged"] is False
    assert status["local_login"] == "break-glass-audited"
    assert status["password_create"] == "break-glass-audited"
    assert status["recovery_path"] == "/api/crm/oidc/login?recover=1"
    assert cutover_enabled() is True


def test_recover_login_uses_forgot_credentials_endpoint(system, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(oidc_staff, "_discovery", lambda issuer: {
        "authorization_endpoint": issuer + "/protocol/openid-connect/auth",
    })
    response = system.client.get("/api/crm/oidc/login?recover=1", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert "/protocol/openid-connect/forgot-credentials?" in location
    assert "/protocol/openid-connect/auth?" not in location
    params = parse_qs(urlparse(location).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["client_id"] == ["voronka-crm"]
    state = params["state"][0]
    payload = json.loads(system.cache.get("crm:oidc:" + state))
    assert payload["recover"] is True


def test_forgot_credentials_endpoint_helper():
    auth = "https://auth.solo-pr.ru/realms/solo-pr/protocol/openid-connect/auth"
    assert oidc_staff._forgot_credentials_endpoint(auth).endswith("/forgot-credentials")
    assert not oidc_staff._forgot_credentials_endpoint(auth).endswith("/auth/forgot-credentials")


def test_cutover_blocks_routine_staff_password(crm, monkeypatch):
    configure(monkeypatch, cutover=True)
    denied = crm.client.post(
        "/api/crm/staff",
        json={"login": "oidc-mgr", "password": "manager-password-123", "project_ids": ["p"]},
    )
    assert denied.status_code == 403
    created = crm.client.post(
        "/api/crm/staff",
        json={"login": "oidc-mgr", "project_ids": ["p"], "role": "manager"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["oidc_only"] is True
    with crm.db() as db:
        account = db.get(Account, body["account_id"])
        assert account.password_hash == UNUSABLE_PASSWORD_HASH
        assert password_ok("manager-password-123", account.password_hash) is False


def test_cutover_break_glass_staff_password_is_audited(crm, monkeypatch):
    configure(monkeypatch, cutover=True)
    created = crm.client.post(
        "/api/crm/staff",
        json={
            "login": "glass-mgr",
            "password": "manager-password-123",
            "project_ids": ["p"],
            "break_glass": True,
        },
    )
    assert created.status_code == 200
    assert created.json()["oidc_only"] is False
    with crm.db() as db:
        events = [e.kind for e in db.scalars(select(Event).where(Event.kind.like("crm.staff.%")))]
        assert "crm.staff.password_break_glass" in events


def test_cutover_local_login_is_audited(crm, monkeypatch):
    configure(monkeypatch, cutover=True)
    response = crm.client.post(
        "/api/crm/login",
        json={"login": "owner", "password": "owner-password-123"},
    )
    assert response.status_code == 200
    with crm.db() as db:
        kinds = [e.kind for e in db.scalars(select(Event))]
        assert "crm.auth.local_login_break_glass" in kinds


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, *args, **kwargs):
        self.userinfo = {"sub": "kc-sub-owner-test", "email": "attacker@example.com",
                         "iss": "https://auth.solo-pr.ru/realms/solo-pr"}
        self.tokens = {"access_token": "access", "id_token": None}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, data=None):
        return _FakeResponse(self.tokens)

    def get(self, url, headers=None):
        return _FakeResponse(self.userinfo)


def test_callback_maps_linked_subject_only(crm, monkeypatch):
    configure(monkeypatch)
    issuer = "https://auth.solo-pr.ru/realms/solo-pr"
    subject = "kc-sub-owner-test"
    with crm.db.begin() as db:
        account = db.scalar(select(Account).where(Account.login == "owner"))
        assert account is not None
        db.add(
            OidcLink(
                id="link-owner-1",
                issuer=issuer,
                subject=subject,
                account_id=account.id,
                provisioning_status="linked",
            )
        )
    state = "good-state"
    crm.cache.setex(
        "crm:oidc:" + state,
        60,
        json.dumps({"verifier": "verifier-value", "nonce": "nonce-value", "return_to": "/crm/"}),
    )
    crm.client.cookies.set("crm_oidc_state", state, path="/api/crm/oidc")
    monkeypatch.setattr(oidc_staff, "_discovery", lambda iss: {
        "authorization_endpoint": iss + "/protocol/openid-connect/auth",
        "token_endpoint": iss + "/protocol/openid-connect/token",
        "userinfo_endpoint": iss + "/protocol/openid-connect/userinfo",
        "issuer": iss,
    })
    monkeypatch.setattr(oidc_staff.httpx, "Client", _FakeClient)
    # Callback issues Redis session via auth.cache — already fakeredis on system.
    response = crm.client.get(
        "/api/crm/oidc/callback?code=demo&state=" + state,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/crm/"
    assert "crm_session" in response.headers.get("set-cookie", "")
    with crm.db() as db:
        owners = list(db.scalars(select(Account).where(Account.login == "owner")))
        assert len(owners) == 1
        kinds = [e.kind for e in db.scalars(select(Event))]
        assert "crm.oidc.login" in kinds
        # Email claim must not auto-link or invent accounts.
        assert db.scalar(select(Account).where(Account.email == "attacker@example.com")) is None


def test_callback_rejects_unlinked_subject(crm, monkeypatch):
    configure(monkeypatch)
    state = "unlinked-state"
    crm.cache.setex(
        "crm:oidc:" + state,
        60,
        json.dumps({"verifier": "v", "nonce": "n", "return_to": "/crm/"}),
    )
    crm.client.cookies.set("crm_oidc_state", state, path="/api/crm/oidc")

    class UnlinkedClient(_FakeClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.userinfo = {
                "sub": "unknown-sub",
                "iss": "https://auth.solo-pr.ru/realms/solo-pr",
            }

    monkeypatch.setattr(oidc_staff, "_discovery", lambda iss: {
        "token_endpoint": iss + "/protocol/openid-connect/token",
        "userinfo_endpoint": iss + "/protocol/openid-connect/userinfo",
        "authorization_endpoint": iss + "/protocol/openid-connect/auth",
        "issuer": iss,
    })
    monkeypatch.setattr(oidc_staff.httpx, "Client", UnlinkedClient)
    response = crm.client.get("/api/crm/oidc/callback?code=demo&state=" + state)
    assert response.status_code == 403


def test_oidc_link_revoke_revokes_sessions(crm, monkeypatch):
    configure(monkeypatch)
    with crm.db.begin() as db:
        db.add(
            OidcLink(
                id="link-revoke-1",
                issuer="https://auth.solo-pr.ru/realms/solo-pr",
                subject="sub-revoke",
                account_id="owner-account",
                provisioning_status="linked",
            )
        )
    token = auth.issue_session(123)
    assert crm.cache.get(auth.session_key(token))
    response = crm.client.post("/api/crm/oidc-links/link-revoke-1/revoke")
    assert response.status_code == 200
    assert response.json()["provisioning_status"] == "revoked"
    assert crm.cache.get(auth.session_key(token)) is None


def test_link_script_rollback_revokes_sessions(crm, monkeypatch):
    """CLI --rollback must match HTTP revoke and drop active CRM sessions."""
    from argparse import Namespace
    from scripts import link_crm_oidc as link_script

    configure(monkeypatch)
    monkeypatch.setattr(link_script, "Session", crm.db)
    monkeypatch.setattr(link_script.auth, "cache", crm.cache)
    with crm.db.begin() as db:
        db.add(
            OidcLink(
                id="link-cli-1",
                issuer="https://auth.solo-pr.ru/realms/solo-pr",
                subject="sub-cli-rollback",
                account_id="owner-account",
                provisioning_status="linked",
            )
        )
    token = auth.issue_session(123)
    assert crm.cache.get(auth.session_key(token))
    link_script.apply_link(Namespace(
        issuer="https://auth.solo-pr.ru/realms/solo-pr",
        subject="sub-cli-rollback",
        account_id="owner-account",
        person_id="",
        email_hint="",
        apply=False,
        rollback=True,
    ))
    assert crm.cache.get(auth.session_key(token)) is None
    with crm.db() as db:
        row = db.get(OidcLink, "link-cli-1")
        assert row.provisioning_status == "revoked"


def test_staff_password_preserves_whitespace(crm, monkeypatch):
    configure(monkeypatch, cutover=True)
    raw = "  padded-password-12  "
    created = crm.client.post(
        "/api/crm/staff",
        json={
            "login": "space-mgr",
            "password": raw,
            "project_ids": ["p"],
            "break_glass": True,
        },
    )
    assert created.status_code == 200
    login = crm.client.post("/api/crm/login", json={"login": "space-mgr", "password": raw})
    assert login.status_code == 200
    stripped = crm.client.post(
        "/api/crm/login",
        json={"login": "space-mgr", "password": raw.strip()},
    )
    assert stripped.status_code == 401
