"""ARCH-M2: solo/LMS learner keycloak_sub grows Person/issuer+sub coverage (no Account)."""
from __future__ import annotations

import json

from sqlalchemy import select

from test_operational import system  # noqa: F401
from test_crm import crm  # noqa: F401
from app.crm_models import Account, OidcLink, Person
from app.integrations import sign_payload
from app.db import now as db_now


def _auth_headers(secret: str, body: bytes):
    ts = str(int(db_now().timestamp()))
    return {
        "Authorization": f"Bearer {secret}",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sign_payload(secret, body, ts),
        "Content-Type": "application/json",
    }


def test_solo_lead_with_keycloak_sub_grows_learner_coverage(crm, monkeypatch):
    monkeypatch.setenv("DEFAULT_PROJECT_ID", "p")
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://auth.solo-pr.ru/realms/solo-pr")

    before = crm.client.get("/api/crm/oidc/coverage").json()
    body = json.dumps({
        "type": "lead.created",
        "event_id": "arch-m2-solo-lead-1",
        "email": "learner-solo@example.ru",
        "name": "Solo Learner",
        "source": "solo_pr",
        "consent": True,
        "ads_consent": False,
        "keycloak_sub": "kc-learner-solo-1",
        "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
        "service": "Курс",
        "project_id": "p",
    }).encode()
    r = crm.client.post(
        "/api/crm/integrations/leads",
        content=body,
        headers=_auth_headers("inbound-secret", body),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("person_id")

    after = crm.client.get("/api/crm/oidc/coverage").json()
    assert after["learners_with_person"] >= before.get("learners_with_person", 0) + 1
    assert after["persons_with_oidc"] >= before["persons_with_oidc"] + 1
    assert "handoff" in after

    with crm.db() as db:
        link = db.scalar(
            select(OidcLink).where(
                OidcLink.issuer == "https://auth.solo-pr.ru/realms/solo-pr",
                OidcLink.subject == "kc-learner-solo-1",
            )
        )
        assert link is not None
        assert link.person_id
        assert link.account_id is None
        assert db.scalar(select(Account).where(Account.email == "learner-solo@example.ru")) is None
        assert db.get(Person, link.person_id) is not None


def test_lms_completion_attaches_learner_oidc(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://auth.solo-pr.ru/realms/solo-pr")

    contact = crm.client.post(
        "/api/crm/projects/p/contacts",
        json={"name": "LMS Learner", "email": "lms-learner@example.ru", "source": "manual"},
    ).json()
    before = crm.client.get("/api/crm/oidc/coverage").json()
    body = json.dumps({
        "type": "learning.completed",
        "event_id": "arch-m2-lms-1",
        "voronka_profile_id": contact["id"],
        "email": "lms-learner@example.ru",
        "course_id": "demo-course",
        "keycloak_sub": "kc-learner-lms-1",
        "evidence": "sandbox complete",
    }).encode()
    r = crm.client.post(
        "/api/crm/integrations/lms_end/completions",
        content=body,
        headers=_auth_headers("inbound-secret", body),
    )
    assert r.status_code == 200, r.text
    after = crm.client.get("/api/crm/oidc/coverage").json()
    assert after["learners_with_person"] >= before.get("learners_with_person", 0) + 1
    with crm.db() as db:
        link = db.scalar(select(OidcLink).where(OidcLink.subject == "kc-learner-lms-1"))
        assert link and link.person_id and link.account_id is None
