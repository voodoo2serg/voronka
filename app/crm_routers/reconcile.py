"""OPS-104 admin APIs: dry-run preview, mutate apply, DLQ list."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from app import reconcile as reconcile_mod
from app.crm_deps import Body, Session, crm_actor, encode, staff_owner
from app.crm_models import IntegrationOutbox
from app.crm_service import audit, project_access

router = APIRouter()

ScopeParam = Literal["payment", "enrollment", "publish", "all"]


class ReconcileApplyBody(Body):
    scope: ScopeParam = "all"
    finding_ids: list[str] | None = Field(default=None, max_length=200)
    acknowledge_mutate: bool = False
    limit: int = Field(default=50, ge=1, le=50)


@router.get("/projects/{pid}/reconcile/preview")
def reconcile_preview(
    pid: str,
    scope: ScopeParam = Query(default="all"),
    limit: int = Query(default=100, ge=1, le=200),
    admin=Depends(crm_actor),
):
    """Dry-run detectors — no mutations. Owner only."""
    staff_owner(admin)
    with Session() as db:
        project_access(db, admin, pid)
        return reconcile_mod.preview(db, scope=scope, project_id=pid, limit=limit)


@router.post("/projects/{pid}/reconcile/apply")
def reconcile_apply(pid: str, body: ReconcileApplyBody, admin=Depends(crm_actor)):
    """Mutating recovery. Requires acknowledge_mutate. Never changes Payment facts."""
    staff_owner(admin)
    if not body.acknowledge_mutate:
        raise HTTPException(422, "Подтвердите мутацию (acknowledge_mutate)")
    with Session.begin() as db:
        project_access(db, admin, pid)
        result = reconcile_mod.apply_findings(
            db,
            scope=body.scope,
            project_id=pid,
            finding_ids=body.finding_ids,
            limit=body.limit,
        )
        audit(
            db,
            admin,
            "reconcile.apply",
            project_id=pid,
            scope=body.scope,
            applied_count=result["applied_count"],
            skipped_count=result["skipped_count"],
            finding_ids=body.finding_ids or [],
        )
        return result


@router.get("/projects/{pid}/integration-outbox/dlq")
def list_dlq(
    pid: str,
    limit: int = Query(default=100, ge=1, le=200),
    admin=Depends(crm_actor),
):
    """DLQ = IntegrationOutbox.state=dead with correlation_id."""
    staff_owner(admin)
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(
            db.scalars(
                select(IntegrationOutbox)
                .where(IntegrationOutbox.state == "dead")
                .order_by(IntegrationOutbox.updated_at.desc())
                .limit(limit * 3)
            )
        )
        items = []
        for r in rows:
            payload = r.payload or {}
            project_match = payload.get("project_id") in {None, pid}
            if not project_match and payload.get("order_id"):
                from app.crm_models import Order

                order = db.get(Order, payload["order_id"])
                project_match = bool(order and order.project_id == pid)
            if not project_match and payload.get("project_id") is not None:
                continue
            items.append(
                {
                    **encode(r),
                    "correlation_id": r.correlation_id or payload.get("correlation_id") or payload.get("order_id") or "",
                }
            )
            if len(items) >= limit:
                break
        return {"items": items, "count": len(items), "auto_retry_cap_attempts": 12}
