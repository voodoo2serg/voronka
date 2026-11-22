"""A9-03/A9-05: LMS progress/completion → History; learner SSO → Person (no Account)."""
from __future__ import annotations

import json

from sqlalchemy import select

from test_operational import system  # noqa: F401
from test_crm import crm, contact, product, order  # noqa: F401
from app.crm_models import Account, Interaction, OidcLink, Order
from app.integrations import sign_payload
from app.db import now as db_now


def _auth_headers(secret: str, body: bytes):
    ts = str(int(db_now().timestamp()))
    return {
        "Authorization": f"Bearer {secret}",
        "X-Lms-Timestamp": ts,
        "X-Lms-Signature": sign_payload(secret, body, ts),
        "Content-Type": "application/json",
    }


def test_lms_progress_writes_history_course_stage(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    person = contact(crm, email="progress-a9@example.ru", name="Progress A9")
    item = product(crm, kind="course", name="Курс A9")
    o = order(crm, person=person, item=item)
    body = json.dumps(
        {
            "type": "progress.updated",
            "event_id": "learning.progress:learner-a9:course-a9:ol-1",
            "voronka_profile_id": person["id"],
            "email": "progress-a9@example.ru",
            "course_id": "course-a9",
            "keycloak_sub": "kc-learner-progress-a9",
            "progress_percent": 40,
            "completed": False,
            "evidence": "Module 1 done",
            "source": "lms_end",
        }
    ).encode()
    r = crm.client.post(
        "/api/crm/integrations/lms_end/progress",
        content=body,
        headers=_auth_headers("inbound-secret", body),
    )
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "learning.progress"
    detail = crm.client.get(f"/api/crm/contacts/{person['id']}").json()
    kinds = {ix["kind"] for ix in detail.get("person_interactions") or []}
    assert "learning.progress" in kinds
    ix = next(i for i in detail["person_interactions"] if i["kind"] == "learning.progress")
    assert ix["payload"].get("stage") == "course"
    assert ix["payload"].get("progress_percent") == 40
    with crm.db() as db:
        assert db.get(Order, o["id"]).academic_complete is False


def test_lms_completion_history_and_academic(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    person = contact(crm, email="complete-a9@example.ru")
    item = product(crm, kind="course", name="Курс Done")
    o = order(crm, person=person, item=item)
    body = json.dumps(
        {
            "type": "learning.completed",
            "event_id": "learning.completed:learner-a9:course-done",
            "voronka_profile_id": person["id"],
            "course_id": "Курс Done",
            "keycloak_sub": "kc-learner-complete-a9",
            "evidence": "OL attest",
            "source": "lms_end",
        }
    ).encode()
    r = crm.client.post(
        "/api/crm/integrations/lms_end/completions",
        content=body,
        headers=_auth_headers("inbound-secret", body),
    )
    assert r.status_code == 200, r.text
    detail = crm.client.get(f"/api/crm/contacts/{person['id']}").json()
    kinds = {ix["kind"] for ix in detail.get("person_interactions") or []}
    assert "learning.completed" in kinds
    with crm.db() as db:
        assert db.get(Order, o["id"]).academic_complete is True


def test_lms_person_sso_grows_learners_with_person(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.setenv("KEYCLOAK_ISSUER", "https://auth.solo-pr.ru/realms/solo-pr")
    before = crm.client.get("/api/crm/oidc/coverage").json()
    body = json.dumps(
        {
            "event_id": "lms.person:kc-a9-learner-sso-1",
            "keycloak_sub": "kc-a9-learner-sso-1",
            "email": "a9-learner-sso@example.ru",
            "name": "A9 Learner SSO",
            "issuer": "https://auth.solo-pr.ru/realms/solo-pr",
        }
    ).encode()
    r = crm.client.post(
        "/api/crm/integrations/lms_end/person",
        content=body,
        headers=_auth_headers("inbound-secret", body),
    )
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload.get("person_id")
    after = crm.client.get("/api/crm/oidc/coverage").json()
    assert after["learners_with_person"] >= before.get("learners_with_person", 0) + 1
    assert "lms_person_sso" in (after.get("handoff") or {})
    with crm.db() as db:
        link = db.scalar(select(OidcLink).where(OidcLink.subject == "kc-a9-learner-sso-1"))
        assert link and link.person_id and link.account_id is None
        assert db.scalar(select(Account).where(Account.email == "a9-learner-sso@example.ru")) is None
        # No email merge-bomb / no Account invent: second distinct sub is its own OIDC link,
        # never creates CRM Account even when email hint collides.
        body2 = json.dumps(
            {
                "keycloak_sub": "kc-a9-learner-sso-2",
                "email": "a9-learner-sso@example.ru",
                "name": "A9 Twin",
            }
        ).encode()
        r2 = crm.client.post(
            "/api/crm/integrations/lms_end/person",
            content=body2,
            headers=_auth_headers("inbound-secret", body2),
        )
        assert r2.status_code == 200, r2.text
        link2 = db.scalar(select(OidcLink).where(OidcLink.subject == "kc-a9-learner-sso-2"))
        assert link2 and link2.person_id and link2.account_id is None
        assert link.id != link2.id
        assert db.scalar(select(Account).where(Account.email == "a9-learner-sso@example.ru")) is None
