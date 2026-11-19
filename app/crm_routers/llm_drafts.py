"""Inbox approve/edit/reject for llm_reply drafts (PDialog P1). Auto-send stays off."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must
from app.crm_models import LlmReplyAudit
from app.crm_service import audit, project_access, uid
from app.db import Identity, Outbox, Run, Version, now
from app.runtime import advance, event, pause_for_operator

router = APIRouter()

PENDING = {"pending", "ok", "fallback"}


class DraftDecision(Body):
    text: str | None = Field(default=None, max_length=4000)


def _get_draft(db, draft_id: str, admin) -> LlmReplyAudit:
    row = must(db.get(LlmReplyAudit, draft_id))
    if row.project_id:
        project_access(db, admin, row.project_id)
    elif admin.role != "owner":
        raise HTTPException(403, "Нет доступа")
    return row


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
        row = _get_draft(db, draft_id, admin)
        if row.response_status not in PENDING:
            raise HTTPException(409, "Черновик уже обработан")
        text = (body.text if body.text is not None else row.draft_text or "").strip()
        if not text:
            raise HTTPException(422, "Пустой текст")
        edited = body.text is not None and body.text.strip() != (row.draft_text or "").strip()
        identity = must(db.get(Identity, row.identity_id))
        out = Outbox(
            id=uid(),
            connection_id=identity.connection_id,
            identity_id=identity.id,
            run_id=row.run_id,
            chat_id=identity.chat_id,
            payload={"type": "text", "text": text[:4000]},
            created_at=now(),
            due_at=now(),
        )
        db.add(out)
        db.flush()
        row.draft_text = text[:20000]
        row.response_status = "edited" if edited else "approved"
        row.approved_by = admin.telegram_id
        row.message_id = out.id
        row.updated_at = now()
        run = db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
        if run and run.status in {"waiting", "paused", "active"}:
            state = dict(run.state or {})
            graph = db.get(Version, run.version_id).graph
            node = graph["nodes"].get(run.current) or {}
            # Advance past llm_reply if still parked on it.
            if node.get("type") == "llm_reply" or state.get("_await") == "llm_approval":
                nxt = node.get("next") or state.get("_escalate_next")
                if not nxt and node.get("type") == "llm_reply":
                    nxt = node.get("next")
                state.pop("_await", None)
                state.pop("_llm_draft_id", None)
                if nxt:
                    run.state, run.current, run.status = state, nxt, "active"
                    advance(db, run, identity)
                else:
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
        row = _get_draft(db, draft_id, admin)
        if row.response_status not in PENDING:
            raise HTTPException(409, "Черновик уже обработан")
        row.response_status = "rejected"
        row.approved_by = admin.telegram_id
        row.updated_at = now()
        if body.text:
            row.draft_text = body.text[:20000]
        identity = must(db.get(Identity, row.identity_id))
        run = db.scalar(select(Run).where(Run.id == row.run_id).with_for_update())
        contact = identity.contact_id
        from app.db import Contact
        contact_row = db.get(Contact, contact)
        if run and contact_row:
            pause_for_operator(
                db, run, identity, contact_row,
                reason="llm_rejected",
                task_title="Черновик ИИ отклонён — нужен ручной ответ",
            )
            state = dict(run.state or {})
            state.pop("_await", None)
            run.state = state
        event(
            db, "llm_reply.rejected", identity.connection_id, identity.contact_id, row.run_id,
            actor_id=admin.telegram_id, audit_id=row.id,
        )
        audit(db, admin, "llm_draft.rejected", draft_id=row.id)
        return {**encode(row), "auto_send": False}
