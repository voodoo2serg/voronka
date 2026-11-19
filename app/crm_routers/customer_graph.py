"""Wave C CRM APIs: Person graph, timeline, consent, journeys, agreements, ledger, OIDC links."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, or_

from app import auth
from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner
from app.crm_models import (
    Account,
    Activity,
    Agreement,
    Communication,
    Consent,
    ContactPoint,
    Deal,
    Interaction,
    Journey,
    JourneyStep,
    MergeReview,
    OidcLink,
    Order,
    Payment,
    Person,
    PersonLink,
    Profile,
)
from app.crm_service import (
    audit,
    get_profile,
    manager_scope,
    project_access,
    uid,
)
from app.db import Event, now
from app import ledger
from app import person_graph as graph

router = APIRouter()


def _person_acl(db, admin, person_id: str, project_id: str | None = None) -> Person:
    person = must(db.get(Person, person_id))
    if person.merged_into_id:
        person = must(db.get(Person, person.merged_into_id))
    profiles = list(db.scalars(select(Profile).where(Profile.person_id == person.id)))
    if project_id:
        project_access(db, admin, project_id)
        profiles = [p for p in profiles if p.project_id == project_id]
    else:
        visible = []
        for p in profiles:
            try:
                project_access(db, admin, p.project_id)
                if manager_scope(admin) and p.owner_id != admin.telegram_id:
                    continue
                visible.append(p)
            except HTTPException:
                continue
        profiles = visible
    if not profiles and admin.role != "owner":
        raise HTTPException(404, "Person недоступен")
    if manager_scope(admin) and profiles and all(p.owner_id != admin.telegram_id for p in profiles):
        raise HTTPException(404, "Person недоступен")
    return person


# --- CRM-001 / CRM-005 ---


@router.post("/projects/{pid}/contacts/{profile_id}/ensure-person")
def ensure_person(pid: str, profile_id: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        profile = get_profile(db, admin, profile_id)
        if profile.project_id != pid:
            raise HTTPException(404, "Клиент недоступен")
        person = graph.sync_profile_contact_points(db, profile)
        audit(db, admin, "person.ensured", person_id=person.id, profile_id=profile.id)
        return {
            "person": encode(person),
            "contact_points": [
                encode(cp)
                for cp in db.scalars(select(ContactPoint).where(ContactPoint.person_id == person.id))
            ],
        }


@router.get("/projects/{pid}/persons/{person_id}")
def get_person(pid: str, person_id: str, admin=Depends(crm_actor)):
    with Session() as db:
        person = _person_acl(db, admin, person_id, pid)
        points = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == person.id)))
        links = list(db.scalars(select(PersonLink).where(PersonLink.person_id == person.id)))
        profiles = list(
            db.scalars(select(Profile).where(Profile.person_id == person.id, Profile.project_id == pid))
        )
        return {
            "person": encode(person),
            "contact_points": [encode(p) for p in points],
            "links": [encode(l) for l in links],
            "profiles": [encode(p) for p in profiles],
        }


class ContactPointBody(Body):
    channel: str = Field(min_length=1, max_length=30)
    value: str = Field(min_length=1, max_length=250)
    verified: bool = False


@router.post("/projects/{pid}/persons/{person_id}/contact-points")
def add_contact_point(pid: str, person_id: str, body: ContactPointBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        person = _person_acl(db, admin, person_id, pid)
        cp = graph.upsert_contact_point(
            db, person, channel=body.channel, value=body.value, verified=body.verified
        )
        if cp is None:
            raise HTTPException(422, "Пустое или некорректное значение")
        if body.verified:
            graph.verify_contact_point(
                db, cp.id, method="staff", actor_id=admin.telegram_id, evidence="crm"
            )
        audit(db, admin, "contact_point.upserted", contact_point_id=cp.id, person_id=person.id)
        return encode(cp)


@router.post("/contact-points/{contact_point_id}/verify")
def verify_point(contact_point_id: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        cp = must(db.get(ContactPoint, contact_point_id))
        _person_acl(db, admin, cp.person_id)
        graph.verify_contact_point(
            db, cp.id, method="staff", actor_id=admin.telegram_id, evidence="manual_verify"
        )
        audit(db, admin, "contact_point.verified", contact_point_id=cp.id)
        return encode(cp)


class MergeBody(Body):
    source_person_id: str
    target_person_id: str
    reason: str = Field(default="", max_length=500)


@router.post("/projects/{pid}/person-merges")
def queue_person_merge(pid: str, body: MergeBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        _person_acl(db, admin, body.source_person_id, pid)
        _person_acl(db, admin, body.target_person_id, pid)
        row = graph.queue_merge(
            db,
            source_person_id=body.source_person_id,
            target_person_id=body.target_person_id,
            reason=body.reason,
            actor_id=admin.telegram_id,
        )
        audit(db, admin, "person.merge_queued", merge_id=row.id)
        return encode(row)


@router.post("/person-merges/{merge_id}/apply")
def apply_person_merge(merge_id: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = graph.apply_merge(db, merge_id, admin.telegram_id)
        audit(db, admin, "person.merge_applied", merge_id=row.id)
        return encode(row)


@router.post("/person-merges/{merge_id}/undo")
def undo_person_merge(merge_id: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = graph.undo_merge(db, merge_id, admin.telegram_id)
        audit(db, admin, "person.merge_undone", merge_id=row.id)
        return encode(row)


@router.get("/projects/{pid}/person-merges")
def list_merges(pid: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(db.scalars(select(MergeReview).order_by(MergeReview.created_at.desc()).limit(100)))
        return [encode(r) for r in rows]


# --- CRM-002 timeline / customer 360 ---


@router.get("/projects/{pid}/persons/{person_id}/timeline")
def person_timeline(pid: str, person_id: str, limit: int = 100, admin=Depends(crm_actor)):
    if not 1 <= limit <= 500:
        raise HTTPException(422, "limit 1..500")
    with Session() as db:
        person = _person_acl(db, admin, person_id, pid)
        items: list[dict] = []
        for ix in db.scalars(
            select(Interaction)
            .where(Interaction.person_id == person.id)
            .order_by(Interaction.occurred_at.desc())
            .limit(limit)
        ):
            items.append({"type": "interaction", "source": ix.source_service, **encode(ix)})
        for profile in db.scalars(select(Profile).where(Profile.person_id == person.id, Profile.project_id == pid)):
            for act in db.scalars(
                select(Activity).where(Activity.profile_id == profile.id).order_by(Activity.created_at.desc()).limit(50)
            ):
                items.append({"type": "activity", "source": "voronka", **encode(act)})
            for deal in db.scalars(select(Deal).where(Deal.profile_id == profile.id)):
                items.append({"type": "deal", "source": "voronka", **encode(deal)})
            for order in db.scalars(select(Order).where(Order.profile_id == profile.id)):
                items.append({"type": "order", "source": "voronka", **encode(order)})
                for pay in db.scalars(select(Payment).where(Payment.order_id == order.id)):
                    items.append({"type": "payment", "source": "voronka", **encode(pay)})
            for ev in db.scalars(
                select(Event)
                .where(Event.contact_id == profile.contact_id)
                .order_by(Event.created_at.desc())
                .limit(50)
            ):
                items.append(
                    {
                        "type": "event",
                        "source": "voronka",
                        "id": ev.id,
                        "kind": ev.kind,
                        "payload": ev.payload,
                        "occurred_at": ev.created_at,
                    }
                )
        for agreement in db.scalars(
            select(Agreement).where(Agreement.person_id == person.id, Agreement.project_id == pid)
        ):
            items.append({"type": "agreement", "source": "voronka", **encode(agreement)})
        for journey in db.scalars(
            select(Journey).where(Journey.person_id == person.id, Journey.project_id == pid)
        ):
            items.append({"type": "journey", "source": "voronka", **encode(journey)})
        for comm in db.scalars(
            select(Communication).where(Communication.person_id == person.id).limit(100)
        ):
            items.append({"type": "communication", "source": "voronka", **encode(comm)})

        def sort_key(item):
            ts = item.get("occurred_at") or item.get("created_at") or item.get("signed_at")
            return ts or datetime.min.replace(tzinfo=None)

        items.sort(key=sort_key, reverse=True)
        return {"person_id": person.id, "project_id": pid, "items": items[:limit]}


# --- CRM-003 consent ---


class ConsentBody(Body):
    purpose: Literal["marketing", "transactional", "service", "analytics"] = "marketing"
    status: Literal["granted", "denied", "revoked", "unknown"]
    evidence: str = Field(default="", max_length=500)
    lawful_basis: str = Field(default="consent", max_length=40)


@router.post("/contact-points/{contact_point_id}/consents")
def upsert_consent(contact_point_id: str, body: ConsentBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        cp = must(db.get(ContactPoint, contact_point_id))
        person = _person_acl(db, admin, cp.person_id)
        row = graph.set_consent(
            db,
            contact_point_id,
            purpose=body.purpose,
            status=body.status,
            evidence=body.evidence,
            lawful_basis=body.lawful_basis,
        )
        if body.status in {"denied", "revoked"} and body.purpose == "marketing":
            graph.pause_journeys_on_opt_out(db, person.id)
            # also mark project profiles stopped for marketing suppressions
            for profile in db.scalars(select(Profile).where(Profile.person_id == person.id)):
                profile.stopped = True
        audit(db, admin, "consent.set", consent_id=row.id, status=body.status)
        return encode(row)


@router.get("/contact-points/{contact_point_id}/consents")
def list_consents(contact_point_id: str, admin=Depends(crm_actor)):
    with Session() as db:
        cp = must(db.get(ContactPoint, contact_point_id))
        _person_acl(db, admin, cp.person_id)
        return [encode(c) for c in db.scalars(select(Consent).where(Consent.contact_point_id == contact_point_id))]


class SendPolicyCheckBody(Body):
    """CRM-102 / MAIL-101 send-time check: contact+channel+purpose → allow/deny."""

    channel: str = Field(min_length=1, max_length=30)
    purpose: Literal["marketing", "transactional", "service", "analytics"] = "marketing"
    person_id: str | None = None
    contact_point_id: str | None = None
    connection_id: str | None = None
    missing_policy: Literal["soft_allow", "deny"] = "deny"


@router.post("/projects/{pid}/send-policy/check")
def check_send_policy(pid: str, body: SendPolicyCheckBody, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        if body.person_id:
            _person_acl(db, admin, body.person_id, pid)
        elif body.contact_point_id:
            cp = must(db.get(ContactPoint, body.contact_point_id))
            _person_acl(db, admin, cp.person_id, pid)
        else:
            raise HTTPException(422, "person_id или contact_point_id обязателен")
        # transactional mail is never blocked by marketing opt-out alone
        missing = "soft_allow" if body.purpose == "transactional" else body.missing_policy
        allowed, reason = graph.send_allowed(
            db,
            person_id=body.person_id,
            contact_point_id=body.contact_point_id,
            channel=body.channel,
            purpose=body.purpose,
            connection_id=body.connection_id,
            missing_policy=missing,
        )
        return {
            "allowed": allowed,
            "reason": reason,
            "channel": body.channel,
            "purpose": body.purpose,
            "project_id": pid,
            "sor": "voronka",
        }


# --- CRM-006 interactions / delivery ---


class InteractionBody(Body):
    kind: str = Field(min_length=1, max_length=60)
    direction: Literal["inbound", "outbound", "system"] = "inbound"
    channel: str = Field(default="", max_length=30)
    contact_point_id: str | None = None
    communication_id: str | None = None
    source_service: str = Field(default="voronka", max_length=40)
    external_event_key: str | None = Field(default=None, max_length=160)
    payload: dict = Field(default_factory=dict)
    evidence_status: str = Field(default="recorded", max_length=30)
    occurred_at: datetime | None = None


@router.post("/projects/{pid}/persons/{person_id}/interactions")
def add_interaction(pid: str, person_id: str, body: InteractionBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        person = _person_acl(db, admin, person_id, pid)
        row, dup = graph.record_interaction(
            db,
            person_id=person.id,
            project_id=pid,
            **body.model_dump(),
        )
        return {**encode(row), "duplicate": dup}


class DeliveryBody(Body):
    normalized_status: Literal["queued", "accepted", "delivered", "failed", "read", "unknown"]
    platform_status: str = Field(default="", max_length=80)
    evidence_ref: str = Field(default="", max_length=200)
    external_event_key: str | None = Field(default=None, max_length=160)
    payload: dict = Field(default_factory=dict)


@router.post("/communications/{communication_id}/delivery-events")
def add_delivery(communication_id: str, body: DeliveryBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        comm = must(db.get(Communication, communication_id))
        _person_acl(db, admin, comm.person_id, comm.project_id)
        row, dup = graph.record_delivery_event(db, communication_id, **body.model_dump())
        return {**encode(row), "duplicate": dup}


# --- CRM-007 journeys ---


class JourneyBody(Body):
    person_id: str
    title: str = Field(default="Прогрев", max_length=200)
    purpose: str = Field(default="warmup", max_length=40)
    campaign_id: str | None = None
    steps: list[dict] = Field(default_factory=list, max_length=50)


@router.post("/projects/{pid}/journeys")
def add_journey(pid: str, body: JourneyBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        person = _person_acl(db, admin, body.person_id, pid)
        journey = graph.create_journey(
            db,
            person_id=person.id,
            project_id=pid,
            title=body.title,
            purpose=body.purpose,
            campaign_id=body.campaign_id,
            steps=body.steps,
        )
        audit(db, admin, "journey.created", journey_id=journey.id)
        steps = list(db.scalars(select(JourneyStep).where(JourneyStep.journey_id == journey.id)))
        return {**encode(journey), "steps": [encode(s) for s in steps]}


@router.get("/projects/{pid}/journeys/{journey_id}")
def get_journey(pid: str, journey_id: str, admin=Depends(crm_actor)):
    with Session() as db:
        journey = must(db.get(Journey, journey_id))
        if journey.project_id != pid:
            raise HTTPException(404, "Journey не найден")
        _person_acl(db, admin, journey.person_id, pid)
        steps = list(
            db.scalars(select(JourneyStep).where(JourneyStep.journey_id == journey.id).order_by(JourneyStep.step_no))
        )
        return {**encode(journey), "steps": [encode(s) for s in steps]}


class JourneyOverride(Body):
    status: Literal["active", "paused", "completed", "cancelled", "opted_out"]
    notes: str = Field(default="", max_length=500)


@router.post("/projects/{pid}/journeys/{journey_id}/override")
def override_journey(pid: str, journey_id: str, body: JourneyOverride, admin=Depends(crm_actor)):
    with Session.begin() as db:
        journey = must(db.get(Journey, journey_id))
        if journey.project_id != pid:
            raise HTTPException(404, "Journey не найден")
        _person_acl(db, admin, journey.person_id, pid)
        journey.status = body.status
        journey.updated_at = now()
        audit(db, admin, "journey.override", journey_id=journey.id, status=body.status)
        return encode(journey)


# --- CRM-008 agreements ---


class AgreementBody(Body):
    person_id: str
    title: str = Field(min_length=1, max_length=250)
    deal_id: str | None = None
    legal_status: Literal["draft", "sent", "accepted", "signed", "void", "expired"] = "draft"
    evidence: str = Field(default="", max_length=2000)


@router.post("/projects/{pid}/agreements")
def add_agreement(pid: str, body: AgreementBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        person = _person_acl(db, admin, body.person_id, pid)
        if body.deal_id:
            deal = must(db.get(Deal, body.deal_id))
            if deal.project_id != pid:
                raise HTTPException(422, "Сделка другого проекта")
        row = graph.create_agreement(
            db,
            person_id=person.id,
            project_id=pid,
            title=body.title,
            deal_id=body.deal_id,
            legal_status=body.legal_status,
            evidence=body.evidence,
        )
        audit(db, admin, "agreement.created", agreement_id=row.id)
        return encode(row)


class AgreementSignBody(Body):
    evidence: str = Field(min_length=5, max_length=2000)
    offer_acceptance: bool = True


@router.post("/agreements/{agreement_id}/sign")
def sign_agreement(agreement_id: str, body: AgreementSignBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.get(Agreement, agreement_id))
        _person_acl(db, admin, row.person_id, row.project_id)
        row.legal_status = "signed"
        row.offer_acceptance = body.offer_acceptance
        row.signed_at = now()
        row.evidence = body.evidence
        row.updated_at = now()
        audit(db, admin, "agreement.signed", agreement_id=row.id)
        return encode(row)


@router.get("/projects/{pid}/agreements")
def list_agreements(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(db.scalars(select(Agreement).where(Agreement.project_id == pid)))
        return [encode(r) for r in rows]


# --- PAY-002 ledger ---


@router.get("/orders/{oid}/ledger")
def order_ledger(oid: str, admin=Depends(crm_actor)):
    with Session() as db:
        order = must(db.get(Order, oid))
        project_access(db, admin, order.project_id)
        get_profile(db, admin, order.profile_id)
        return ledger.ledger_summary(db, order)


# --- AUTH-004 OIDC subject links (bridge; full OIDC login waits AUTH-002) ---


class OidcLinkBody(Body):
    issuer: str = Field(min_length=3, max_length=250)
    subject: str = Field(min_length=1, max_length=250)
    account_id: str | None = None
    contact_id: str | None = None
    person_id: str | None = None
    profile_id: str | None = None
    email_hint: str = Field(default="", max_length=250)


@router.post("/oidc-links")
def link_oidc(body: OidcLinkBody, admin=Depends(crm_actor)):
    """Map issuer+sub → Account/Contact/Profile. Email hint never auto-binds or elevates ACL."""
    staff_owner(admin)
    if not any([body.account_id, body.contact_id, body.person_id, body.profile_id]):
        raise HTTPException(422, "Укажите account_id, contact_id, person_id или profile_id")
    with Session.begin() as db:
        prior = db.scalar(
            select(OidcLink).where(OidcLink.issuer == body.issuer, OidcLink.subject == body.subject)
        )
        if prior:
            if prior.provisioning_status == "revoked":
                prior.provisioning_status = "linked"
            # refuse silent rebinding to a different principal
            targets = {
                "account_id": body.account_id,
                "contact_id": body.contact_id,
                "person_id": body.person_id,
                "profile_id": body.profile_id,
            }
            for key, value in targets.items():
                if value and getattr(prior, key) and getattr(prior, key) != value:
                    prior.provisioning_status = "conflict"
                    raise HTTPException(409, "OIDC subject уже привязан к другому объекту")
                if value:
                    setattr(prior, key, value)
            prior.email_hint = body.email_hint or prior.email_hint
            prior.updated_at = now()
            audit(db, admin, "oidc.linked", oidc_link_id=prior.id)
            return encode(prior)
        # Explicit link only — do NOT match by email_hint alone
        row = OidcLink(
            id=uid(),
            issuer=body.issuer,
            subject=body.subject,
            account_id=body.account_id,
            contact_id=body.contact_id,
            person_id=body.person_id,
            profile_id=body.profile_id,
            provisioning_status="linked",
            email_hint=body.email_hint,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        db.flush()
        audit(db, admin, "oidc.linked", oidc_link_id=row.id)
        return encode(row)


@router.get("/oidc-links")
def resolve_oidc(issuer: str, subject: str, admin=Depends(crm_actor)):
    with Session() as db:
        row = db.scalar(select(OidcLink).where(OidcLink.issuer == issuer, OidcLink.subject == subject))
        if row is None or row.provisioning_status == "revoked":
            raise HTTPException(404, "OIDC link не найден")
        return encode(row)


@router.post("/oidc-links/{link_id}/revoke")
def revoke_oidc(link_id: str, admin=Depends(crm_actor)):
    """Revoke issuer+sub mapping. Does not delete Account/Admin or change roles."""
    staff_owner(admin)
    with Session.begin() as db:
        row = must(db.get(OidcLink, link_id))
        if row.provisioning_status == "revoked":
            return encode(row)
        row.provisioning_status = "revoked"
        row.updated_at = now()
        audit(db, admin, "oidc.revoked", oidc_link_id=row.id, account_id=row.account_id)
        if row.account_id:
            account = db.get(Account, row.account_id)
            if account:
                auth.revoke_sessions(account.admin_id)
        return encode(row)
