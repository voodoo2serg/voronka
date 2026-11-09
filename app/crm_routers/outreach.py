"""Warm-up outreach batches: dry_run invites via Entry deep links, not Campaign/Outbox."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must
from app.crm_models import Activity, OutreachBatch, OutreachRecipient, Profile
from app.crm_service import (
    uid, project_access, get_profile, audit, assigned_profile_filter, manager_scope,
)
from app.db import Connection, Entry, Event, Funnel, now
from app.outreach import (
    bot_entry_link, render_invite, project_outreach_used_today, eligibility,
)
from app.outreach_transport import get_transport

router = APIRouter()


class OutreachBatchBody(Body):
    title: str = Field(min_length=1, max_length=200)
    invite_text: str = Field(min_length=1, max_length=4000)
    profile_ids: list[str] = Field(min_length=1, max_length=500)
    entry_token: str | None = None
    funnel_id: str | None = None
    entry_connection_id: str
    daily_cap: int = Field(default=30, ge=1, le=50)
    mode: Literal["dry_run", "live"] = "dry_run"
    transport: Literal["manual", "pymax"] = "manual"


class OutcomeBody(Body):
    state: Literal["invited", "started", "skipped", "failed", "stopped"]
    note: str = Field(default="", max_length=2000)


class OutreachControl(Body):
    action: Literal["pause", "resume", "cancel"]


def _resolve_entry(db, admin, project_id, body: OutreachBatchBody) -> Entry:
    connection = must(db.get(Connection, body.entry_connection_id))
    if connection.project_id != project_id:
        raise HTTPException(404, "Подключение недоступно проекту")
    if connection.platform not in {"telegram", "max"}:
        raise HTTPException(422, "Прогрев ведёт только на официальный Telegram или MAX бот")
    if connection.role and connection.role not in {"bot", ""}:
        # Outreach kind connections must not be Entry targets for official Start.
        if connection.role == "outreach":
            raise HTTPException(422, "Точка входа должна быть официальным ботом, не outreach-аккаунтом")
    if body.entry_token:
        entry = must(db.get(Entry, body.entry_token))
        if entry.connection_id != connection.id or not entry.active:
            raise HTTPException(409, "Entry не принадлежит выбранному боту или неактивен")
        funnel = must(db.get(Funnel, entry.funnel_id))
        if funnel.project_id and funnel.project_id != project_id:
            raise HTTPException(404, "Воронка другого проекта")
        return entry
    if not body.funnel_id:
        raise HTTPException(422, "Укажите entry_token или funnel_id")
    funnel = must(db.get(Funnel, body.funnel_id))
    if funnel.project_id and funnel.project_id != project_id:
        raise HTTPException(404, "Воронка другого проекта")
    if connection.id not in funnel.connection_ids or not funnel.published_version_id:
        raise HTTPException(409, "Нужно опубликовать воронку и выбрать подключение бота")
    import secrets
    token = secrets.token_urlsafe(18)
    entry = Entry(
        token=token,
        funnel_id=funnel.id,
        connection_id=connection.id,
        attribution={"source": "outreach", "project_id": project_id},
        active=True,
    )
    db.add(entry)
    db.flush()
    return entry


def _batch_payload(db, batch: OutreachBatch) -> dict:
    recipients = list(db.scalars(select(OutreachRecipient).where(OutreachRecipient.batch_id == batch.id)))
    states: dict[str, int] = {}
    for row in recipients:
        states[row.state] = states.get(row.state, 0) + 1
    return {**encode(batch), "recipient_states": states, "recipients": [encode(r) for r in recipients]}


@router.post("/projects/{pid}/outreach-batches")
def create_batch(pid: str, body: OutreachBatchBody, admin=Depends(crm_actor)):
    if body.mode == "live" and body.transport == "pymax":
        # Live unofficial send is opt-in and still blocked unless explicitly prepared later.
        pass
    with Session.begin() as db:
        project_access(db, admin, pid)
        entry = _resolve_entry(db, admin, pid, body)
        connection = must(db.get(Connection, body.entry_connection_id))
        preview = []
        recipients = []
        for profile_id in set(body.profile_ids):
            try:
                profile = get_profile(db, admin, profile_id)
            except HTTPException:
                preview.append({"profile_id": profile_id, "eligible": False, "reason": "Клиент недоступен"})
                continue
            if profile.project_id != pid:
                raise HTTPException(404, "Клиент другого проекта")
            ok, reason = eligibility(profile)
            preview.append({"profile_id": profile_id, "eligible": ok, "reason": reason, "phone": profile.phone})
            recipients.append(profile)
        batch = OutreachBatch(
            id=uid(),
            project_id=pid,
            title=body.title,
            invite_text=body.invite_text,
            entry_token=entry.token,
            entry_connection_id=connection.id,
            daily_cap=body.daily_cap,
            mode=body.mode,
            transport=body.transport,
            state="draft",
            report={"preview": preview},
            actor_id=admin.telegram_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(batch)
        db.flush()
        for profile in recipients:
            db.add(
                OutreachRecipient(
                    id=uid(),
                    batch_id=batch.id,
                    profile_id=profile.id,
                    phone=profile.phone or "",
                    state="pending",
                    entry_token=entry.token,
                )
            )
        audit(db, admin, "outreach.created", batch_id=batch.id, recipients=len(recipients), mode=body.mode)
        db.flush()
        return _batch_payload(db, batch)


@router.get("/projects/{pid}/outreach-batches")
def list_batches(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        result = []
        for batch in db.scalars(select(OutreachBatch).where(OutreachBatch.project_id == pid).order_by(OutreachBatch.created_at.desc())):
            states: dict[str, int] = {}
            for status in db.scalars(select(OutreachRecipient.state).where(OutreachRecipient.batch_id == batch.id)):
                states[status] = states.get(status, 0) + 1
            result.append({**encode(batch), "recipient_states": states})
        return result


@router.get("/outreach-batches/{bid}")
def get_batch(bid: str, admin=Depends(crm_actor)):
    with Session() as db:
        batch = must(db.get(OutreachBatch, bid))
        project_access(db, admin, batch.project_id)
        payload = _batch_payload(db, batch)
        if manager_scope(admin):
            visible = assigned_profile_filter(admin)
            allowed = {
                p.id
                for p in db.scalars(select(Profile).where(Profile.project_id == batch.project_id, visible))
            }
            payload["recipients"] = [r for r in payload["recipients"] if r["profile_id"] in allowed]
        return payload


@router.post("/outreach-batches/{bid}/prepare")
def prepare_batch(bid: str, admin=Depends(crm_actor)):
    """Dry-run first: generate invite text + official bot links; never enqueue Outbox."""
    with Session.begin() as db:
        batch = must(db.scalar(select(OutreachBatch).where(OutreachBatch.id == bid).with_for_update()))
        project_access(db, admin, batch.project_id)
        if batch.state in {"cancelled", "completed"}:
            raise HTTPException(409, "Партия уже закрыта")
        if batch.state == "paused":
            raise HTTPException(409, "Снимите паузу перед подготовкой")
        entry = must(db.get(Entry, batch.entry_token))
        if not entry.active:
            raise HTTPException(409, "Entry неактивен")
        connection = must(db.get(Connection, batch.entry_connection_id))
        link = bot_entry_link(connection, entry.token)
        used = project_outreach_used_today(db, batch.project_id)
        remaining = max(0, batch.daily_cap - used)
        transport = get_transport(batch.mode, batch.transport)
        prepared, skipped, errors = [], [], []
        recipients = list(
            db.scalars(
                select(OutreachRecipient)
                .where(OutreachRecipient.batch_id == bid, OutreachRecipient.state == "pending")
                .with_for_update()
            )
        )
        for recipient in recipients:
            try:
                profile = get_profile(db, admin, recipient.profile_id)
            except HTTPException:
                recipient.state = "skipped"
                recipient.last_error = "Клиент недоступен"
                skipped.append({"profile_id": recipient.profile_id, "reason": recipient.last_error})
                continue
            ok, reason = eligibility(profile)
            if not ok:
                recipient.state = "stopped" if profile.stopped else "skipped"
                recipient.last_error = reason
                skipped.append({"profile_id": recipient.profile_id, "reason": reason})
                continue
            if len(prepared) >= remaining:
                recipient.state = "skipped"
                recipient.last_error = "Дневной лимит партии"
                skipped.append({"profile_id": recipient.profile_id, "reason": recipient.last_error})
                continue
            text = render_invite(batch.invite_text, name=profile.name, link=link, phone=profile.phone)
            try:
                result = transport.send_invite(profile.phone, text)
            except RuntimeError as exc:
                recipient.state = "failed"
                recipient.last_error = str(exc)
                recipient.attempts += 1
                errors.append({"profile_id": recipient.profile_id, "error": str(exc)})
                continue
            # Live PyMax still must not write Outbox; dry_run never sends.
            if batch.mode == "live" and batch.transport == "pymax" and result.get("sent"):
                recipient.state = "invited"
                recipient.invited_at = now()
            else:
                recipient.state = "prepared"
            recipient.phone = profile.phone
            recipient.invite_text = text
            recipient.bot_link = link
            recipient.entry_token = entry.token
            recipient.prepared_at = now()
            recipient.attempts += 1
            recipient.last_error = ""
            db.add(
                Activity(
                    id=uid(),
                    profile_id=profile.id,
                    actor_id=admin.telegram_id,
                    kind="outreach",
                    channel=connection.platform,
                    outcome="Подготовлено приглашение (dry_run)" if batch.mode == "dry_run" else "Приглашение outreach",
                    body=text,
                )
            )
            db.add(
                Event(
                    id=uid(),
                    connection_id=connection.id,
                    contact_id=profile.contact_id,
                    actor_id=admin.telegram_id,
                    kind="outreach.prepared",
                    payload={
                        "batch_id": batch.id,
                        "recipient_id": recipient.id,
                        "profile_id": profile.id,
                        "phone": profile.phone,
                        "bot_link": link,
                        "mode": batch.mode,
                        "transport": batch.transport,
                        "sent": bool(result.get("sent")),
                    },
                )
            )
            prepared.append({"profile_id": profile.id, "bot_link": link, "sent": bool(result.get("sent"))})
        batch.state = "prepared" if prepared or batch.state == "draft" else batch.state
        batch.updated_at = now()
        batch.report = {
            **(batch.report or {}),
            "prepared": prepared,
            "skipped": skipped,
            "errors": errors,
            "daily_used_before": used,
            "daily_cap": batch.daily_cap,
            "outbox_used": False,
        }
        audit(db, admin, "outreach.prepared", batch_id=batch.id, prepared=len(prepared), skipped=len(skipped))
        return {
            "state": batch.state,
            "prepared": len(prepared),
            "skipped": len(skipped),
            "errors": errors,
            "daily_remaining": max(0, batch.daily_cap - used - len(prepared)),
            "bot_link_example": link,
            "outbox_used": False,
            "mode": batch.mode,
        }


@router.post("/outreach-batches/{bid}/recipients/{rid}/outcome")
def mark_outcome(bid: str, rid: str, body: OutcomeBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        batch = must(db.get(OutreachBatch, bid))
        project_access(db, admin, batch.project_id)
        recipient = must(
            db.scalar(
                select(OutreachRecipient)
                .where(OutreachRecipient.id == rid, OutreachRecipient.batch_id == bid)
                .with_for_update()
            )
        )
        profile = get_profile(db, admin, recipient.profile_id)
        if body.state == "stopped":
            profile.stopped = True
        recipient.state = body.state
        recipient.outcome_note = body.note
        if body.state == "invited":
            recipient.invited_at = recipient.invited_at or now()
        if body.state == "started":
            recipient.started_at = now()
        connection = db.get(Connection, batch.entry_connection_id)
        db.add(
            Activity(
                id=uid(),
                profile_id=profile.id,
                actor_id=admin.telegram_id,
                kind="outreach_outcome",
                channel=connection.platform if connection else "manual",
                outcome=body.state,
                body=body.note or f"Статус прогрева: {body.state}",
            )
        )
        audit(db, admin, "outreach.outcome", batch_id=bid, recipient_id=rid, state=body.state)
        return encode(recipient)


@router.post("/outreach-batches/{bid}/control")
def control_batch(bid: str, body: OutreachControl, admin=Depends(crm_actor)):
    with Session.begin() as db:
        batch = must(db.scalar(select(OutreachBatch).where(OutreachBatch.id == bid).with_for_update()))
        project_access(db, admin, batch.project_id)
        if body.action == "cancel":
            batch.state = "cancelled"
            for recipient in db.scalars(
                select(OutreachRecipient).where(
                    OutreachRecipient.batch_id == bid,
                    OutreachRecipient.state.in_(["pending", "prepared"]),
                )
            ):
                recipient.state = "skipped"
                recipient.last_error = "Партия отменена"
        elif body.action == "pause":
            if batch.state not in {"draft", "prepared"}:
                raise HTTPException(409, "Нельзя поставить на паузу")
            batch.state = "paused"
        else:
            if batch.state != "paused":
                raise HTTPException(409, "Партия не на паузе")
            batch.state = "prepared" if batch.report.get("prepared") else "draft"
        batch.updated_at = now()
        audit(db, admin, "outreach." + body.action, batch_id=bid)
        return {"state": batch.state}
