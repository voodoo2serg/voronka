"""Ops wizards: channel→wait→DM, support pipelines. Creates Funnel drafts via graph API rules."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app import portals
from app.crm_deps import Body, Session
from app.crm_service import project_access, uid, audit
from app.db import Connection, Funnel, Version, Entry
from app.graph import validate_graph, validate_bindings
from app.ops_routers.deps import ops_actor
from app.runtime import event

router = APIRouter()


class ChannelDmWizard(Body):
    name: str = Field(min_length=1, max_length=160)
    channel_connection_id: str
    dm_connection_id: str
    wait_days: int = Field(ge=0, le=30)
    wait_seconds: int | None = Field(default=None, ge=1, le=2592000)
    dm_text: str = Field(min_length=1, max_length=4000)
    # Channel→DM is useless without publish + Entry; always publish.
    publish: bool = True


class SupportWizard(Body):
    name: str = Field(min_length=1, max_length=160)
    connection_id: str
    menu_text: str = Field(default="Чем помочь?", min_length=1, max_length=2000)
    branches: list[dict] = Field(min_length=1, max_length=5)
    escalate_text: str = Field(
        default="Передаю менеджеру — ответим в ближайшее время.",
        min_length=1,
        max_length=4000,
    )
    publish: bool = False


def _wait_seconds(days: int, seconds: int | None) -> int:
    if seconds is not None:
        return seconds
    return max(1, int(days) * 86400)


def _channel_dm_graph(*, wait_sec: int, dm_text: str, dm_connection_id: str) -> dict:
    """Join on channel connection → handoff to DM bot → wait → personal text."""
    return {
        "start": "joined",
        "meta": {"trigger": "channel_join", "dm_connection_id": dm_connection_id},
        "nodes": {
            "joined": {
                "type": "handoff",
                "connection_id": dm_connection_id,
                "next": "wait_dm",
            },
            "wait_dm": {"type": "wait", "seconds": wait_sec, "next": "personal"},
            "personal": {"type": "text", "text": dm_text, "next": "finish"},
            "finish": {"type": "finish"},
        },
    }


def _support_graph(*, menu_text: str, branches: list[dict], escalate_text: str) -> dict:
    options: list[str] = []
    routes: dict[str, str] = {}
    nodes: dict = {
        "menu": {
            "type": "question",
            "title": "Меню поддержки",
            "key": "support_choice",
            "text": menu_text,
            "options": options,
            "routes": routes,
            "next": "finish",
        },
        "finish": {"type": "finish"},
    }
    needs_escalate = False
    for i, branch in enumerate(branches):
        label = str(branch.get("label") or branch.get("option") or "").strip()
        reply = str(branch.get("reply") or branch.get("text") or "").strip()
        escalate = bool(branch.get("escalate"))
        if not label or not reply:
            raise HTTPException(422, "Каждая ветка: label и reply обязательны")
        if label in options:
            raise HTTPException(422, "Повтор варианта в меню")
        node_id = f"branch_{i}"
        options.append(label)
        routes[label] = node_id
        if escalate:
            needs_escalate = True
            nodes[node_id] = {"type": "text", "text": reply, "next": "escalate_msg"}
        else:
            nodes[node_id] = {"type": "text", "text": reply, "next": "finish"}
    if needs_escalate:
        nodes["escalate_msg"] = {"type": "text", "text": escalate_text, "next": "escalate_task"}
        nodes["escalate_task"] = {
            "type": "task",
            "text": "Эскалация: ответить в CRM inbox",
            "next": "finish",
        }
    return {"start": "menu", "nodes": nodes}


def _create_funnel(db, *, admin, pid, name, connection_ids, graph, publish: bool, wizard: str):
    try:
        validate_bindings(db, graph, connection_ids)
    except ValueError as error:
        raise HTTPException(422, str(error))
    funnel = Funnel(
        id=uid(),
        name=name,
        connection_ids=connection_ids,
        draft=graph,
        project_id=pid,
    )
    db.add(funnel)
    db.flush()
    event(db, "funnel.created", actor_id=admin.telegram_id, funnel=funnel.id, wizard=wizard)
    audit(db, admin, f"ops.wizard.{wizard}", funnel_id=funnel.id)
    version_id = None
    if publish:
        version = Version(id=uid(), funnel_id=funnel.id, graph=graph)
        db.add(version)
        funnel.published_version_id = version.id
        version_id = version.id
        event(db, "funnel.published", actor_id=admin.telegram_id, funnel=funnel.id, version=version.id)
    return funnel, version_id


@router.post("/projects/{pid}/wizard/channel-dm")
def wizard_channel_dm(pid: str, body: ChannelDmWizard, admin=Depends(ops_actor)):
    wait_sec = _wait_seconds(body.wait_days, body.wait_seconds)
    graph = _channel_dm_graph(
        wait_sec=wait_sec,
        dm_text=body.dm_text,
        dm_connection_id=body.dm_connection_id,
    )
    connection_ids = list(dict.fromkeys([body.channel_connection_id, body.dm_connection_id]))
    try:
        validate_graph(graph)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error))
    with Session.begin() as db:
        project_access(db, admin, pid)
        for cid in connection_ids:
            conn = db.get(Connection, cid)
            if not conn or conn.project_id != pid:
                raise HTTPException(404, "Подключение недоступно")
            if admin.role != "owner" and cid not in (admin.connection_ids or []):
                raise HTTPException(403, "Подключение недоступно")
        funnel, version_id = _create_funnel(
            db,
            admin=admin,
            pid=pid,
            name=body.name,
            connection_ids=connection_ids,
            graph=graph,
            publish=True,
            wizard="channel_dm",
        )
        # Entry on the channel bot: chat_member join starts this funnel.
        # Deactivate prior channel_join entries on this connection so one automation wins.
        for old in db.scalars(
            select(Entry).where(Entry.connection_id == body.channel_connection_id, Entry.active.is_(True))
        ):
            if (old.attribution or {}).get("trigger") == "channel_join":
                old.active = False
        token = secrets.token_urlsafe(18)
        entry = Entry(
            token=token,
            funnel_id=funnel.id,
            connection_id=body.channel_connection_id,
            attribution={
                "trigger": "channel_join",
                "dm_connection_id": body.dm_connection_id,
                "wizard": "channel_dm",
            },
            active=True,
        )
        db.add(entry)
        channel = db.get(Connection, body.channel_connection_id)
        channel.config = {
            **(channel.config or {}),
            "channel_join_entry": token,
        }
        flag_modified(channel, "config")
        event(
            db,
            "entry.created",
            body.channel_connection_id,
            actor_id=admin.telegram_id,
            token=token,
            trigger="channel_join",
        )
        return {
            "id": funnel.id,
            "revision": funnel.revision,
            "wait_seconds": wait_sec,
            "version_id": version_id,
            "entry_token": token,
            "graph": graph,
            "dm_connection_id": body.dm_connection_id,
            "channel_connection_id": body.channel_connection_id,
            "note": "Бот канала должен быть админом; webhook includes chat_member. DM уходит через handoff → dm_connection_id.",
            "editor_hint": "Правки узлов — в разделе Сценарии или Mini App editor.",
        }


@router.post("/projects/{pid}/wizard/support")
def wizard_support(pid: str, body: SupportWizard, admin=Depends(ops_actor)):
    graph = _support_graph(
        menu_text=body.menu_text,
        branches=body.branches,
        escalate_text=body.escalate_text,
    )
    try:
        validate_graph(graph)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error))
    with Session.begin() as db:
        project_access(db, admin, pid)
        conn = db.get(Connection, body.connection_id)
        if not conn or conn.project_id != pid:
            raise HTTPException(404, "Подключение недоступно")
        if admin.role != "owner" and body.connection_id not in (admin.connection_ids or []):
            raise HTTPException(403, "Подключение недоступно")
        funnel, version_id = _create_funnel(
            db,
            admin=admin,
            pid=pid,
            name=body.name,
            connection_ids=[body.connection_id],
            graph=graph,
            publish=body.publish,
            wizard="support",
        )
        return {
            "id": funnel.id,
            "revision": funnel.revision,
            "version_id": version_id,
            "graph": graph,
            "crm_inbox_url": f"{portals.PORTAL_URLS['crm']}/crm/?section=inbox&project={pid}",
            "note": "Эскалация создаёт task; ответ менеджера — в CRM Диалоги. Pause run: POST /api/messages.",
        }
