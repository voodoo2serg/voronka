"""Person / ContactPoint / Consent / Interaction helpers (Wave C CRM-001…008)."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.crm_models import (
    Agreement,
    Communication,
    Consent,
    ContactPoint,
    ContactVerification,
    DeliveryEvent,
    Interaction,
    Journey,
    JourneyStep,
    MergeReview,
    Person,
    PersonLink,
    Profile,
    Reply,
)
from app.crm_service import normalize_email, normalize_phone, uid
from app.db import Contact, Identity, now


def address_hash(channel: str, normalized: str) -> str:
    raw = f"{channel}:{normalized}".encode()
    return hashlib.sha256(raw).hexdigest()


def resolve_person(person: Person | None) -> Person | None:
    if person is None:
        return None
    seen = set()
    while person.merged_into_id and person.id not in seen:
        seen.add(person.id)
        nxt = person  # placeholder for type checkers
        # Caller must pass db — use merged_into_id chain via helper below
        break
    return person


def ensure_person_for_contact(db, contact: Contact, display_name: str = "") -> Person:
    """Create or return Person bound to a funnel Contact (CRM-001)."""
    if contact.person_id:
        person = db.get(Person, contact.person_id)
        if person:
            if person.merged_into_id:
                person = db.get(Person, person.merged_into_id) or person
            return person
    person = Person(
        id=uid(),
        display_name=display_name or "",
        lifecycle_stage="active",
        extra={},
        created_at=now(),
        updated_at=now(),
    )
    db.add(person)
    db.flush()
    contact.person_id = person.id
    link = PersonLink(
        id=uid(),
        person_id=person.id,
        service="voronka",
        native_id=f"contact:{contact.id}",
        verified_by="system",
        created_at=now(),
    )
    db.add(link)
    db.flush()
    return person


def ensure_person_for_profile(db, profile: Profile) -> Person:
    contact = db.get(Contact, profile.contact_id)
    if contact is None:
        raise HTTPException(404, "Контакт не найден")
    person = ensure_person_for_contact(db, contact, profile.name)
    profile.person_id = person.id
    if not person.display_name and profile.name:
        person.display_name = profile.name
        person.updated_at = now()
    return person


def upsert_contact_point(
    db,
    person: Person,
    *,
    channel: str,
    value: str,
    verified: bool = False,
    connection_id: str | None = None,
    identity_id: str | None = None,
    raw_value: str | None = None,
) -> ContactPoint | None:
    """Register a channel endpoint; empty values skipped (CRM-005)."""
    value = (value or "").strip()
    if not value:
        return None
    if channel == "email":
        try:
            normalized = normalize_email(value)
        except ValueError:
            return None
    elif channel == "phone":
        try:
            normalized = normalize_phone(value)
        except ValueError:
            return None
    else:
        normalized = value
    if not normalized:
        return None
    digest = address_hash(channel, normalized)
    existing = db.scalar(
        select(ContactPoint).where(
            ContactPoint.person_id == person.id,
            ContactPoint.channel == channel,
            ContactPoint.address_hash == digest,
        )
    )
    if existing:
        if verified and not existing.verified:
            existing.verified = True
        if connection_id:
            existing.connection_id = connection_id
        if identity_id:
            existing.identity_id = identity_id
        return existing
    row = ContactPoint(
        id=uid(),
        person_id=person.id,
        channel=channel,
        raw_value=raw_value if raw_value is not None else value,
        normalized_value=normalized,
        address_hash=digest,
        verified=verified,
        reachable=True,
        connection_id=connection_id,
        identity_id=identity_id,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def sync_profile_contact_points(db, profile: Profile) -> Person:
    """Backfill Person + ContactPoints from Profile email/phone and Identities."""
    person = ensure_person_for_profile(db, profile)
    upsert_contact_point(db, person, channel="email", value=profile.email, verified=False)
    upsert_contact_point(db, person, channel="phone", value=profile.phone, verified=False)
    for identity in db.scalars(select(Identity).where(Identity.contact_id == profile.contact_id)):
        conn = identity.connection_id
        platform = getattr(db.get(__import__("app.db", fromlist=["Connection"]).Connection, conn), "platform", "telegram")
        upsert_contact_point(
            db,
            person,
            channel=platform or "telegram",
            value=identity.external_user_id,
            verified=True,
            connection_id=identity.connection_id,
            identity_id=identity.id,
            raw_value=identity.chat_id or identity.external_user_id,
        )
    return person


def verify_contact_point(db, contact_point_id: str, *, method: str, actor_id: int | None, evidence: str = "") -> ContactPoint:
    cp = db.get(ContactPoint, contact_point_id)
    if cp is None:
        raise HTTPException(404, "Контактная точка не найдена")
    cp.verified = True
    db.add(
        ContactVerification(
            id=uid(),
            contact_point_id=cp.id,
            method=method,
            outcome="verified",
            evidence=evidence,
            actor_id=actor_id,
            verified_at=now(),
        )
    )
    db.flush()
    return cp


def set_consent(
    db,
    contact_point_id: str,
    *,
    purpose: str,
    status: str,
    evidence: str = "",
    lawful_basis: str = "consent",
) -> Consent:
    if purpose not in {"marketing", "transactional", "service", "analytics"}:
        raise HTTPException(422, "Неизвестная цель согласия")
    if status not in {"granted", "denied", "revoked", "unknown"}:
        raise HTTPException(422, "Неизвестный статус согласия")
    cp = db.get(ContactPoint, contact_point_id)
    if cp is None:
        raise HTTPException(404, "Контактная точка не найдена")
    row = db.scalar(
        select(Consent).where(Consent.contact_point_id == contact_point_id, Consent.purpose == purpose)
    )
    ts = now()
    if row is None:
        row = Consent(
            id=uid(),
            contact_point_id=contact_point_id,
            purpose=purpose,
            lawful_basis=lawful_basis,
            status=status,
            evidence=evidence,
            effective_at=ts,
            revoked_at=ts if status in {"revoked", "denied"} else None,
            created_at=ts,
        )
        db.add(row)
    else:
        row.status = status
        row.evidence = evidence or row.evidence
        row.lawful_basis = lawful_basis
        row.effective_at = ts
        row.revoked_at = ts if status in {"revoked", "denied"} else None
    db.flush()
    return row


def marketing_allowed(db, person_id: str, channel: str, connection_id: str | None = None) -> tuple[bool, str]:
    """Delivery guard for marketing sends (CRM-003). Denied/revoked blocks; missing consent is soft-allow for messenger dialogs."""
    return send_allowed(
        db,
        person_id=person_id,
        channel=channel,
        purpose="marketing",
        connection_id=connection_id,
        missing_policy="soft_allow",
    )


def send_allowed(
    db,
    *,
    person_id: str | None = None,
    contact_point_id: str | None = None,
    channel: str,
    purpose: str = "marketing",
    connection_id: str | None = None,
    missing_policy: str = "soft_allow",
) -> tuple[bool, str]:
    """CRM-102 send-time / preflight: contact+channel+purpose → allow/deny.

    missing_policy:
      - soft_allow: no consent row → allow (legacy messenger dialogs)
      - deny: no consent row → block (strict marketing / email)
    """
    if purpose not in {"marketing", "transactional", "service", "analytics"}:
        raise HTTPException(422, "Неизвестная цель согласия")
    if missing_policy not in {"soft_allow", "deny"}:
        raise HTTPException(422, "Неизвестный missing_policy")

    points: list[ContactPoint] = []
    if contact_point_id:
        cp = db.get(ContactPoint, contact_point_id)
        if cp is None:
            return False, "contact_point_not_found"
        if channel and cp.channel != channel:
            return False, "channel_mismatch"
        if person_id and cp.person_id != person_id:
            return False, "person_mismatch"
        points = [cp]
        person_id = cp.person_id
    else:
        if not person_id:
            raise HTTPException(422, "person_id или contact_point_id обязателен")
        q = select(ContactPoint).where(ContactPoint.person_id == person_id, ContactPoint.channel == channel)
        if connection_id:
            q = q.where(
                (ContactPoint.connection_id == connection_id) | (ContactPoint.connection_id.is_(None))
            )
        points = list(db.scalars(q))

    if not points:
        if purpose == "transactional":
            return True, "no_contact_point_transactional"
        if missing_policy == "deny":
            return False, "no_contact_point"
        return True, "no_contact_point"

    saw_grant = False
    for cp in points:
        if not cp.reachable:
            return False, "unreachable"
        consent = db.scalar(
            select(Consent).where(Consent.contact_point_id == cp.id, Consent.purpose == purpose)
        )
        if consent and consent.status in {"denied", "revoked"}:
            return False, f"consent_{consent.status}"
        if consent and consent.status == "granted":
            saw_grant = True

    if purpose == "marketing" and missing_policy == "deny" and not saw_grant:
        return False, "consent_missing"
    return True, "ok"


def record_interaction(
    db,
    *,
    person_id: str,
    kind: str,
    direction: str = "inbound",
    channel: str = "",
    project_id: str | None = None,
    contact_point_id: str | None = None,
    communication_id: str | None = None,
    source_service: str = "voronka",
    external_event_key: str | None = None,
    payload: dict | None = None,
    evidence_status: str = "recorded",
    occurred_at: datetime | None = None,
) -> tuple[Interaction, bool]:
    """Idempotent interaction insert; duplicate external key returns existing (CRM-006)."""
    if external_event_key:
        prior = db.scalar(
            select(Interaction).where(
                Interaction.source_service == source_service,
                Interaction.external_event_key == external_event_key,
            )
        )
        if prior:
            return prior, True
    row = Interaction(
        id=uid(),
        person_id=person_id,
        project_id=project_id,
        contact_point_id=contact_point_id,
        communication_id=communication_id,
        kind=kind,
        direction=direction,
        channel=channel,
        evidence_status=evidence_status,
        source_service=source_service,
        external_event_key=external_event_key,
        payload=payload or {},
        occurred_at=occurred_at or now(),
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row, False


def create_communication(
    db,
    *,
    person_id: str,
    purpose: str = "marketing",
    channel: str = "",
    contact_point_id: str | None = None,
    project_id: str | None = None,
    campaign_id: str | None = None,
    journey_step_id: str | None = None,
    outbox_id: str | None = None,
    body_preview: str = "",
    status: str = "queued",
) -> Communication:
    row = Communication(
        id=uid(),
        person_id=person_id,
        contact_point_id=contact_point_id,
        project_id=project_id,
        campaign_id=campaign_id,
        journey_step_id=journey_step_id,
        outbox_id=outbox_id,
        direction="outbound",
        purpose=purpose,
        channel=channel,
        status=status,
        body_preview=(body_preview or "")[:500],
        attempted_at=now() if status != "queued" else None,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def record_delivery_event(
    db,
    communication_id: str,
    *,
    normalized_status: str,
    platform_status: str = "",
    evidence_ref: str = "",
    external_event_key: str | None = None,
    payload: dict | None = None,
) -> tuple[DeliveryEvent, bool]:
    if external_event_key:
        prior = db.scalar(
            select(DeliveryEvent).where(
                DeliveryEvent.communication_id == communication_id,
                DeliveryEvent.external_event_key == external_event_key,
            )
        )
        if prior:
            return prior, True
    # accepted ≠ read (CRM-006)
    if normalized_status == "accepted":
        pass
    row = DeliveryEvent(
        id=uid(),
        communication_id=communication_id,
        platform_status=platform_status,
        normalized_status=normalized_status,
        evidence_ref=evidence_ref,
        external_event_key=external_event_key,
        payload=payload or {},
        occurred_at=now(),
    )
    db.add(row)
    comm = db.get(Communication, communication_id)
    if comm and normalized_status in {"accepted", "delivered", "failed", "partial"}:
        # never promote accepted → read
        if normalized_status != "read":
            if comm.status != "delivered" or normalized_status == "failed":
                if not (comm.status == "delivered" and normalized_status == "accepted"):
                    comm.status = normalized_status if normalized_status != "read" else comm.status
    db.flush()
    return row, False


def record_reply(
    db,
    communication_id: str,
    *,
    provider_message_id: str,
    kind: str = "message",
    body_preview: str = "",
) -> tuple[Reply, bool]:
    prior = db.scalar(
        select(Reply).where(
            Reply.communication_id == communication_id,
            Reply.provider_message_id == provider_message_id,
        )
    )
    if prior:
        return prior, True
    row = Reply(
        id=uid(),
        communication_id=communication_id,
        provider_message_id=provider_message_id,
        kind=kind,
        body_preview=(body_preview or "")[:500],
        received_at=now(),
    )
    db.add(row)
    db.flush()
    return row, False


def create_journey(
    db,
    *,
    person_id: str,
    project_id: str,
    title: str = "",
    purpose: str = "warmup",
    campaign_id: str | None = None,
    steps: list[dict] | None = None,
) -> Journey:
    journey = Journey(
        id=uid(),
        person_id=person_id,
        project_id=project_id,
        campaign_id=campaign_id,
        purpose=purpose,
        status="active",
        title=title or "Прогрев",
        created_at=now(),
        updated_at=now(),
    )
    db.add(journey)
    db.flush()
    for index, step in enumerate(steps or [], start=1):
        due = step.get("due_at")
        if isinstance(due, str):
            due = datetime.fromisoformat(due)
        db.add(
            JourneyStep(
                id=uid(),
                journey_id=journey.id,
                step_no=step.get("step_no", index),
                condition=step.get("condition", ""),
                status=step.get("status", "pending"),
                due_at=due,
                notes=step.get("notes", ""),
            )
        )
    db.flush()
    return journey


def pause_journeys_on_opt_out(db, person_id: str, project_id: str | None = None) -> int:
    q = select(Journey).where(Journey.person_id == person_id, Journey.status == "active")
    if project_id:
        q = q.where(Journey.project_id == project_id)
    count = 0
    for journey in db.scalars(q):
        journey.status = "opted_out"
        journey.updated_at = now()
        count += 1
    return count


def create_agreement(
    db,
    *,
    person_id: str,
    project_id: str,
    title: str,
    deal_id: str | None = None,
    legal_status: str = "draft",
    evidence: str = "",
) -> Agreement:
    if legal_status not in {"draft", "sent", "accepted", "signed", "void", "expired"}:
        raise HTTPException(422, "Неизвестный статус договора")
    row = Agreement(
        id=uid(),
        person_id=person_id,
        project_id=project_id,
        deal_id=deal_id,
        legal_status=legal_status,
        title=title,
        offer_acceptance=legal_status in {"accepted", "signed"},
        signed_at=now() if legal_status == "signed" else None,
        evidence=evidence,
        created_at=now(),
        updated_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def apply_merge(db, review_id: str, actor_id: int | None) -> MergeReview:
    review = db.get(MergeReview, review_id)
    if review is None:
        raise HTTPException(404, "Заявка на слияние не найдена")
    if review.state != "pending":
        raise HTTPException(409, "Заявка уже обработана")
    source = db.get(Person, review.source_person_id)
    target = db.get(Person, review.target_person_id)
    if not source or not target:
        raise HTTPException(404, "Person не найден")
    if source.id == target.id:
        raise HTTPException(422, "Нельзя слить person сам с собой")
    source.merged_into_id = target.id
    source.lifecycle_stage = "merged"
    source.updated_at = now()
    # Retarget profiles/contacts/points/links soft-pointer via person_id updates
    for profile in db.scalars(select(Profile).where(Profile.person_id == source.id)):
        profile.person_id = target.id
    for contact in db.scalars(select(Contact).where(Contact.person_id == source.id)):
        contact.person_id = target.id
    for cp in db.scalars(select(ContactPoint).where(ContactPoint.person_id == source.id)):
        # move if no hash collision on target
        clash = db.scalar(
            select(ContactPoint).where(
                ContactPoint.person_id == target.id,
                ContactPoint.channel == cp.channel,
                ContactPoint.address_hash == cp.address_hash,
            )
        )
        if clash:
            continue
        cp.person_id = target.id
    for link in db.scalars(select(PersonLink).where(PersonLink.person_id == source.id)):
        exists = db.scalar(
            select(PersonLink).where(PersonLink.service == link.service, PersonLink.native_id == link.native_id)
        )
        if exists and exists.id != link.id:
            continue
        link.person_id = target.id
    review.state = "applied"
    review.actor_id = actor_id
    review.applied_at = now()
    db.flush()
    return review


def undo_merge(db, review_id: str, actor_id: int | None) -> MergeReview:
    review = db.get(MergeReview, review_id)
    if review is None:
        raise HTTPException(404, "Заявка на слияние не найдена")
    if review.state != "applied":
        raise HTTPException(409, "Отмена доступна только для applied")
    source = db.get(Person, review.source_person_id)
    if source:
        source.merged_into_id = None
        source.lifecycle_stage = "active"
        source.updated_at = now()
    review.state = "undone"
    review.undone_at = now()
    review.actor_id = actor_id
    db.flush()
    return review


def queue_merge(
    db,
    *,
    source_person_id: str,
    target_person_id: str,
    reason: str = "",
    evidence: dict | None = None,
    actor_id: int | None = None,
) -> MergeReview:
    if source_person_id == target_person_id:
        raise HTTPException(422, "Нельзя слить person сам с собой")
    if not db.get(Person, source_person_id) or not db.get(Person, target_person_id):
        raise HTTPException(404, "Person не найден")
    row = MergeReview(
        id=uid(),
        source_person_id=source_person_id,
        target_person_id=target_person_id,
        state="pending",
        reason=reason,
        evidence=evidence or {},
        actor_id=actor_id,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row
