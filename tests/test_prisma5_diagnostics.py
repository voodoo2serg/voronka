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
    ContactVerification,
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


def full_body():
    payload = body()
    processing, marketing = "Сохранение полной анкеты в CRM без автоудаления", "Согласие на рассылку"
    payload["consents"].update(processing_text=processing, marketing_text=marketing, processing_text_hash=hashlib.sha256(processing.encode()).hexdigest(), marketing_text_hash=hashlib.sha256(marketing.encode()).hexdigest(), received_at=payload["completed_at"])
    payload["report"] = {"id": payload["diagnostic_id"], "submission": {"contact": payload["contact"], "consents": {k: payload["consents"][k] for k in ["processing", "marketing", "version"]}, "current": [4] * 5, "target": [6] * 5, "answers": ["Практика на курсе", "Будущее профессии", "Развитие рынка"]}, "answerQuestions": ["Первый вопрос", "Второй вопрос", "Третий вопрос"], "methodology": {"axes": [{"axis": str(i), "levels": [{"score": j, "description": "Описание"} for j in range(11)]} for i in range(5)]}, "study": {"id": "study", "revision": "snapshot-v1", "content": {"introText": "Что исследуем и как проходить"}}}
    return payload


def proof(payload, owner=True):
    return {"event_id": "prisma5-proof:" + payload["diagnostic_id"] + ":email", "kind": "prisma5.verified", "diagnostic_id": payload["diagnostic_id"], "project_id": PROJECT, "channel": "email", "verified_at": "2026-11-27T12:05:00Z", "evidence": {"method": "email_link", "email_hash": hashlib.sha256(payload["contact"]["email"].encode()).hexdigest(), "owner_session_matched": owner}}


def test_full_record_and_receipts_are_kept(diagnostic):
    payload = full_body()
    assert send(diagnostic, payload).status_code == 200
    with diagnostic.db() as db:
        saved = db.scalar(select(Interaction).where(Interaction.external_event_key == payload["event_id"]))
        assert saved.payload["report"] == payload["report"]
        assert saved.payload["retention"] == "no_automatic_expiry"
        assert saved.payload["consent_receipt"]["processing_text"] == payload["consents"]["processing_text"]
        assert saved.payload["consent_receipt"]["marketing"] is True
    bad = full_body()
    bad["report"]["submission"]["target"] = [3] * 5
    assert send(diagnostic, bad).status_code == 422
    bad = full_body()
    bad["consents"]["processing_text"] = "Changed without changing receipt hash"
    assert send(diagnostic, bad).status_code == 422


def test_email_proof_confirms_only_its_own_contact(diagnostic):
    payload = full_body()
    assert send(diagnostic, proof(payload)).status_code == 503
    person = send(diagnostic, payload).json()["person_id"]
    bad = proof(payload)
    bad["evidence"]["email_hash"] = "a" * 64
    assert send(diagnostic, bad).status_code == 422
    event = proof(payload)
    assert send(diagnostic, event).status_code == 200
    assert send(diagnostic, event).json()["duplicate"] is True
    with diagnostic.db() as db:
        point = db.scalar(select(ContactPoint).where(ContactPoint.person_id == person, ContactPoint.channel == "email"))
        assert point.verified is True
        assert db.scalar(select(ContactVerification).where(ContactVerification.contact_point_id == point.id)).method == "email_link"
        consents = {c.purpose: c.status for c in db.scalars(select(Consent).where(Consent.contact_point_id == point.id))}
        assert consents == {"service": "granted", "marketing": "granted"}
        assert all(len(c.evidence) <= 500 for c in db.scalars(select(Consent).where(Consent.contact_point_id == point.id)))


def test_email_proof_from_other_browser_does_not_confirm_marketing_claim(diagnostic):
    payload = full_body()
    person = send(diagnostic, payload).json()["person_id"]
    assert send(diagnostic, proof(payload, owner=False)).status_code == 200
    with diagnostic.db() as db:
        point = db.scalar(select(ContactPoint).where(ContactPoint.person_id == person))
        consent = db.scalar(select(Consent).where(Consent.contact_point_id == point.id, Consent.purpose == "marketing"))
        assert point.verified is True and consent.status == "unknown"


def test_snapshot_backfill_and_existing_revocation(diagnostic):
    legacy = body()
    person = send(diagnostic, legacy).json()["person_id"]
    payload = full_body()
    payload.update(diagnostic_id=legacy["diagnostic_id"], event_id="prisma5-snapshot:" + legacy["diagnostic_id"], kind="prisma5.snapshot")
    payload["report"]["id"] = legacy["diagnostic_id"]
    assert send(diagnostic, payload).json()["person_id"] == person
    with diagnostic.db.begin() as db:
        point = db.scalar(select(ContactPoint).where(ContactPoint.person_id == person, ContactPoint.channel == "email"))
        db.add(Consent(id="already-revoked", contact_point_id=point.id, purpose="marketing", status="revoked"))
    assert send(diagnostic, proof(legacy)).status_code == 200
    with diagnostic.db() as db:
        assert db.get(Consent, "already-revoked").status == "revoked"
        assert len(db.scalars(select(Person)).all()) == 1


def test_telegram_proof_is_bound_to_configured_private_chat(diagnostic, monkeypatch):
    monkeypatch.setenv("PRISMA_TELEGRAM_CONNECTION_ID", "bot-connection")
    payload = full_body()
    person = send(diagnostic, payload).json()["person_id"]
    event = {"event_id": "prisma5-proof:" + payload["diagnostic_id"] + ":telegram", "kind": "prisma5.verified", "diagnostic_id": payload["diagnostic_id"], "project_id": PROJECT, "channel": "telegram", "verified_at": "2026-11-27T12:05:00Z", "evidence": {"method": "telegram_bot_start", "chat_id": "100", "user_id": "101", "connection_id": "bot-connection"}}
    assert send(diagnostic, event).status_code == 422
    event["evidence"]["user_id"] = "100"
    assert send(diagnostic, event).status_code == 200
    with diagnostic.db() as db:
        point = db.scalar(select(ContactPoint).where(ContactPoint.person_id == person, ContactPoint.channel == "telegram", ContactPoint.normalized_value == "100"))
        assert point.verified is True


def test_storage_errors_do_not_expose_full_private_record(diagnostic, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.crm_routers import diagnostics

    class BrokenSession:
        @staticmethod
        def begin():
            raise SQLAlchemyError("private-sql-parameters-must-not-be-disclosed")

    monkeypatch.setattr(diagnostics, "Session", BrokenSession)
    response = send(diagnostic, full_body())
    assert response.status_code == 503
    assert "private-sql-parameters" not in response.text
