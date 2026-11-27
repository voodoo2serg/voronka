import hashlib
import hmac
import json
import secrets
import time
import uuid

import pytest
from sqlalchemy import select
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

from app.crm_models import (
    Consent,
    ContactPoint,
    IntegrationInbox,
    Interaction,
    MergeReview,
    Person,
    Profile,
)
from app.db import Project
from app.person_graph import address_hash

PROJECT = "11111111-1111-1111-1111-111111111111"
PATH = "/api/crm/integrations/solo_pr/diagnostics"


@pytest.fixture
def diagnostic(crm, monkeypatch):  # noqa: F811 — imported pytest fixture
    from app.crm_routers import diagnostics
    monkeypatch.setattr(diagnostics, "Session", crm.db)
    monkeypatch.setenv("PRISMA5_ENABLED", "1")
    monkeypatch.setenv("PRISMA_CRM_SECRET", "test-diagnostic-secret")
    monkeypatch.setenv("PRISMA_PROJECT_IDS", PROJECT)
    with crm.db.begin() as db:
        db.add(Project(id=PROJECT, name="Prisma", goal="Test"))
    return crm


def body():
    identity = str(uuid.uuid4())
    return {"event_id": "prisma5:" + identity, "kind": "prisma5.completed", "diagnostic_id": identity, "methodology_version": "1.1", "project_id": PROJECT, "cohort": "", "completed_at": "2026-11-27T12:00:00Z", "contact": {"name": "Guest", "email": "guest@example.invalid", "telegram": "", "email_verified": False}, "consents": {"processing": True, "marketing": True, "version": "prisma5-2026-11-27", "processing_text_hash": "a" * 64, "marketing_text_hash": "b" * 64}, "summary": {"current_total": 20, "target_total": 30, "effort": 10, "priorities": ["smart"]}}


def wire(value, nonce=None):
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ts, nonce = str(int(time.time())), nonce or secrets.token_hex(24)
    message = "\n".join(["v1", ts, nonce, "POST", PATH, hashlib.sha256(raw).hexdigest()])
    signature = hmac.new(b"test-diagnostic-secret", message.encode(), hashlib.sha256).hexdigest()
    return raw, {"content-type": "application/json", "x-prisma-timestamp": ts, "x-prisma-nonce": nonce, "x-prisma-signature": signature}


def send(diagnostic, value):
    raw, headers = wire(value)
    return diagnostic.client.post(PATH, content=raw, headers=headers)


def test_no_email_merge_or_permission_grant(diagnostic):
    with diagnostic.db.begin() as db:
        db.add(Person(id="existing", display_name="Verified owner"))
        db.flush()
        db.add(ContactPoint(id="verified", person_id="existing", channel="email", raw_value="guest@example.invalid", normalized_value="guest@example.invalid", address_hash=address_hash("email", "guest@example.invalid"), verified=True))
        db.flush()
        db.add(Consent(id="permission", contact_point_id="verified", purpose="marketing", status="revoked"))
    payload = body()
    response = send(diagnostic, payload)
    assert response.status_code == 200, response.text
    person = response.json()["person_id"]
    assert person != "existing"
    assert send(diagnostic, payload).json()["person_id"] == person
    with diagnostic.db() as db:
        assert db.get(Person, person).lifecycle_stage == "provisional"
        profile = db.scalar(select(Profile).where(Profile.person_id == person))
        assert profile.project_id == PROJECT and profile.stopped is True
        assert db.get(Consent, "permission").status == "revoked"
        assert len(db.scalars(select(Consent)).all()) == 1
        assert len(db.scalars(select(MergeReview)).all()) == 1
        assert len(db.scalars(select(Interaction).where(Interaction.kind == "diagnostic.completed")).all()) == 1
    changed = {**payload, "cohort": "different"}
    assert send(diagnostic, changed).status_code == 409


def test_replay_body_tampering_and_project_scope(diagnostic):
    payload = body()
    raw, headers = wire(payload)
    assert diagnostic.client.post(PATH, content=raw, headers=headers).status_code == 200
    assert diagnostic.client.post(PATH, content=raw, headers=headers).status_code == 401
    assert diagnostic.client.post(PATH, content=raw + b" ", headers=headers).status_code == 401
    assert send(diagnostic, {**body(), "project_id": "22222222-2222-2222-2222-222222222222"}).status_code == 403
    assert send(diagnostic, {**body(), "answers": ["must not be accepted"]}).status_code == 422


def test_erasure_before_delayed_completion(diagnostic):
    payload = body()
    deletion = {k: payload[k] for k in ["diagnostic_id", "project_id"]}
    deletion.update(kind="prisma5.deleted", event_id="prisma5-delete:" + payload["diagnostic_id"])
    assert send(diagnostic, deletion).status_code == 200
    assert send(diagnostic, payload).status_code == 410
    with diagnostic.db() as db:
        receipt = db.scalar(select(IntegrationInbox).where(IntegrationInbox.event_key == deletion["event_id"]))
        assert receipt.kind == "subject.delete" and receipt.state == "pending_review"
