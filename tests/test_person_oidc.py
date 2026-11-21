"""ARCH-L2: Person↔OIDC on first staff/learner SSO (no empty Person after green SSO)."""
from __future__ import annotations

import json
import secrets

from sqlalchemy import select

from test_crm_oidc import configure, crm, _FakeClient
from app.crm_models import Account, OidcLink, Person, PersonLink
from app.crm_routers import oidc_staff
from app.db import Event
from app.integrations import format_signature, sign_payload
from app.db import now as db_now


SECRET = "signal-m2m-test-secret"


def _m2m_headers(body: bytes, *, scopes: str, jti: str | None = None):
    ts = str(int(db_now().timestamp()))
    digest = sign_payload(SECRET, body, ts)
    return {
        "Content-Type": "application/json",
        "X-Signal-Timestamp": ts,
        "X-Signal-Signature": format_signature(digest),
        "X-Signal-Jti": jti or secrets.token_urlsafe(12),
        "X-Signal-Service": "signal",
        "X-Signal-Scopes": scopes,
    }


def test_staff_callback_attaches_person_on_first_sso(crm, monkeypatch):
    configure(monkeypatch)
    issuer = "https://auth.solo-pr.ru/realms/solo-pr"
    subject = "kc-sub-owner-test"
    with crm.db.begin() as db:
        account = db.scalar(select(Account).where(Account.login == "owner"))
        db.add(
            OidcLink(
                id="link-owner-person",
                issuer=issuer,
                subject=subject,
                account_id=account.id,
                provisioning_status="linked",
                # person_id intentionally empty — ARCH-L2 fills on SSO
            )
        )
    before = crm.client.get("/api/crm/oidc/coverage").json()
    assert before["links_missing_person"] >= 1

    state = "person-sso-state"
    crm.cache.setex(
        "crm:oidc:" + state,
        60,
        json.dumps({"verifier": "v", "nonce": "n", "return_to": "/crm/"}),
    )
    crm.client.cookies.set("crm_oidc_state", state, path="/api/crm/oidc")
    monkeypatch.setattr(oidc_staff, "_discovery", lambda iss: {
        "authorization_endpoint": iss + "/protocol/openid-connect/auth",
        "token_endpoint": iss + "/protocol/openid-connect/token",
        "userinfo_endpoint": iss + "/protocol/openid-connect/userinfo",
        "issuer": iss,
    })
    monkeypatch.setattr(oidc_staff.httpx, "Client", _FakeClient)

    response = crm.client.get(
        "/api/crm/oidc/callback?code=demo&state=" + state,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/crm/"

    with crm.db() as db:
        link = db.get(OidcLink, "link-owner-person")
        assert link.person_id
        person = db.get(Person, link.person_id)
        assert person is not None
        pl = db.scalar(
            select(PersonLink).where(
                PersonLink.service == "keycloak",
                PersonLink.native_id == subject,
            )
        )
        assert pl is not None
        assert pl.person_id == person.id
        kinds = [e.kind for e in db.scalars(select(Event))]
        assert "crm.oidc.login" in kinds
        assert "crm.oidc.person_ensured" in kinds
        # Email claim must not invent a second Account.
        assert db.scalar(select(Account).where(Account.email == "attacker@example.com")) is None

    after = crm.client.get("/api/crm/oidc/coverage").json()
    assert after["persons_with_oidc"] >= before["persons_with_oidc"]
    assert after["links_missing_person"] < before["links_missing_person"]

    status = crm.client.get("/api/crm/oidc/status").json()
    assert "coverage" in status
    assert status["coverage"]["persons_with_oidc"] == after["persons_with_oidc"]


def test_staff_callback_idempotent_person(crm, monkeypatch):
    configure(monkeypatch)
    issuer = "https://auth.solo-pr.ru/realms/solo-pr"
    subject = "kc-sub-idem"
    with crm.db.begin() as db:
        account = db.scalar(select(Account).where(Account.login == "owner"))
        person = Person(
            id="person-idem-1",
            display_name="Owner",
            lifecycle_stage="active",
            extra={},
        )
        db.add(person)
        db.flush()
        db.add(
            OidcLink(
                id="link-idem",
                issuer=issuer,
                subject=subject,
                account_id=account.id,
                person_id=person.id,
                provisioning_status="linked",
            )
        )

    def _run(state: str):
        crm.cache.setex(
            "crm:oidc:" + state,
            60,
            json.dumps({"verifier": "v", "nonce": "n", "return_to": "/crm/"}),
        )
        crm.client.cookies.set("crm_oidc_state", state, path="/api/crm/oidc")
        class Client(_FakeClient):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.userinfo = {
                    "sub": subject,
                    "iss": issuer,
                    "email": "owner@example.ru",
                }
        monkeypatch.setattr(oidc_staff, "_discovery", lambda iss: {
            "token_endpoint": iss + "/protocol/openid-connect/token",
            "userinfo_endpoint": iss + "/protocol/openid-connect/userinfo",
            "authorization_endpoint": iss + "/protocol/openid-connect/auth",
            "issuer": iss,
        })
        monkeypatch.setattr(oidc_staff.httpx, "Client", Client)
        return crm.client.get(
            "/api/crm/oidc/callback?code=demo&state=" + state,
            follow_redirects=False,
        )

    assert _run("idem-1").status_code == 303
    assert _run("idem-2").status_code == 303
    with crm.db() as db:
        link = db.get(OidcLink, "link-idem")
        assert link.person_id == "person-idem-1"
        persons = list(db.scalars(select(Person)))
        assert sum(1 for p in persons if p.id == "person-idem-1") == 1


def test_learner_m2m_ensures_person_without_account(crm, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("SIGNAL_M2M_SECRET", SECRET)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "*")
    monkeypatch.setenv("SIGNAL_M2M_SCOPES", "person:read,person:write")

    before = crm.client.get("/api/crm/oidc/coverage").json()
    body = json.dumps({
        "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
        "subject": "learner-sub-1",
        "display_name": "Learner One",
        "email_hint": "learner1@example.ru",
    }).encode()
    r = crm.client.post(
        "/api/crm/m2m/oidc-person",
        content=body,
        headers=_m2m_headers(body, scopes="person:write"),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["person_id"]
    assert payload["changed"] is True
    assert payload["account_id"] is None
    assert payload["coverage"]["persons_with_oidc"] >= before["persons_with_oidc"] + 1

    # Idempotent second call
    body2 = json.dumps({
        "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
        "subject": "learner-sub-1",
        "email_hint": "learner1@example.ru",
    }).encode()
    r2 = crm.client.post(
        "/api/crm/m2m/oidc-person",
        content=body2,
        headers=_m2m_headers(body2, scopes="person:write"),
    )
    assert r2.status_code == 200
    assert r2.json()["person_id"] == payload["person_id"]

    with crm.db() as db:
        # Email must not auto-create staff Account.
        assert db.scalar(select(Account).where(Account.email == "learner1@example.ru")) is None
        link = db.scalar(
            select(OidcLink).where(
                OidcLink.issuer == "https://auth.solo-pr.ru/realms/solo-pr",
                OidcLink.subject == "learner-sub-1",
            )
        )
        assert link.person_id == payload["person_id"]
        assert link.account_id is None


def test_m2m_oidc_person_requires_write_scope(crm, monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv("SIGNAL_M2M_SECRET", SECRET)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_SCOPES", "person:read,person:write")
    body = json.dumps({
        "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
        "subject": "learner-denied",
    }).encode()
    r = crm.client.post(
        "/api/crm/m2m/oidc-person",
        content=body,
        headers=_m2m_headers(body, scopes="person:read"),
    )
    assert r.status_code == 403


def test_email_hint_does_not_merge_distinct_subjects(crm, monkeypatch):
    """Two KC subjects with same email hint stay two Persons (no email merge)."""
    from app import person_graph as graph

    with crm.db.begin() as db:
        link_a, person_a, _ = graph.ensure_oidc_person(
            db,
            issuer="https://auth.solo-pr.ru/realms/solo-pr",
            subject="sub-a",
            email_hint="shared@example.ru",
            display_name="A",
        )
        link_b, person_b, _ = graph.ensure_oidc_person(
            db,
            issuer="https://auth.solo-pr.ru/realms/solo-pr",
            subject="sub-b",
            email_hint="shared@example.ru",
            display_name="B",
        )
        assert person_a.id != person_b.id
        assert link_a.person_id != link_b.person_id


def test_backfill_script_attaches_missing_persons(crm, monkeypatch):
    from scripts import backfill_oidc_persons as backfill

    configure(monkeypatch)
    monkeypatch.setattr(backfill, "Session", crm.db)
    with crm.db.begin() as db:
        account = db.scalar(select(Account).where(Account.login == "owner"))
        db.add(
            OidcLink(
                id="link-backfill-1",
                issuer="https://auth.solo-pr.ru/realms/solo-pr",
                subject="sub-backfill-1",
                account_id=account.id,
                provisioning_status="linked",
            )
        )
    monkeypatch.setattr(
        "sys.argv",
        ["backfill_oidc_persons.py", "--apply"],
    )
    backfill.main()
    with crm.db() as db:
        from app import person_graph

        link = db.get(OidcLink, "link-backfill-1")
        assert link.person_id
        after = person_graph.oidc_person_coverage(db)
        assert after["links_missing_person"] == 0
        assert after["persons_with_oidc"] >= 1


def test_http_oidc_link_ensures_person(crm, monkeypatch):
    configure(monkeypatch)
    response = crm.client.post(
        "/api/crm/oidc-links",
        json={
            "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
            "subject": "http-link-sub",
            "account_id": "owner-account",
            "email_hint": "owner@example.ru",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["person_id"]
    assert body["account_id"] == "owner-account"
