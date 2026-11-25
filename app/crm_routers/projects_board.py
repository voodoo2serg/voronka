"""Home Projects board, Branch CRUD, Course M2M, Project dashboard extras."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, func

from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner
from app.crm_models import (
    ActionChain,
    Branch,
    Deal,
    Order,
    Payment,
    Product,
    Profile,
    ProjectCourse,
    WorkItem,
)
from app.crm_service import audit, project_access, project_ids, uid
from app.db import Project, now
from app.projects_domain import (
    PERSON_ROLES,
    base_chain_summary,
    ensure_progrev_project,
    ensure_start_project,
    get_base_action_chain,
    project_board_row,
    start_project_id,
)

router = APIRouter()


class ProjectUpdate(Body):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    goal: str | None = Field(default=None, max_length=400)
    description: str | None = Field(default=None, max_length=800)
    status: Literal["active", "archived"] | None = None


class BranchBody(Body):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=800)


class BranchPatch(Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=800)
    status: Literal["active", "archived"] | None = None


class CourseBind(Body):
    course_id: str = Field(min_length=1, max_length=200)
    label: str = Field(default="", max_length=200)
    branch_id: str | None = None


class ProfileBranchBody(Body):
    branch_id: str | None = None
    person_role: Literal["lead", "client", "alumni"] | None = None


@router.get("/home/projects")
def home_projects(admin=Depends(crm_actor)):
    """CRM home = Projects board (contacts $, money $, next tasks)."""
    with Session.begin() as db:
        ensure_start_project(db)
        ensure_progrev_project(db)
        ids = project_ids(db, admin)
        rows = list(
            db.scalars(
                select(Project)
                .where(Project.id.in_(ids))
                .order_by(Project.is_system.desc(), Project.name, Project.id)
            )
        )
        return {
            "items": [project_board_row(db, p) for p in rows],
            "start_project_id": start_project_id(db),
        }


@router.post("/system/ensure-start")
def ensure_start(admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = ensure_start_project(db)
        audit(db, admin, "project.start_ensured", project_id=row.id)
        return encode(row)


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: ProjectUpdate, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = must(db.get(Project, pid))
        if row.is_system and body.name is not None and body.name.strip() != row.name:
            # Allow rename only for non-system; Старт name stays stable for staff mental model.
            if row.system_key == "start" and body.name.strip() != "Старт":
                raise HTTPException(422, "Системный проект «Старт» нельзя переименовать")
        if body.name is not None:
            row.name = body.name.strip()
        if body.goal is not None:
            row.goal = body.goal.strip()
        if body.description is not None:
            row.description = body.description.strip()
        if body.status is not None:
            if row.is_system and body.status == "archived":
                raise HTTPException(422, "Системный проект нельзя архивировать")
            row.status = body.status
        audit(db, admin, "project.updated", project_id=pid)
        return encode(row)


@router.get("/projects/{pid}/dashboard")
def project_dashboard(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        project = must(db.get(Project, pid))
        board = project_board_row(db, project)
        deals = db.scalar(select(func.count()).select_from(Deal).where(Deal.project_id == pid)) or 0
        open_deals = (
            db.scalar(
                select(func.count())
                .select_from(Deal)
                .where(Deal.project_id == pid, Deal.stage.notin_(["won", "lost"]))
            )
            or 0
        )
        products = db.scalar(select(func.count()).select_from(Product).where(Product.project_id == pid)) or 0
        chains = list(
            db.scalars(
                select(ActionChain)
                .where(ActionChain.project_id == pid)
                .order_by(ActionChain.is_base.desc(), ActionChain.updated_at.desc(), ActionChain.id)
                .limit(20)
            )
        )
        branches = list(
            db.scalars(select(Branch).where(Branch.project_id == pid).order_by(Branch.name, Branch.id))
        )
        courses = list(db.scalars(select(ProjectCourse).where(ProjectCourse.project_id == pid)))
        base = get_base_action_chain(db, pid, None)
        start_clients = 0
        if project.system_key == "start":
            start_clients = board["contacts_count"]
        return {
            **board,
            "deals_count": int(deals),
            "open_deals_count": int(open_deals),
            "products_count": int(products),
            "branches": [encode(b) for b in branches],
            "courses": [encode(c) for c in courses],
            "chains": [encode(c) for c in chains],
            # CRM-R5: SoR for project steps = saved constructor base, not fixed funnel template.
            "base_chain": base_chain_summary(base),
            "start_inbox": project.system_key == "start",
            "start_open_count": int(start_clients),
        }


@router.get("/projects/{pid}/branches")
def list_branches(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(db.scalars(select(Branch).where(Branch.project_id == pid).order_by(Branch.name)))
        return {"items": [encode(r) for r in rows]}


@router.post("/projects/{pid}/branches")
def create_branch(pid: str, body: BranchBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = Branch(
            id=uid(),
            project_id=pid,
            name=body.name.strip(),
            description=(body.description or "").strip(),
            status="active",
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        audit(db, admin, "branch.created", project_id=pid, branch_id=row.id)
        return encode(row)


@router.patch("/branches/{bid}")
def patch_branch(bid: str, body: BranchPatch, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.get(Branch, bid))
        project_access(db, admin, row.project_id)
        if body.name is not None:
            row.name = body.name.strip()
        if body.description is not None:
            row.description = body.description.strip()
        if body.status is not None:
            row.status = body.status
        row.updated_at = now()
        audit(db, admin, "branch.updated", project_id=row.project_id, branch_id=bid)
        return encode(row)


@router.get("/projects/{pid}/courses")
def list_courses(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(db.scalars(select(ProjectCourse).where(ProjectCourse.project_id == pid)))
        # Also surface Product.config.course_id bindings for this project.
        products = list(db.scalars(select(Product).where(Product.project_id == pid, Product.active.is_(True))))
        product_courses = []
        for p in products:
            cfg = p.config if isinstance(p.config, dict) else {}
            cid = cfg.get("course_id")
            if isinstance(cid, str) and cid.strip():
                product_courses.append(
                    {"product_id": p.id, "product_name": p.name, "course_id": cid.strip()}
                )
        return {"items": [encode(r) for r in rows], "product_binds": product_courses}


@router.post("/projects/{pid}/courses")
def bind_course(pid: str, body: CourseBind, admin=Depends(crm_actor)):
    course_id = body.course_id.strip()
    with Session.begin() as db:
        project_access(db, admin, pid)
        if body.branch_id:
            br = must(db.get(Branch, body.branch_id))
            if br.project_id != pid:
                raise HTTPException(422, "Ветка другого проекта")
        prior = db.scalar(
            select(ProjectCourse).where(ProjectCourse.project_id == pid, ProjectCourse.course_id == course_id)
        )
        if prior:
            prior.label = (body.label or prior.label or "").strip()
            prior.branch_id = body.branch_id
            audit(db, admin, "project_course.updated", project_id=pid, course_id=course_id)
            return encode(prior)
        row = ProjectCourse(
            id=uid(),
            project_id=pid,
            course_id=course_id,
            branch_id=body.branch_id,
            label=(body.label or "").strip(),
            created_at=now(),
        )
        db.add(row)
        audit(db, admin, "project_course.bound", project_id=pid, course_id=course_id)
        return encode(row)


@router.delete("/projects/{pid}/courses/{course_id}")
def unbind_course(pid: str, course_id: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = db.scalar(
            select(ProjectCourse).where(ProjectCourse.project_id == pid, ProjectCourse.course_id == course_id)
        )
        must(row)
        db.delete(row)
        audit(db, admin, "project_course.unbound", project_id=pid, course_id=course_id)
        return {"ok": True}


@router.patch("/contacts/{profile_id}/placement")
def place_contact(profile_id: str, body: ProfileBranchBody, admin=Depends(crm_actor)):
    """Attach Profile to Branch and/or set person_role (lead|client|alumni)."""
    with Session.begin() as db:
        row = must(db.get(Profile, profile_id))
        project_access(db, admin, row.project_id)
        if body.branch_id is not None:
            if body.branch_id == "":
                row.branch_id = None
            else:
                br = must(db.get(Branch, body.branch_id))
                if br.project_id != row.project_id:
                    raise HTTPException(422, "Ветка другого проекта")
                row.branch_id = br.id
        if body.person_role is not None:
            if body.person_role not in PERSON_ROLES:
                raise HTTPException(422, "Неизвестная роль")
            row.person_role = body.person_role
        row.revision += 1
        audit(db, admin, "profile.placement", project_id=row.project_id, profile_id=profile_id)
        return encode(row)
