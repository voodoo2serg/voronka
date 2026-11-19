"""CRM endpoints for thin company / project setup defaults."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app import company_setup
from app.crm_deps import Session, crm_actor, staff_owner
from app.crm_service import project_access, audit

router = APIRouter()


@router.get("/company-setup/blueprint")
def company_setup_blueprint(admin=Depends(crm_actor)):
    """Static consent / Signal / AccessGrant contract (any staff)."""
    return {**company_setup.blueprint(), "env": company_setup.env_checklist()}


@router.get("/projects/{pid}/company-setup")
def company_setup_status(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        return company_setup.project_status(db, project_id=pid)


@router.post("/projects/{pid}/company-setup")
def company_setup_apply(pid: str, admin=Depends(crm_actor)):
    """Apply project AiRule defaults + return Signal/AccessGrant checklist."""
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        result = company_setup.apply_project_defaults(
            db, project_id=pid, actor_telegram_id=admin.telegram_id
        )
        audit(db, admin, "company_setup.applied", project_id=pid, rule_id=result["ai_rule_id"])
        return result
