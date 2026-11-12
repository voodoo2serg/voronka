"""AI provider + rules admin API (VOR-3). Draft never auto-sends."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app import ai_providers
from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner
from app.crm_models import AiRule
from app.crm_service import uid, project_access, audit
from app.db import Connection, now

router = APIRouter()


class AiRuleBody(Body):
    scope: Literal["global", "project", "connection"]
    project_id: str | None = None
    connection_id: str | None = None
    provider: Literal["inherit", "raga", "openrouter", "ollama"] = "inherit"
    model: str = Field(default="", max_length=120)
    system_rules: str = Field(default="", max_length=20000)
    active: bool = True


@router.get("/ai/status")
def ai_status(admin=Depends(crm_actor)):
    status = ai_providers.env_status()
    with Session() as db:
        rules = [encode(r) for r in db.scalars(select(AiRule).order_by(AiRule.scope, AiRule.updated_at.desc()))]
    return {**status, "rules": rules, "sent_default": False}


@router.put("/ai/rules")
def upsert_rule(body: AiRuleBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    if body.scope == "global" and (body.project_id or body.connection_id):
        raise HTTPException(422, "global без project/connection")
    if body.scope == "project" and not body.project_id:
        raise HTTPException(422, "project_id обязателен")
    if body.scope == "connection" and not body.connection_id:
        raise HTTPException(422, "connection_id обязателен")
    with Session.begin() as db:
        if body.project_id:
            project_access(db, admin, body.project_id)
        if body.connection_id:
            conn = must(db.get(Connection, body.connection_id))
            if body.project_id and conn.project_id != body.project_id:
                raise HTTPException(422, "connection другого проекта")
        query = select(AiRule).where(AiRule.scope == body.scope)
        if body.scope == "global":
            query = query.where(AiRule.project_id.is_(None), AiRule.connection_id.is_(None))
        elif body.scope == "project":
            query = query.where(AiRule.project_id == body.project_id, AiRule.connection_id.is_(None))
        else:
            query = query.where(AiRule.connection_id == body.connection_id)
        row = db.scalar(query.with_for_update())
        if row:
            row.provider = body.provider
            row.model = body.model
            row.system_rules = body.system_rules
            row.active = body.active
            row.revision += 1
            row.updated_at = now()
            row.updated_by = admin.telegram_id
        else:
            row = AiRule(
                id=uid(),
                scope=body.scope,
                project_id=body.project_id if body.scope != "global" else None,
                connection_id=body.connection_id if body.scope == "connection" else None,
                provider=body.provider,
                model=body.model,
                system_rules=body.system_rules,
                active=body.active,
                revision=1,
                updated_at=now(),
                updated_by=admin.telegram_id,
            )
            db.add(row)
            db.flush()
        audit(db, admin, "ai.rule_upserted", rule_id=row.id, scope=row.scope)
        return encode(row)


