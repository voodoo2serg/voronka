"""Project / Branch / «Старт» domain helpers for CRM redesign."""
from __future__ import annotations

from sqlalchemy import select, func

from app.crm_service import uid
from app.db import Project, now

START_SYSTEM_KEY = "start"
START_PROJECT_NAME = "Старт"
START_PROJECT_GOAL = "Непривязанный интерес: заявки до коммерческого проекта"
START_PROJECT_DESCRIPTION = (
    "Системный проект для людей, которые оставили заявку или email, "
    "но ещё не привязаны к коммерческому Project / Branch."
)
CONVERTED_TAG = "converted"

# CRM-R8: ordinary commercial project — not a special module / not is_system.
# system_key is a stable seed marker only (unique); staff may rename the display name.
PROGREV_SYSTEM_KEY = "progrev"
PROGREV_PROJECT_NAME = "Прогрев"
PROGREV_PROJECT_GOAL = "Прогрев аудитории как обычный коммерческий проект"
PROGREV_PROJECT_DESCRIPTION = (
    "Обычный Project вместо снятого модуля «Прогрев» / outreach-nav. "
    "Цепочки и коммуникации — через конструктор и рассылки проекта."
)

PERSON_ROLES = ("lead", "client", "alumni")
ACTION_NODE_KINDS = (
    "contact",
    "survey",
    "email",
    "payment",
    "code",
    "wait",
    "telegram",
    "finish",
)


def ensure_start_project(db) -> Project:
    """Idempotent seed of system Project «Старт». Existing commercial projects stay intact."""
    row = db.scalar(select(Project).where(Project.system_key == START_SYSTEM_KEY))
    if row:
        row.is_system = True
        if not (row.name or "").strip():
            row.name = START_PROJECT_NAME
        return row
    # Prefer stable env override only when that id is free / already the start project.
    preferred = ( __import__("os").getenv("CRM_START_PROJECT_ID") or "").strip()
    if preferred:
        existing = db.get(Project, preferred)
        if existing:
            existing.system_key = START_SYSTEM_KEY
            existing.is_system = True
            existing.name = existing.name or START_PROJECT_NAME
            existing.goal = existing.goal or START_PROJECT_GOAL
            existing.description = existing.description or START_PROJECT_DESCRIPTION
            return existing
        row = Project(
            id=preferred,
            name=START_PROJECT_NAME,
            goal=START_PROJECT_GOAL,
            description=START_PROJECT_DESCRIPTION,
            status="active",
            is_system=True,
            system_key=START_SYSTEM_KEY,
            created_at=now(),
        )
        db.add(row)
        db.flush()
        return row
    row = Project(
        id=uid(),
        name=START_PROJECT_NAME,
        goal=START_PROJECT_GOAL,
        description=START_PROJECT_DESCRIPTION,
        status="active",
        is_system=True,
        system_key=START_SYSTEM_KEY,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def start_project_id(db) -> str:
    return ensure_start_project(db).id


def ensure_progrev_project(db) -> Project:
    """Idempotent seed of ordinary Project «Прогрев» (CRM-R8).

    Stable identity is ``system_key=progrev`` (unique), not the mutable display name.
    ``is_system`` stays False — not a special module; staff may rename/archive like any project.
    """
    row = db.scalar(select(Project).where(Project.system_key == PROGREV_SYSTEM_KEY))
    if row:
        row.is_system = False
        return row
    # One-time adopt of an existing name-matched row that has no system_key yet.
    row = db.scalar(
        select(Project)
        .where(Project.name == PROGREV_PROJECT_NAME, Project.system_key.is_(None))
        .order_by(Project.created_at.asc(), Project.id)
    )
    if row:
        row.system_key = PROGREV_SYSTEM_KEY
        row.is_system = False
        return row
    row = Project(
        id=uid(),
        name=PROGREV_PROJECT_NAME,
        goal=PROGREV_PROJECT_GOAL,
        description=PROGREV_PROJECT_DESCRIPTION,
        status="active",
        is_system=False,
        system_key=PROGREV_SYSTEM_KEY,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def project_board_row(db, project: Project) -> dict:
    """Home card metrics: contacts $, money $, next tasks."""
    from app.crm_models import Profile, Order, Payment, WorkItem, Deal, ActionChain, Branch

    contacts = db.scalar(
        select(func.count()).select_from(Profile).where(Profile.project_id == project.id)
    ) or 0
    order_ids = list(db.scalars(select(Order.id).where(Order.project_id == project.id)))
    received = 0
    if order_ids:
        received = (
            db.scalar(
                select(func.coalesce(func.sum(Payment.amount_minor), 0)).where(
                    Payment.order_id.in_(order_ids),
                    Payment.kind == "payment",
                )
            )
            or 0
        )
    open_tasks = list(
        db.scalars(
            select(WorkItem)
            .join(Profile, Profile.id == WorkItem.profile_id)
            .where(Profile.project_id == project.id, WorkItem.state == "open")
            .order_by(WorkItem.due_at.is_(None), WorkItem.due_at.asc(), WorkItem.id)
            .limit(5)
        )
    )
    # Fallback next actions from deals when no WorkItems.
    next_tasks: list[dict] = [
        {
            "id": t.id,
            "title": t.title,
            "due_at": t.due_at.isoformat() if t.due_at else None,
            "kind": "task",
            "profile_id": t.profile_id,
        }
        for t in open_tasks
    ]
    if not next_tasks:
        deals = list(
            db.scalars(
                select(Deal)
                .where(Deal.project_id == project.id, Deal.next_action != "")
                .order_by(Deal.due_at.is_(None), Deal.due_at.asc(), Deal.id)
                .limit(5)
            )
        )
        next_tasks = [
            {
                "id": d.id,
                "title": d.next_action or d.title,
                "due_at": d.due_at.isoformat() if d.due_at else None,
                "kind": "deal",
                "profile_id": d.profile_id,
            }
            for d in deals
        ]
    branches = db.scalar(
        select(func.count()).select_from(Branch).where(Branch.project_id == project.id)
    ) or 0
    chains = db.scalar(
        select(func.count()).select_from(ActionChain).where(ActionChain.project_id == project.id)
    ) or 0
    return {
        "id": project.id,
        "name": project.name,
        "goal": project.goal or "",
        "description": getattr(project, "description", None) or project.goal or "",
        "status": getattr(project, "status", None) or "active",
        "is_system": bool(getattr(project, "is_system", False)),
        "system_key": getattr(project, "system_key", None),
        "contacts_count": int(contacts),
        "received_minor": int(received),
        "branches_count": int(branches),
        "chains_count": int(chains),
        "next_tasks": next_tasks,
    }


def is_start_project(project: Project | None) -> bool:
    return bool(project and getattr(project, "system_key", None) == START_SYSTEM_KEY)


def profile_converted_extra(profile) -> dict:
    extra = profile.extra if isinstance(getattr(profile, "extra", None), dict) else {}
    return extra


def is_converted_start_profile(profile) -> bool:
    extra = profile_converted_extra(profile)
    if extra.get("converted_to_project_id"):
        return True
    tags = profile.tags if isinstance(getattr(profile, "tags", None), list) else []
    return CONVERTED_TAG in tags


def _start_lead_last_action(db, profile, project_id: str) -> tuple[str, str | None, bool]:
    """Prefer Activity; fall back to project-scoped message Events (same as contacts list)."""
    from datetime import timezone

    from app.crm_models import Activity
    from app.db import Connection, Event, Identity

    last = db.scalar(
        select(Activity)
        .where(Activity.profile_id == profile.id)
        .order_by(Activity.created_at.desc(), Activity.id.desc())
        .limit(1)
    )
    linked = select(Identity.connection_id).join(Connection).where(
        Identity.contact_id == profile.contact_id,
        Connection.project_id == project_id,
    )
    event = db.scalar(
        select(Event)
        .where(
            Event.contact_id == profile.contact_id,
            Event.connection_id.in_(linked),
            Event.kind.in_(["message.received", "message.sent", "manager.message_queued"]),
        )
        .order_by(Event.created_at.desc())
        .limit(1)
    )
    event_labels = {
        "message.received": "Получено сообщение",
        "message.sent": "Платформа приняла сообщение",
        "manager.message_queued": "Ответ менеджера в очереди",
    }
    if event and (
        not last
        or event.created_at.replace(tzinfo=timezone.utc)
        > last.created_at.replace(tzinfo=timezone.utc)
    ):
        return (
            event_labels.get(event.kind, event.kind),
            event.created_at.isoformat() if event.created_at else None,
            True,
        )
    if last:
        return (
            (last.outcome or last.kind or ""),
            last.created_at.isoformat() if last.created_at else None,
            True,
        )
    return ("", None, False)


def start_inbox_metrics(db, project: Project, admin=None) -> dict:
    """CRM-R7: contacted / open / converted for system Project «Старт».

    When ``admin`` is a manager, only assigned profiles are included (same as contacts list).
    """
    from app.crm_models import Profile
    from app.crm_service import assigned_profile_filter

    if not is_start_project(project):
        return {
            "start_inbox": False,
            "start_open_count": 0,
            "start_contacted_count": 0,
            "start_converted_count": 0,
            "start_leads": [],
        }
    query = select(Profile).where(Profile.project_id == project.id)
    if admin is not None:
        query = query.where(assigned_profile_filter(admin))
    profiles = list(db.scalars(query.order_by(Profile.created_at.desc(), Profile.id)))
    open_rows: list = []
    converted_rows: list = []
    for row in profiles:
        if is_converted_start_profile(row):
            converted_rows.append(row)
        else:
            open_rows.append(row)
    contacted = 0
    lead_items: list[dict] = []
    for row in open_rows:
        last_action, last_at, was_contacted = _start_lead_last_action(db, row, project.id)
        if was_contacted:
            contacted += 1
        if len(lead_items) < 12:
            extra = profile_converted_extra(row)
            lead_items.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "email": row.email or "",
                    "phone": row.phone or "",
                    "source": row.source or "",
                    "person_role": row.person_role or "lead",
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "last_action": last_action,
                    "last_action_at": last_at,
                    "contacted": was_contacted,
                    "service": str(extra.get("service") or "")[:200],
                }
            )
    return {
        "start_inbox": True,
        "start_open_count": len(open_rows),
        "start_contacted_count": contacted,
        "start_converted_count": len(converted_rows),
        "start_leads": lead_items,
    }


def convert_profile_from_start(
    db,
    profile,
    *,
    target_project_id: str,
    branch_id: str | None = None,
    person_role: str | None = None,
):
    """Attach a «Старт» lead to a commercial Project (+ optional Branch).

    Soft-convert: keeps the Старт Profile marked converted; creates/reuses a Profile
    on the target Project sharing the same Contact; reassigns open Deals.
    """
    from app.crm_models import Branch, Deal, Profile

    source = db.get(Project, profile.project_id)
    if not is_start_project(source):
        raise ValueError("Конвертация только из системного проекта «Старт»")
    if is_converted_start_profile(profile):
        raise ValueError("Лид уже конвертирован")
    target = db.get(Project, target_project_id)
    if not target:
        raise ValueError("Проект не найден")
    if is_start_project(target):
        raise ValueError("Нельзя конвертировать обратно в «Старт»")
    if target_project_id == profile.project_id:
        raise ValueError("Укажите другой проект")

    resolved_branch: str | None = None
    if branch_id:
        br = db.get(Branch, branch_id)
        if not br or br.project_id != target_project_id:
            raise ValueError("Ветка другого проекта")
        resolved_branch = br.id

    role = (person_role or profile.person_role or "lead").strip()
    if role not in PERSON_ROLES:
        raise ValueError("Неизвестная роль")

    dest = db.scalar(
        select(Profile).where(
            Profile.contact_id == profile.contact_id,
            Profile.project_id == target_project_id,
        )
    )
    if dest is None:
        dest = Profile(
            id=uid(),
            contact_id=profile.contact_id,
            person_id=profile.person_id,
            project_id=target_project_id,
            branch_id=resolved_branch,
            person_role=role,
            name=profile.name,
            email=profile.email or "",
            phone=profile.phone or "",
            company=profile.company or "",
            source=profile.source or "start_convert",
            tags=list(profile.tags or []),
            extra={
                **(profile_converted_extra(profile)),
                "converted_from_start": True,
                "start_profile_id": profile.id,
            },
            owner_id=profile.owner_id,
            stopped=bool(profile.stopped),
        )
        db.add(dest)
        db.flush()
    else:
        if resolved_branch is not None:
            dest.branch_id = resolved_branch
        dest.person_role = role
        if profile.person_id and not dest.person_id:
            dest.person_id = profile.person_id
        dest.revision = int(getattr(dest, "revision", 1) or 1) + 1

    open_deals = list(
        db.scalars(
            select(Deal).where(
                Deal.profile_id == profile.id,
                Deal.stage.notin_(["won", "lost"]),
            )
        )
    )
    for deal in open_deals:
        deal.profile_id = dest.id
        deal.project_id = target_project_id
        deal.revision = int(getattr(deal, "revision", 1) or 1) + 1

    stamp = now().isoformat()
    extra = dict(profile_converted_extra(profile))
    extra["converted_to_project_id"] = target_project_id
    extra["converted_to_profile_id"] = dest.id
    extra["converted_at"] = stamp
    if resolved_branch:
        extra["converted_to_branch_id"] = resolved_branch
    profile.extra = extra
    tags = list(profile.tags or [])
    if CONVERTED_TAG not in tags:
        tags.append(CONVERTED_TAG)
    profile.tags = tags
    profile.revision = int(getattr(profile, "revision", 1) or 1) + 1
    return dest


def validate_action_nodes(nodes: list) -> list:
    if not isinstance(nodes, list):
        raise ValueError("nodes должен быть списком")
    if len(nodes) > 40:
        raise ValueError("Слишком много шагов (макс. 40)")
    cleaned = []
    seen = set()
    for i, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            raise ValueError(f"Шаг {i + 1}: ожидается объект")
        nid = str(raw.get("id") or uid())
        if nid in seen:
            raise ValueError(f"Дубликат id шага: {nid}")
        seen.add(nid)
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in ACTION_NODE_KINDS:
            raise ValueError(f"Неизвестный тип шага: {kind or '∅'}")
        title = str(raw.get("title") or "").strip()[:200]
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        media = raw.get("media_asset_id")
        media_asset_id = str(media).strip()[:80] if media else None
        from_address = str(raw.get("from_address") or "").strip()[:250] or None
        # VR-454: vault refs live in config (vault_id / from_vault_id) — keep opaque string ids only.
        cfg = dict(config)
        for key in ("vault_id", "from_vault_id"):
            if key in cfg and cfg[key] is not None:
                cfg[key] = str(cfg[key]).strip()[:36] or None
        cleaned.append(
            {
                "id": nid,
                "kind": kind,
                "title": title or kind,
                "config": cfg,
                "media_asset_id": media_asset_id,
                "from_address": from_address,
            }
        )
    return cleaned


def chain_to_funnel_graph(nodes: list) -> dict:
    """Minimal funnel graph so published ActionChain can ride existing Run/Outbox runtime."""
    if not nodes:
        return {"start": "finish", "nodes": {"finish": {"type": "finish", "text": "Готово"}}}
    graph_nodes: dict = {}
    start_id = nodes[0]["id"]
    for idx, node in enumerate(nodes):
        has_next = idx + 1 < len(nodes)
        nxt = nodes[idx + 1]["id"] if has_next else "finish"
        kind = node["kind"]
        cfg = node.get("config") or {}
        title = node.get("title") or kind
        if kind == "finish":
            entry = {"type": "finish", "text": str(title)[:160]}
        elif kind == "survey":
            options = cfg.get("options") if isinstance(cfg.get("options"), list) else ["Да", "Нет"]
            options = [str(o)[:100] for o in options[:20]] or ["Да", "Нет"]
            qkey = str(cfg.get("key") or f"q_{node['id']}")[:80]
            entry = {
                "type": "survey",
                "key": qkey,
                "text": str(cfg.get("question") or title)[:4000],
                "questions": [
                    {
                        "key": qkey,
                        "text": str(cfg.get("question") or title)[:4000],
                        "options": options,
                    }
                ],
                "next": nxt,
            }
        elif kind == "wait":
            try:
                seconds = int(cfg.get("seconds") or 60)
            except (TypeError, ValueError):
                seconds = 60
            entry = {
                "type": "wait",
                "seconds": max(1, min(seconds, 2592000)),
                "next": nxt,
            }
        else:
            # email / telegram / contact / payment / code → text node in runtime
            text = str(cfg.get("body") or cfg.get("text") or cfg.get("question") or title)
            entry = {"type": "text", "text": text[:4000], "next": nxt}
        graph_nodes[node["id"]] = entry
    # Add terminal «finish» only when referenced by a next pointer.
    referenced = {entry.get("next") for entry in graph_nodes.values() if isinstance(entry.get("next"), str)}
    if "finish" in referenced and "finish" not in graph_nodes:
        graph_nodes["finish"] = {"type": "finish", "text": "Готово"}
    return {"start": start_id, "nodes": graph_nodes}


def get_base_action_chain(db, project_id: str, branch_id: str | None = None):
    """CRM-R5: Project (branch_id=None) or Branch base ActionChain, if any."""
    from app.crm_models import ActionChain

    q = select(ActionChain).where(
        ActionChain.project_id == project_id,
        ActionChain.is_base.is_(True),
    )
    if branch_id:
        q = q.where(ActionChain.branch_id == branch_id)
    else:
        q = q.where(ActionChain.branch_id.is_(None))
    return db.scalar(q.order_by(ActionChain.updated_at.desc(), ActionChain.id))


def promote_action_chain_as_base(db, row) -> None:
    """CRM-R5: Сохранить → this chain becomes the base for its Project/Branch scope.

    Locks existing base rows in-scope (FOR UPDATE) before flipping flags.
    DB partial unique indexes enforce one base even under concurrent Save.
    """
    from app.crm_models import ActionChain

    q = (
        select(ActionChain)
        .where(
            ActionChain.project_id == row.project_id,
            ActionChain.is_base.is_(True),
        )
        .with_for_update()
    )
    if row.branch_id:
        q = q.where(ActionChain.branch_id == row.branch_id)
    else:
        q = q.where(ActionChain.branch_id.is_(None))
    for other in db.scalars(q):
        if other.id != row.id:
            other.is_base = False
    # Flush demotions first — SQLite enforces unique indexes per statement.
    db.flush()
    row.is_base = True


def base_chain_summary(row) -> dict | None:
    """Compact base-chain payload for dashboard / work-today (not fixed funnel template)."""
    if row is None:
        return None
    nodes = row.nodes if isinstance(row.nodes, list) else []
    return {
        "id": row.id,
        "name": row.name,
        "branch_id": row.branch_id,
        "state": row.state,
        "is_base": bool(getattr(row, "is_base", False)),
        "revision": row.revision,
        "funnel_id": row.funnel_id,
        "steps": [
            {
                "id": n.get("id"),
                "kind": n.get("kind"),
                "title": n.get("title") or n.get("kind"),
            }
            for n in nodes
            if isinstance(n, dict)
        ],
    }


# --- CRM-R11: Course ↔ Project M2M ---


def ensure_project_course(
    db,
    project_id: str,
    course_id: str,
    *,
    label: str = "",
    branch_id: str | None = None,
    fill_label_if_empty: bool = False,
):
    """Idempotent bind of LMS course_id to a Project (same course may hang on many projects).

    ``fill_label_if_empty``: product sync may suggest a label only for new/empty rows —
    never overwrite a staff-entered label (CRM-R11 Codex P2).
    """
    from app.crm_models import ProjectCourse

    cid = (course_id or "").strip()
    if not cid:
        raise ValueError("course_id required")
    prior = db.scalar(
        select(ProjectCourse).where(
            ProjectCourse.project_id == project_id,
            ProjectCourse.course_id == cid,
        )
    )
    suggested = (label or "").strip()
    if prior:
        if fill_label_if_empty:
            if suggested and not (prior.label or "").strip():
                prior.label = suggested
        elif suggested:
            prior.label = suggested
        if branch_id is not None:
            prior.branch_id = branch_id or None
        return prior
    row = ProjectCourse(
        id=uid(),
        project_id=project_id,
        course_id=cid,
        branch_id=branch_id or None,
        label=suggested,
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def project_products_reference_course(
    db, project_id: str, course_id: str, *, exclude_product_id: str | None = None
) -> bool:
    """True if any Product on the project still has config.course_id == course_id."""
    from app.crm_models import Product

    cid = (course_id or "").strip()
    if not cid:
        return False
    for product in db.scalars(select(Product).where(Product.project_id == project_id)):
        if exclude_product_id and product.id == exclude_product_id:
            continue
        cfg = product.config if isinstance(product.config, dict) else {}
        raw = cfg.get("course_id")
        if isinstance(raw, str) and raw.strip() == cid:
            return True
    return False


def release_project_course_if_unused(
    db,
    project_id: str,
    course_id: str,
    *,
    auto_labels: set[str] | None = None,
) -> bool:
    """Drop product-auto ProjectCourse when no Product still references it.

    Keeps binds that look staff-explicit: nonempty label not in ``auto_labels``
    (product names that owned the auto-bind). Manual + Курс labels survive.
    """
    from app.crm_models import ProjectCourse

    cid = (course_id or "").strip()
    if not cid:
        return False
    if project_products_reference_course(db, project_id, cid):
        return False
    row = db.scalar(
        select(ProjectCourse).where(
            ProjectCourse.project_id == project_id,
            ProjectCourse.course_id == cid,
        )
    )
    if not row:
        return False
    label = (row.label or "").strip()
    if label and auto_labels is not None and label not in auto_labels:
        return False
    db.delete(row)
    db.flush()
    return True


def course_projects_payload(db, course_id: str, accessible_ids: set[str] | None = None) -> list[dict]:
    """Projects that share this LMS course_id — staff see Project name (CRM-R11)."""
    from app.crm_models import ProjectCourse

    cid = (course_id or "").strip()
    if not cid:
        return []
    binds = list(
        db.scalars(
            select(ProjectCourse)
            .where(ProjectCourse.course_id == cid)
            .order_by(ProjectCourse.created_at.asc(), ProjectCourse.id)
        )
    )
    out: list[dict] = []
    for bind in binds:
        if accessible_ids is not None and bind.project_id not in accessible_ids:
            continue
        project = db.get(Project, bind.project_id)
        if not project:
            continue
        out.append(
            {
                "project_id": project.id,
                "project_name": project.name,
                "label": bind.label or "",
                "branch_id": bind.branch_id,
                "bind_id": bind.id,
            }
        )
    return out


def encode_project_course(db, row, accessible_ids: set[str] | None = None) -> dict:
    """ProjectCourse row + project_name + sibling projects sharing the same course_id."""
    project = db.get(Project, row.project_id)
    projects = course_projects_payload(db, row.course_id, accessible_ids=accessible_ids)
    return {
        "id": row.id,
        "project_id": row.project_id,
        "project_name": project.name if project else "",
        "course_id": row.course_id,
        "branch_id": row.branch_id,
        "label": row.label or "",
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "projects": projects,
    }
