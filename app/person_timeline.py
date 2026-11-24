"""VR-452 Person Timeline: persist + project staff-safe history rows."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import select, func, or_

from app.crm_models import (
    AccessGrant,
    Activity,
    Communication,
    Consent,
    ContactPoint,
    Interaction,
    Order,
    Payment,
    PersonTimelineEvent,
    Profile,
)
from app.crm_service import uid
from app.db import Event, Identity, Outbox, now

Category = Literal["correspondence", "technical", "all"]

# Interaction / event kinds → timeline category.
_CORRESPONDENCE_PREFIXES = (
    "message.",
    "email.",
    "mail.",
    "comm.",
    "staff.",
    "note.",
    "touch",
    "lead.channel_invite",
    "course_channel_invite",
    "signal.",
)
_TECHNICAL_PREFIXES = (
    "deal.paid",
    "payment.",
    "access.",
    "course.access",
    "course.enter",
    "module.open",
    "consent.",
    "outbox.",
    "learning.",
    "login.",
    "enrollment.",
    "site.visit",
)


def classify_kind(kind: str, *, channel: str = "", direction: str = "") -> str:
    k = (kind or "").lower()
    ch = (channel or "").lower()
    if any(k.startswith(p) or k == p.rstrip(".") for p in _TECHNICAL_PREFIXES):
        return "technical"
    if ch in {"payment", "system", "lms", "lms_end"} and k not in {"", "note", "manual"}:
        if k.startswith("message") or ch in {"email", "telegram", "max"}:
            pass
        elif any(k.startswith(p) or p in k for p in ("pay", "access", "consent", "outbox", "learning", "grant")):
            return "technical"
    if any(k.startswith(p) or k == p.rstrip(".") for p in _CORRESPONDENCE_PREFIXES):
        return "correspondence"
    if ch in {"email", "telegram", "max", "vk", "phone", "meeting", "manual"} or direction in {
        "inbound",
        "outbound",
    }:
        return "correspondence"
    if "consent" in k or "payment" in k or "grant" in k or "outbox" in k:
        return "technical"
    return "correspondence"


def _ru_title(kind: str, *, channel: str = "", payload: dict | None = None) -> str:
    payload = payload or {}
    k = kind or ""
    mapping = {
        "deal.paid": "Оплата",
        "course.access_granted": "Доступ открыт",
        "access.granted": "Доступ открыт",
        "access.revoked": "Доступ отозван",
        "consent.changed": "Согласие изменено",
        "consent.set": "Согласие изменено",
        "lead.channel_invite.queued": "Приглашение в канал (очередь)",
        "lead.channel_invite.pending": "Приглашение в канал (ожидает /start)",
        "outbox.failed": "Сбой доставки",
        "message.in": "Входящее сообщение",
        "message.out": "Исходящее сообщение",
        "staff.note": "Заметка сотрудника",
        "course.enter": "Вход в курс",
        "module.open": "Открытие модуля",
        "learning.progress": "Прогресс по курсу",
        "learning.completed": "Курс завершён",
    }
    if k in mapping:
        title = mapping[k]
        if k == "deal.paid" and payload.get("sandbox"):
            return "Оплата · sandbox"
        return title
    if k.startswith("email.") or channel == "email":
        return "Письмо"
    if channel in {"telegram", "max"}:
        return f"Сообщение · {channel}"
    return k or "Событие"


def _safe_ref(payload: dict | None) -> dict:
    """Drop secrets / large bodies — keep ids and short evidence only."""
    if not payload:
        return {}
    keep = {
        "payment_id",
        "order_id",
        "grant_id",
        "consent_id",
        "outbox_id",
        "communication_id",
        "interaction_id",
        "activity_id",
        "event_id",
        "profile_id",
        "resource",
        "capability",
        "purpose",
        "status",
        "amount_minor",
        "sandbox",
        "provider",
        "course_id",
        "module_id",
        "stage",
        "stage_label",
        "project_id",
        "branch_id",
        "lead_event_id",
        "error",
    }
    out = {k: payload[k] for k in keep if k in payload and payload[k] is not None}
    if "evidence" in payload and isinstance(payload["evidence"], str):
        out["evidence"] = payload["evidence"][:200]
    if "body_preview" in payload and isinstance(payload["body_preview"], str):
        out["body_preview"] = payload["body_preview"][:200]
    return out


def record_timeline_event(
    db,
    *,
    person_id: str,
    kind: str,
    external_key: str,
    source: str = "voronka",
    channel: str = "system",
    category: str | None = None,
    title: str = "",
    summary: str = "",
    project_id: str | None = None,
    branch_id: str | None = None,
    payload_ref: dict | None = None,
    occurred_at: datetime | None = None,
) -> tuple[PersonTimelineEvent, bool]:
    """Idempotent upsert by (source, external_key)."""
    prior = db.scalar(
        select(PersonTimelineEvent).where(
            PersonTimelineEvent.source == source,
            PersonTimelineEvent.external_key == external_key,
        )
    )
    if prior:
        return prior, True
    cat = category or classify_kind(kind, channel=channel)
    if cat not in {"correspondence", "technical"}:
        cat = "correspondence"
    row = PersonTimelineEvent(
        id=uid(),
        person_id=person_id,
        project_id=project_id,
        branch_id=branch_id,
        occurred_at=occurred_at or now(),
        source=source[:40],
        channel=(channel or "system")[:30],
        category=cat,
        kind=kind[:80],
        title=(title or _ru_title(kind, channel=channel, payload=payload_ref))[:300],
        summary=(summary or "")[:800],
        payload_ref=_safe_ref(payload_ref),
        external_key=external_key[:200],
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row, False


def _profile_branch(db, person_id: str, project_id: str | None) -> str | None:
    if not project_id:
        return None
    profile = db.scalar(
        select(Profile).where(Profile.person_id == person_id, Profile.project_id == project_id).limit(1)
    )
    return profile.branch_id if profile else None


def project_from_interaction(db, ix: Interaction) -> tuple[PersonTimelineEvent, bool] | None:
    key = ix.external_event_key or f"interaction:{ix.id}"
    source = ix.source_service or "voronka"
    payload = dict(ix.payload or {})
    payload["interaction_id"] = ix.id
    if ix.communication_id:
        payload["communication_id"] = ix.communication_id
    branch = _profile_branch(db, ix.person_id, ix.project_id)
    summary = ""
    if isinstance(payload.get("evidence"), str):
        summary = payload["evidence"][:800]
    elif isinstance(payload.get("body_preview"), str):
        summary = payload["body_preview"][:800]
    return record_timeline_event(
        db,
        person_id=ix.person_id,
        kind=ix.kind,
        external_key=key,
        source=source,
        channel=ix.channel or "system",
        category=classify_kind(ix.kind, channel=ix.channel or "", direction=ix.direction or ""),
        title=_ru_title(ix.kind, channel=ix.channel or "", payload=payload),
        summary=summary,
        project_id=ix.project_id,
        branch_id=branch,
        payload_ref=payload,
        occurred_at=ix.occurred_at or ix.created_at,
    )


def project_person_sources(db, person_id: str, project_id: str | None = None, *, limit_each: int = 200) -> int:
    """Backfill PersonTimelineEvent from Interaction / Consent / Events / Outbox / payments / grants / activities."""
    created = 0

    ix_q = select(Interaction).where(Interaction.person_id == person_id).order_by(Interaction.occurred_at.desc()).limit(
        limit_each
    )
    if project_id:
        ix_q = (
            select(Interaction)
            .where(
                Interaction.person_id == person_id,
                or_(Interaction.project_id == project_id, Interaction.project_id.is_(None)),
            )
            .order_by(Interaction.occurred_at.desc())
            .limit(limit_each)
        )
    for ix in db.scalars(ix_q):
        _, dup = project_from_interaction(db, ix) or (None, True)
        if not dup:
            created += 1

    linked_comm_ids = {
        ix.communication_id
        for ix in db.scalars(
            select(Interaction).where(
                Interaction.person_id == person_id,
                Interaction.communication_id.is_not(None),
            )
        )
    }
    for comm in db.scalars(
        select(Communication)
        .where(Communication.person_id == person_id)
        .order_by(Communication.created_at.desc())
        .limit(limit_each)
    ):
        if project_id and comm.project_id and comm.project_id != project_id:
            continue
        if comm.id in linked_comm_ids:
            continue  # already covered via Interaction projection
        kind = "message.out" if (comm.direction or "outbound") == "outbound" else "message.in"
        _, dup = record_timeline_event(
            db,
            person_id=person_id,
            kind=kind,
            external_key=f"communication:{comm.id}",
            source="signal" if (comm.channel or "") in {"email", "telegram"} else "voronka",
            channel=comm.channel or "system",
            category="correspondence",
            title=_ru_title(kind, channel=comm.channel or ""),
            summary=(comm.body_preview or "")[:800],
            project_id=comm.project_id or project_id,
            branch_id=_profile_branch(db, person_id, comm.project_id or project_id),
            payload_ref={"communication_id": comm.id, "status": comm.status, "body_preview": comm.body_preview},
            occurred_at=comm.created_at,
        )
        if not dup:
            created += 1

    profiles = list(db.scalars(select(Profile).where(Profile.person_id == person_id)))
    if project_id:
        profiles = [p for p in profiles if p.project_id == project_id]
    contact_ids = [p.contact_id for p in profiles if p.contact_id]

    for profile in profiles:
        for act in db.scalars(
            select(Activity).where(Activity.profile_id == profile.id).order_by(Activity.created_at.desc()).limit(100)
        ):
            kind = "staff.note" if act.kind in {"note", "manual", ""} else (act.kind or "staff.note")
            if act.channel == "lms_end" or (act.outcome or "").startswith("learning."):
                kind = act.outcome or "learning.progress"
                cat = "technical"
            else:
                cat = "correspondence"
            _, dup = record_timeline_event(
                db,
                person_id=person_id,
                kind=kind,
                external_key=f"activity:{act.id}",
                source="staff",
                channel=act.channel or "manual",
                category=cat,
                title=_ru_title(kind, channel=act.channel or "") if cat == "technical" else (act.outcome or "Заметка сотрудника"),
                summary=(act.body or "")[:800],
                project_id=profile.project_id,
                branch_id=profile.branch_id,
                payload_ref={"activity_id": act.id, "outcome": act.outcome},
                occurred_at=act.created_at,
            )
            if not dup:
                created += 1

        for order in db.scalars(select(Order).where(Order.profile_id == profile.id)):
            for pay in db.scalars(select(Payment).where(Payment.order_id == order.id)):
                # Align with integrations._record_payment_interaction external_event_key.
                sandbox = pay.provider == "manual" or "sandbox" in (pay.evidence or "").lower()
                _, dup = record_timeline_event(
                    db,
                    person_id=person_id,
                    kind="deal.paid" if pay.kind == "payment" else f"payment.{pay.kind}",
                    external_key=f"deal.paid:{pay.id}",
                    source="voronka",
                    channel="payment",
                    category="technical",
                    title="Оплата · sandbox" if sandbox else "Оплата",
                    summary=(pay.evidence or "")[:800],
                    project_id=order.project_id,
                    branch_id=profile.branch_id,
                    payload_ref={
                        "payment_id": pay.id,
                        "order_id": order.id,
                        "amount_minor": pay.amount_minor,
                        "provider": pay.provider,
                        "sandbox": sandbox,
                    },
                    occurred_at=pay.created_at,
                )
                if not dup:
                    created += 1
            for grant in db.scalars(select(AccessGrant).where(AccessGrant.order_id == order.id)):
                # Align with integrations.signal_access_granted_event_id / Interaction key.
                grant_key = f"course_access_granted:{grant.id}"
                _, dup = record_timeline_event(
                    db,
                    person_id=person_id,
                    kind="access.granted",
                    external_key=grant_key,
                    source="voronka",
                    channel="system",
                    category="technical",
                    title="Доступ открыт",
                    summary=f"{grant.resource} · {grant.capability}",
                    project_id=order.project_id,
                    branch_id=profile.branch_id,
                    payload_ref={
                        "grant_id": grant.id,
                        "order_id": order.id,
                        "resource": grant.resource,
                        "capability": grant.capability,
                    },
                    occurred_at=order.created_at,
                )
                if not dup:
                    created += 1

    # Consent changes via contact points of this person.
    cps = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == person_id)))
    for cp in cps:
        for consent in db.scalars(select(Consent).where(Consent.contact_point_id == cp.id)):
            _, dup = record_timeline_event(
                db,
                person_id=person_id,
                kind="consent.changed",
                external_key=f"consent:{consent.id}:{consent.status}:{int(consent.effective_at.timestamp()) if consent.effective_at else 0}",
                source="voronka",
                channel=cp.channel or "system",
                category="technical",
                title="Согласие изменено",
                summary=f"{consent.purpose}: {consent.status}",
                project_id=project_id,
                branch_id=_profile_branch(db, person_id, project_id),
                payload_ref={
                    "consent_id": consent.id,
                    "purpose": consent.purpose,
                    "status": consent.status,
                },
                occurred_at=consent.effective_at or consent.created_at,
            )
            if not dup:
                created += 1

    if contact_ids:
        for ev in db.scalars(
            select(Event)
            .where(Event.contact_id.in_(contact_ids))
            .order_by(Event.created_at.desc())
            .limit(limit_each)
        ):
            kind = ev.kind or "event"
            cat = (
                "correspondence"
                if "channel_invite" in kind or kind.startswith("message")
                else "technical"
                if any(x in kind for x in ("consent", "payment", "access", "outbox", "login"))
                else classify_kind(kind)
            )
            # Prefer invite / system events; skip noisy funnel ticks unless invite-related.
            if kind.startswith("lead.channel_invite") or kind.startswith("outbox.") or "consent" in kind:
                pass
            elif not kind.startswith(("message", "email", "payment", "access", "consent", "learning", "module", "course")):
                continue
            profile = next((p for p in profiles if p.contact_id == ev.contact_id), None)
            _, dup = record_timeline_event(
                db,
                person_id=person_id,
                kind=kind,
                external_key=f"event:{ev.id}",
                source="voronka",
                channel="telegram" if "channel_invite" in kind else "system",
                category=cat,
                title=_ru_title(kind),
                summary="",
                project_id=(profile.project_id if profile else project_id),
                branch_id=(profile.branch_id if profile else None),
                payload_ref={"event_id": ev.id, **_safe_ref(ev.payload if isinstance(ev.payload, dict) else {})},
                occurred_at=ev.created_at,
            )
            if not dup:
                created += 1

        # Failed Outbox rows linked via Identity.
        identities = list(db.scalars(select(Identity).where(Identity.contact_id.in_(contact_ids))))
        id_map = {i.id: i for i in identities}
        if identities:
            for out in db.scalars(
                select(Outbox)
                .where(
                    Outbox.identity_id.in_([i.id for i in identities]),
                    Outbox.status.in_(["failed", "unknown"]),
                )
                .order_by(Outbox.updated_at.desc())
                .limit(100)
            ):
                ident = id_map.get(out.identity_id) if out.identity_id else None
                profile = next((p for p in profiles if ident and p.contact_id == ident.contact_id), None)
                _, dup = record_timeline_event(
                    db,
                    person_id=person_id,
                    kind="outbox.failed",
                    external_key=f"outbox.failed:{out.id}",
                    source="voronka",
                    channel="system",
                    category="technical",
                    title="Сбой доставки",
                    summary=(out.error or out.status or "")[:800],
                    project_id=(profile.project_id if profile else project_id),
                    branch_id=(profile.branch_id if profile else None),
                    payload_ref={"outbox_id": out.id, "status": out.status, "error": out.error},
                    occurred_at=out.updated_at or out.created_at,
                )
                if not dup:
                    created += 1

    return created


def list_timeline(
    db,
    *,
    person_id: str,
    project_id: str | None = None,
    category: Category = "all",
    offset: int = 0,
    limit: int = 20,
    since: datetime | None = None,
    until: datetime | None = None,
    sync: bool = True,
) -> dict:
    if sync:
        project_person_sources(db, person_id, project_id)

    q = select(PersonTimelineEvent).where(PersonTimelineEvent.person_id == person_id)
    count_q = select(func.count()).select_from(PersonTimelineEvent).where(PersonTimelineEvent.person_id == person_id)
    if project_id:
        scope = or_(PersonTimelineEvent.project_id == project_id, PersonTimelineEvent.project_id.is_(None))
        q = q.where(scope)
        count_q = count_q.where(scope)
    if category in {"correspondence", "technical"}:
        q = q.where(PersonTimelineEvent.category == category)
        count_q = count_q.where(PersonTimelineEvent.category == category)
    if since:
        q = q.where(PersonTimelineEvent.occurred_at >= since)
        count_q = count_q.where(PersonTimelineEvent.occurred_at >= since)
    if until:
        q = q.where(PersonTimelineEvent.occurred_at <= until)
        count_q = count_q.where(PersonTimelineEvent.occurred_at <= until)

    total = int(db.scalar(count_q) or 0)
    rows = list(
        db.scalars(q.order_by(PersonTimelineEvent.occurred_at.desc()).offset(max(0, offset)).limit(max(1, min(limit, 100))))
    )

    # Resolve project/branch labels for staff UI.
    from app.db import Project
    from app.crm_models import Branch

    project_ids = {r.project_id for r in rows if r.project_id}
    branch_ids = {r.branch_id for r in rows if r.branch_id}
    projects = {
        p.id: p.name for p in db.scalars(select(Project).where(Project.id.in_(project_ids or ["__none__"])))
    }
    branches = {
        b.id: b.name for b in db.scalars(select(Branch).where(Branch.id.in_(branch_ids or ["__none__"])))
    }

    items = []
    for r in rows:
        items.append(
            {
                "id": r.id,
                "person_id": r.person_id,
                "project_id": r.project_id,
                "project_name": projects.get(r.project_id) if r.project_id else None,
                "branch_id": r.branch_id,
                "branch_name": branches.get(r.branch_id) if r.branch_id else None,
                "occurred_at": r.occurred_at,
                "source": r.source,
                "channel": r.channel,
                "category": r.category,
                "kind": r.kind,
                "title": r.title,
                "summary": r.summary,
                "payload_ref": r.payload_ref or {},
            }
        )
    return {
        "person_id": person_id,
        "project_id": project_id,
        "category": category,
        "offset": offset,
        "limit": limit,
        "total": total,
        "items": items,
    }
