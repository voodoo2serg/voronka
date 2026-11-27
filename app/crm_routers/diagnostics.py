"""Dedicated diagnostic claims intake. Email equality never proves ownership."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.crm_models import (
    Consent,
    ContactPoint,
    ContactVerification,
    IntegrationInbox,
    Interaction,
    MergeReview,
    Person,
    PersonLink,
    Profile,
)
from app.crm_service import uid
from app.db import Contact as FunnelContact
from app.db import Project, Session, now
from app.person_graph import address_hash, upsert_contact_point

router = APIRouter()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Contact(Strict):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=250)
    telegram: str = Field(default="", max_length=100)
    email_verified: Literal[False]


class ConsentReceipt(Strict):
    processing: Literal[True]
    marketing: bool
    version: str = Field(min_length=1, max_length=80)
    processing_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    marketing_text_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    processing_text: str | None = Field(default=None, max_length=4000)
    marketing_text: str | None = Field(default=None, max_length=4000)
    received_at: str | None = Field(default=None, max_length=40)


class Summary(Strict):
    current_total: int = Field(ge=0, le=50)
    target_total: int = Field(ge=0, le=50)
    effort: int = Field(ge=0, le=20)
    priorities: list[Literal["pr_asset", "personal_asset", "smart", "business", "meaning"]] = Field(max_length=5)


class Completion(Strict):
    event_id: str = Field(max_length=80)
    kind: Literal["prisma5.completed"]
    diagnostic_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    methodology_version: Literal["1.1"]
    project_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    cohort: str = Field(max_length=80)
    completed_at: str = Field(max_length=40)
    contact: Contact
    consents: ConsentReceipt
    summary: Summary
    report: dict | None = None


class Snapshot(Completion):
    kind: Literal["prisma5.snapshot"]


class Evidence(Strict):
    method: Literal["email_link", "telegram_bot_start"]
    email_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    chat_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    user_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    connection_id: str | None = Field(default=None, max_length=36)
    owner_session_matched: bool = False


class Verification(Strict):
    event_id: str = Field(max_length=100)
    kind: Literal["prisma5.verified"]
    diagnostic_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    project_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    channel: Literal["email", "telegram"]
    verified_at: str = Field(max_length=40)
    evidence: Evidence


def validate_full_record(body: Completion):
    for field in ["processing", "marketing"]:
        value = getattr(body.consents, field + "_text")
        if value is not None and hashlib.sha256(value.encode()).hexdigest() != getattr(body.consents, field + "_text_hash"):
            raise HTTPException(422, "Consent text hash mismatch")
    if body.report is None:
        return  # Backward-compatible intake for previously queued v1.1 events.
    report = body.report
    try:
        submission = report["submission"]
        current, target, answers = submission["current"], submission["target"], submission["answers"]
        valid = (
            report["id"] == body.diagnostic_id
            and submission["contact"]["email"].strip().lower() == body.contact.email.strip().lower()
            and submission["consents"]["processing"] is True
            and submission["consents"]["marketing"] == body.consents.marketing
            and submission["consents"]["version"] == body.consents.version
            and len(current) == len(target) == 5 and len(answers) == 3
            and all(type(a) is int and type(b) is int and 0 <= a <= b <= 10 for a, b in zip(current, target))
            and all(isinstance(a, str) and len(a) <= 3000 for a in answers)
            and sum(current) == body.summary.current_total and sum(target) == body.summary.target_total
            and len(report["methodology"]["axes"]) == 5
        )
    except (KeyError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise HTTPException(422, "Invalid full diagnostic record")


def apply_verification(db, body: Verification):
    link = db.scalar(select(PersonLink).where(PersonLink.service == "solo_pr", PersonLink.native_id == "prisma5:" + body.diagnostic_id))
    if not link:
        raise HTTPException(503, "Completion must be delivered before proof")
    completed = db.scalar(select(Interaction).where(Interaction.source_service == "solo_pr", Interaction.external_event_key == "prisma5:" + body.diagnostic_id))
    if not completed or completed.project_id != body.project_id:
        raise HTTPException(403, "Diagnostic project differs")
    if body.channel == "email":
        point = db.scalars(select(ContactPoint).where(ContactPoint.person_id == link.person_id, ContactPoint.channel == "email")).one()
        expected = hashlib.sha256(point.normalized_value.encode()).hexdigest()
        if body.evidence.method != "email_link" or body.evidence.email_hash != expected:
            raise HTTPException(422, "Email proof does not match")
    else:
        evidence = body.evidence
        if evidence.method != "telegram_bot_start" or not evidence.chat_id or evidence.chat_id != evidence.user_id or evidence.connection_id != os.getenv("PRISMA_TELEGRAM_CONNECTION_ID"):
            raise HTTPException(422, "Telegram proof does not match")
        digest = address_hash("telegram", evidence.chat_id)
        point = db.scalar(select(ContactPoint).where(ContactPoint.person_id == link.person_id, ContactPoint.channel == "telegram", ContactPoint.address_hash == digest))
        if not point:
            point = ContactPoint(id=uid(), person_id=link.person_id, channel="telegram", raw_value=evidence.chat_id, normalized_value=evidence.chat_id, address_hash=digest)
            db.add(point)
            db.flush()
    point.verified = True
    db.add(ContactVerification(id=uid(), contact_point_id=point.id, method=body.evidence.method, outcome="verified", evidence=json.dumps(body.evidence.model_dump(exclude_none=True))))
    receipt = completed.payload["consent_receipt"]
    revoked_elsewhere = db.scalar(select(Consent.id).join(ContactPoint, Consent.contact_point_id == ContactPoint.id).where(ContactPoint.channel == body.channel, ContactPoint.address_hash == point.address_hash, Consent.purpose == "marketing", Consent.status == "revoked").limit(1))
    for purpose, granted in [("service", True), ("marketing", receipt["marketing"])]:
        pending = purpose == "marketing" and granted and (revoked_elsewhere or not body.evidence.owner_session_matched)
        status = "unknown" if pending else "granted" if granted else "denied"
        existing = db.scalar(select(Consent).where(Consent.contact_point_id == point.id, Consent.purpose == purpose))
        if existing and existing.status == "revoked":
            continue
        if existing is None:
            db.add(Consent(id=uid(), contact_point_id=point.id, purpose=purpose, status=status, lawful_basis="consent", evidence=json.dumps({"diagnostic_id": body.diagnostic_id, "receipt_event": completed.external_event_key, "version": receipt["version"], "proof_event": body.event_id})))
    for profile in db.scalars(select(Profile).where(Profile.person_id == link.person_id, Profile.project_id == body.project_id)):
        profile.extra = {**(profile.extra or {}), body.channel + "_verified": True, "processing_consent": True, "marketing_consent_claimed": receipt["marketing"]}
        label = "Email подтверждён" if body.channel == "email" else "Telegram подтверждён"
        profile.tags = list(dict.fromkeys([tag for tag in (profile.tags or []) if tag != "неподтверждённый контакт"] + [label, "Личность не проверена"]))
    db.add(Interaction(id=uid(), person_id=link.person_id, project_id=body.project_id, contact_point_id=point.id, kind="diagnostic.contact_verified", direction="system", channel=body.channel, evidence_status="verified", source_service="solo_pr", external_event_key=body.event_id, payload={"diagnostic_id": body.diagnostic_id, "verified_at": body.verified_at, "proof": body.evidence.model_dump(exclude_none=True), "consent_receipt": receipt}))
    return link.person_id


class Deletion(Strict):
    event_id: str = Field(max_length=80)
    kind: Literal["prisma5.deleted"]
    diagnostic_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    project_id: str = Field(pattern=r"^[0-9a-f-]{36}$")


@router.post("/integrations/solo_pr/diagnostics")
async def receive_diagnostic(request: Request):
    try:
        return await _receive_diagnostic(request)
    except SQLAlchemyError:
        raise HTTPException(503, "Diagnostic storage temporarily unavailable") from None


async def _receive_diagnostic(request: Request):
    if os.getenv("PRISMA5_ENABLED") != "1":
        raise HTTPException(404, "Not enabled")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 524288:
            raise HTTPException(413, "Request too large")
    secret = os.getenv("PRISMA_CRM_SECRET", "")
    ts = request.headers.get("x-prisma-timestamp", "")
    nonce = request.headers.get("x-prisma-nonce", "")
    if not secret or not re.fullmatch(r"[0-9]{10,12}", ts) or abs(time.time() - int(ts)) > 300 or not re.fullmatch(r"[0-9a-f]{48}", nonce):
        raise HTTPException(401, "Invalid signature")
    digest = hashlib.sha256(raw).hexdigest()
    message = f"v1\n{ts}\n{nonce}\nPOST\n{request.url.path}\n{digest}"
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if request.url.query or not hmac.compare_digest(expected, request.headers.get("x-prisma-signature", "")):
        raise HTTPException(401, "Invalid signature")
    try:
        payload = json.loads(raw)
        schema = {"prisma5.deleted": Deletion, "prisma5.verified": Verification, "prisma5.snapshot": Snapshot}.get(payload.get("kind"), Completion)
        body = schema.model_validate(payload)
    except (ValidationError, ValueError, AttributeError) as exc:
        raise HTTPException(422, "Invalid diagnostic") from exc
    if body.project_id not in {p.strip() for p in os.getenv("PRISMA_PROJECT_IDS", "").split(",") if p.strip()}:
        raise HTTPException(403, "Project not allowed")
    expected_event = "prisma5-proof:" + body.diagnostic_id + ":" + body.channel if isinstance(body, Verification) else ("prisma5-delete:" if isinstance(body, Deletion) else "prisma5-snapshot:" if isinstance(body, Snapshot) else "prisma5:") + body.diagnostic_id
    if body.event_id != expected_event:
        raise HTTPException(422, "Invalid event identity")
    if isinstance(body, Completion) and (not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", body.contact.email) or body.summary.target_total - body.summary.current_total != body.summary.effort):
        raise HTTPException(422, "Invalid summary or email")
    if isinstance(body, Completion):
        validate_full_record(body)
    with Session.begin() as db:
        if db.get_bind().dialect.name == "postgresql":
            lock_key = int.from_bytes(hashlib.sha256(body.diagnostic_id.encode()).digest()[:8], signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
        if not db.get(Project, body.project_id):
            raise HTTPException(404, "Project not found")
        # Existing durable inbox supplies a unique nonce constraint without a new database.
        db.execute(delete(IntegrationInbox).where(IntegrationInbox.source == "prisma_nonce", IntegrationInbox.created_at < now() - timedelta(seconds=601)))
        nonce_key = hashlib.sha256(nonce.encode()).hexdigest()
        try:
            with db.begin_nested():
                db.add(IntegrationInbox(id=uid(), source="prisma_nonce", event_key=nonce_key, kind="nonce", payload={}, state="applied"))
                db.flush()
        except IntegrityError as exc:
            raise HTTPException(401, "Replayed request") from exc
        previous = db.scalar(select(IntegrationInbox).where(IntegrationInbox.source == "solo_pr_prisma", IntegrationInbox.event_key == body.event_id))
        if previous:
            if previous.payload.get("body_hash") != digest:
                raise HTTPException(409, "Diagnostic identity conflict")
            return {"diagnostic_id": body.diagnostic_id, "person_id": previous.payload.get("person_id"), "duplicate": True}
        deleted = db.scalar(select(IntegrationInbox).where(IntegrationInbox.source == "solo_pr_prisma", IntegrationInbox.event_key == "prisma5-delete:" + body.diagnostic_id))
        if not isinstance(body, Deletion) and deleted:
            raise HTTPException(410, "Diagnostic deleted")
        if isinstance(body, Deletion):
            db.add(IntegrationInbox(id=uid(), source="solo_pr_prisma", event_key=body.event_id, kind="subject.delete", payload={"body_hash": digest, "diagnostic_id": body.diagnostic_id, "project_id": body.project_id, "user_id": "prisma5:" + body.diagnostic_id}, state="pending_review"))
            return {"diagnostic_id": body.diagnostic_id, "status": "recorded_for_review"}
        if isinstance(body, Verification):
            person_id = apply_verification(db, body)
            db.add(IntegrationInbox(id=uid(), source="solo_pr_prisma", event_key=body.event_id, kind=body.kind, payload={"body_hash": digest, "diagnostic_id": body.diagnostic_id, "person_id": person_id, "project_id": body.project_id}, state="applied"))
            return {"diagnostic_id": body.diagnostic_id, "person_id": person_id, "verified": True}
        native = "prisma5:" + body.diagnostic_id
        link = db.scalar(select(PersonLink).where(PersonLink.service == "solo_pr", PersonLink.native_id == native))
        if link:
            person = db.get(Person, link.person_id)
        else:
            person = Person(id=uid(), lifecycle_stage="provisional", display_name=body.contact.name, extra={"source": "prisma5", "identity_status": "unverified_claim"})
            db.add(person)
            db.flush()
            db.add(PersonLink(id=uid(), person_id=person.id, service="solo_pr", native_id=native, verified_by="unverified_claim"))
            local_contact = FunnelContact(id=uid(), person_id=person.id)
            db.add(local_contact)
            db.flush()
            db.add(Profile(id=uid(), contact_id=local_contact.id, person_id=person.id, project_id=body.project_id, name=body.contact.name, email=body.contact.email.strip().lower(), source="prisma5", tags=["ПРИЗМА 5", "неподтверждённый контакт"], extra={"diagnostic_id": body.diagnostic_id, "identity_status": "unverified_claim"}, stopped=True))
            email = body.contact.email.strip().lower()
            digest_address = address_hash("email", email)
            contact = ContactPoint(id=uid(), person_id=person.id, channel="email", raw_value=email, normalized_value=email, address_hash=digest_address, verified=False)
            db.add(contact)
            if body.contact.telegram:
                upsert_contact_point(db, person, channel="telegram", value=body.contact.telegram, verified=False)
            # Existing marketing/transactional permissions are never changed by a guest claim.
            for match in db.scalars(select(ContactPoint).where(ContactPoint.channel == "email", ContactPoint.address_hash == digest_address, ContactPoint.person_id != person.id).limit(5)):
                db.add(MergeReview(id=uid(), source_person_id=person.id, target_person_id=match.person_id, reason="Unverified diagnostic email claim", evidence={"source": native, "verified": False}))
        receipt = body.consents.model_dump()
        interaction_kind = "diagnostic.archived" if isinstance(body, Snapshot) else "diagnostic.completed"
        db.add(Interaction(id=uid(), person_id=person.id, project_id=body.project_id, kind=interaction_kind, direction="system", channel="web", evidence_status="unverified_claim", source_service="solo_pr", external_event_key=body.event_id, payload={"diagnostic_id": body.diagnostic_id, "methodology_version": body.methodology_version, "cohort": body.cohort, "summary": body.summary.model_dump(), "consent_receipt": receipt, "report": body.report, "retention": "no_automatic_expiry", "marketing_permission": "not_granted_without_address_proof"}))
        db.add(IntegrationInbox(id=uid(), source="solo_pr_prisma", event_key=body.event_id, kind=body.kind, payload={"body_hash": digest, "person_id": person.id, "diagnostic_id": body.diagnostic_id, "project_id": body.project_id}, state="applied"))
        try:
            db.flush()
        except IntegrityError as exc:
            raise HTTPException(503, "Retry diagnostic delivery") from exc
        return {"diagnostic_id": body.diagnostic_id, "person_id": person.id, "identity_status": "provisional"}
