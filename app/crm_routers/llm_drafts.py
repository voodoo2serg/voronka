"""Inbox approve/edit/reject for llm_reply drafts (PDialog P1). Auto-send stays off."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must
from app.crm_models import LlmReplyAudit, Profile
from app.crm_service import audit, manager_scope, profile_visible, project_access, uid
from app.db import Contact, Identity, Run, Version, now
from app.runtime import advance, event, pause_for_operator, queue

router = APIRouter()

PENDING = {"pending", "ok", "fallback"}
DELIVERY_TEXT_LIMIT = 4000


class DraftDecision(Body):
    text: str | None = Field(default=None, max_length=DELIVERY_TEXT_LIMIT)


def _manager_owns_contact(db, admin, row: LlmReplyAudit) -> None:
    """Managers may only touch drafts for contacts they own (same as inbox/contacts)."""
    if not manager_scope(admin) or not row.project_id:
        return
    profile = db.scalar(
        select(Profile).where(
            Profile.contact_id == row.contact_id,
            Profile.project_id == row.project_id,
        )
    )
    if profile is None or not profile_visible(admin, profile):
        raise HTTPException(404, "Не найдено")


def _get_draft(db, draft_id: str, admin, *, for_update: bool = False) -> LlmReplyAudit:
    query = select(LlmReplyAudit).where(LlmReplyAudit.id == draft_id)
    if for_update:
        query = query.with_for_update()
    row = must(db.scalar(query))
    if row.project_id:
        project_access(db, admin, row.project_id)
    elif admin.role != "owner":
        raise HTTPException(403, "Нет доступа")
    _manager_owns_contact(db, admin, row)
    return row


def _require_delivery_length(text: str) -> str:
    text = text.strip()
    if not text:
        raise HTTPException(422, "Пустой текст")
    if len(text) > DELIVERY_TEXT_LIMIT:
        raise HTTPException(
            422,
            f"Текст длиннее {DELIVERY_TEXT_LIMIT} символов — сократите перед отправкой (без обрезки)",
        )
    return text


@router.get("/projects/{pid}/llm-drafts")
def list_drafts(
    pid: str,
    status: Literal["pending", "all"] = "pending",
    limit: int = 50,
    admin=Depends(crm_actor),
):
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit 1–200")
    with Session() as db:
        project_access(db, admin, pid)
        query = select(LlmReplyAudit).where(LlmReplyAudit.project_id == pid)
        if status == "pending":
            query = query.where(LlmReplyAudit.response_status.in_(list(PENDING)))
        if manager_scope(admin):
            owned = select(Profile.contact_id).where(
                Profile.project_id == pid,
                Profile.owner_id == admin.telegram_id,
            )
            query = query.where(LlmReplyAudit.contact_id.in_(owned))
        rows = list(db.scalars(query.order_by(LlmReplyAudit.created_at.desc()).limit(limit)))
        return {
            "items": [encode(r) for r in rows],
            "auto_send": False,
        }


@router.post("/llm-drafts/{draft_id}/approve")
def approve_draft(draft_id: str, body: DraftDecision, admin=Depends(crm_actor)):
    """Operator approves (optionally edits) draft → queues Outbox send; advances run. Not auto-send."""
    if admin.role not in {"owner", "editor", "manager"}:
        raise HTTPException(403, "Нет права отправки")
    with Session.begin() as db:
        row = _get_draft(db, draft_id, admin, for_update=True)
        if row.response_status not in PENDING:
            raise HTTPException(409, "Черновик уже обработан")
        text = _require_delivery_length(body.text if body.text is not None else row.draft_text or "")
        edited = body.text is not None and body.text.strip() != (row.draft_text or "").strip()
        identity = must(db.get(Identity, row.identity_id))
        run = db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
        # Shared queue helper assigns locked next position (never default 0 ahead of prior run mail).
        out = queue(db, identity, {"type": "text", "text": text}, run=run)
        db.flush()
        row.draft_text = text
        row.response_status = "edited" if edited else "approved"
        row.approved_by = admin.telegram_id
        row.message_id = out.id
        row.updated_at = now()
        if run:
            state = dict(run.state or {})
            graph = db.get(Version, run.version_id).graph
            node = graph["nodes"].get(run.current) or {}
            parked_for_draft = (
                run.status == "waiting"
                and state.get("_await") == "llm_approval"
                and state.get("_llm_draft_id") == row.id
            )
            # Resume only draft-parked waiting runs. Respect operator manual pause / other pauses.
            if parked_for_draft:
                nxt = node.get("next") if node.get("type") == "llm_reply" else state.get("_escalate_next")
                state.pop("_await", None)
                state.pop("_llm_draft_id", None)
                if nxt:
                    run.state, run.current, run.status = state, nxt, "active"
                    advance(db, run, identity)
                else:
                    run.state = state
            elif state.get("_llm_draft_id") == row.id:
                # Clear draft markers without unpausing a human takeover.
                state.pop("_await", None)
                state.pop("_llm_draft_id", None)
                run.state = state
        event(
            db, "llm_reply.approved", identity.connection_id, identity.contact_id, row.run_id,
            actor_id=admin.telegram_id, audit_id=row.id, message_id=out.id, edited=edited,
        )
        audit(db, admin, "llm_draft.approved", draft_id=row.id, message_id=out.id, edited=edited)
        return {**encode(row), "sent": False, "queued": True, "auto_send": False}


@router.post("/llm-drafts/{draft_id}/reject")
def reject_draft(draft_id: str, body: DraftDecision, admin=Depends(crm_actor)):
    """Reject draft: mark audit, escalate/pause for operator (no client send)."""
    with Session.begin() as db:
        row = _get_draft(db, draft_id, admin, for_update=True)
        if row.response_status not in PENDING:
            raise HTTPException(409, "Черновик уже обработан")
        if body.text is not None:
            row.draft_text = _require_delivery_length(body.text)
        row.response_status = "rejected"
        row.approved_by = admin.telegram_id
        row.updated_at = now()
        identity = must(db.get(Identity, row.identity_id))
        run = db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
        contact_row = db.get(Contact, identity.contact_id)
        if run and contact_row:
            state = dict(run.state or {})
            state.pop("_await", None)
            state.pop("_llm_draft_id", None)
            run.state = state
            # Only escalate/pause when still draft-waiting; don't stack pause over manual pause.
            if run.status == "waiting":
                pause_for_operator(
                    db, run, identity, contact_row,
                    reason="llm_rejected",
                    task_title="Черновик ИИ отклонён — нужен ручной ответ",
                )
        event(
            db, "llm_reply.rejected", identity.connection_id, identity.contact_id, row.run_id,
            actor_id=admin.telegram_id, audit_id=row.id,
        )
        audit(db, admin, "llm_draft.rejected", draft_id=row.id)
        return {**encode(row), "auto_send": False}
