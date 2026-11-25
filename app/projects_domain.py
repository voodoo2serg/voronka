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
    """CRM-R5: Сохранить → this chain becomes the base for its Project/Branch scope."""
    from app.crm_models import ActionChain

    q = select(ActionChain).where(
        ActionChain.project_id == row.project_id,
        ActionChain.is_base.is_(True),
        ActionChain.id != row.id,
    )
    if row.branch_id:
        q = q.where(ActionChain.branch_id == row.branch_id)
    else:
        q = q.where(ActionChain.branch_id.is_(None))
    for other in db.scalars(q):
        other.is_base = False
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
