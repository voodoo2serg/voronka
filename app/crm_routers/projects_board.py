"""Home Projects board, Branch CRUD, Course M2M, Project dashboard extras."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import or_, select, func

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
from app.crm_service import (
    audit,
    get_profile,
    manager_scope,
    project_access,
    project_ids,
    uid,
)
from app.db import Project, now
from app.projects_domain import (
    PERSON_ROLES,
    base_chain_summary,
    convert_profile_from_start,
    course_projects_payload,
    encode_project_course,
    ensure_progrev_project,
    ensure_project_course,
    ensure_start_project,
    get_base_action_chain,
    is_start_project,
    project_board_row,
    start_inbox_metrics,
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


class ConvertBody(Body):
    """CRM-R7: move a «Старт» lead into a commercial Project (+ optional Branch)."""

    project_id: str = Field(min_length=1, max_length=36)
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
        deal_filter = [Deal.project_id == pid]
        if manager_scope(admin):
            # Same visibility as GET /projects/{pid}/deals.
            deal_filter.append(
                or_(Deal.owner_id == admin.telegram_id, Profile.owner_id == admin.telegram_id)
            )
        deal_count_q = select(func.count()).select_from(Deal)
        recent_q = select(Deal).where(Deal.project_id == pid)
        if manager_scope(admin):
            deal_count_q = deal_count_q.join(Profile, Profile.id == Deal.profile_id)
            recent_q = recent_q.join(Profile, Profile.id == Deal.profile_id)
        deals = db.scalar(deal_count_q.where(*deal_filter)) or 0
        open_deals = (
            db.scalar(
                deal_count_q.where(*deal_filter, Deal.stage.notin_(["won", "lost"]))
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
        access_ids = set(project_ids(db, admin))
        base = get_base_action_chain(db, pid, None)
        if manager_scope(admin):
            recent_q = recent_q.where(
                or_(Deal.owner_id == admin.telegram_id, Profile.owner_id == admin.telegram_id)
            )
        recent_deals = list(
            db.scalars(recent_q.order_by(Deal.created_at.desc(), Deal.id.desc()).limit(8))
        )
        start_extra = start_inbox_metrics(db, project, admin=admin)
        return {
            **board,
            "deals_count": int(deals),
            "open_deals_count": int(open_deals),
            "products_count": int(products),
            "branches": [encode(b) for b in branches],
            # CRM-R11: each course includes project_name + sibling projects sharing course_id.
            "courses": [encode_project_course(db, c, accessible_ids=access_ids) for c in courses],
            "chains": [encode(c) for c in chains],
            # CRM-R5: SoR for project steps = saved constructor base, not fixed funnel template.
            "base_chain": base_chain_summary(base),
            "recent_deals": [encode(d) for d in recent_deals],
            **start_extra,
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
    """CRM-R11: courses on this project; each item lists all Projects sharing the course."""
    with Session() as db:
        project_access(db, admin, pid)
        access_ids = set(project_ids(db, admin))
        rows = list(db.scalars(select(ProjectCourse).where(ProjectCourse.project_id == pid)))
        # Also surface Product.config.course_id bindings for this project.
        products = list(db.scalars(select(Product).where(Product.project_id == pid, Product.active.is_(True))))
        product_courses = []
        for p in products:
            cfg = p.config if isinstance(p.config, dict) else {}
            cid = cfg.get("course_id")
            if isinstance(cid, str) and cid.strip():
                course_id = cid.strip()
                product_courses.append(
                    {
                        "product_id": p.id,
                        "product_name": p.name,
                        "course_id": course_id,
                        "projects": course_projects_payload(db, course_id, accessible_ids=access_ids),
                    }
                )
        return {
            "items": [encode_project_course(db, r, accessible_ids=access_ids) for r in rows],
            "product_binds": product_courses,
        }


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
        created = prior is None
        row = ensure_project_course(db, pid, course_id, branch_id=body.branch_id)
        # Explicit staff bind always writes label (including clear).
        row.label = (body.label or "").strip()
        row.branch_id = body.branch_id
        audit(
            db,
            admin,
            "project_course.updated" if not created else "project_course.bound",
            project_id=pid,
            course_id=course_id,
        )
        access_ids = set(project_ids(db, admin))
        return encode_project_course(db, row, accessible_ids=access_ids)


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


@router.get("/courses/{course_id}/projects")
def course_projects(course_id: str, admin=Depends(crm_actor)):
    """CRM-R11 reverse lookup: which Projects (name visible) attach this LMS course."""
    cid = course_id.strip()
    if not cid:
        raise HTTPException(422, "course_id обязателен")
    with Session() as db:
        access_ids = set(project_ids(db, admin))
        items = course_projects_payload(db, cid, accessible_ids=access_ids)
        if not items:
            # 404 only when no binds at all (vs empty because of ACL).
            any_bind = db.scalar(select(ProjectCourse.id).where(ProjectCourse.course_id == cid).limit(1))
            if not any_bind:
                raise HTTPException(404, "Курс не привязан ни к одному проекту")
        return {"course_id": cid, "items": items, "project_names": [x["project_name"] for x in items]}


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


@router.post("/contacts/{profile_id}/convert")
def convert_contact(profile_id: str, body: ConvertBody, admin=Depends(crm_actor)):
    """CRM-R7: convert a «Старт» lead into a commercial Project / Branch."""
    target_project_id = body.project_id.strip()
    with Session.begin() as db:
        # get_profile enforces project_access + profile_visible (manager scope).
        row = get_profile(db, admin, profile_id)
        project_access(db, admin, target_project_id)
        source = must(db.get(Project, row.project_id))
        if not is_start_project(source):
            raise HTTPException(422, "Конвертация только из системного проекта «Старт»")
        try:
            dest = convert_profile_from_start(
                db,
                row,
                target_project_id=target_project_id,
                branch_id=(body.branch_id.strip() if body.branch_id else None),
                person_role=body.person_role,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        audit(
            db,
            admin,
            "profile.converted_from_start",
            project_id=target_project_id,
            profile_id=dest.id,
            start_profile_id=profile_id,
        )
        return {
            "ok": True,
            "start_profile": encode(row),
            "profile": encode(dest),
            "project_id": dest.project_id,
            "branch_id": dest.branch_id,
        }
