"""CRM connection hub: webhook/media readiness without leaking token values (VOR-1, VOR-10)."""
from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.capabilities import platform_capabilities
from app.crm_deps import Session, crm_actor, encode, must, staff_owner
from app.crm_service import project_access
from app.db import Connection, Outbox, Event, Asset
from app import media as media_mod

router = APIRouter()


def _connection_visible(admin, connection: Connection) -> bool:
    if admin.role == "owner":
        return True
    return connection.id in (admin.connection_ids or [])


@router.get("/projects/{pid}/connections/hub")
def connections_hub(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        root = media_mod.ROOT
        root.mkdir(parents=True, exist_ok=True)
        media_writable = os.access(root, os.W_OK)
        free = shutil.disk_usage(root).free
        rows = []
        for connection in db.scalars(select(Connection).where(Connection.project_id == pid)):
            if not _connection_visible(admin, connection):
                continue
            last_err = db.scalar(
                select(Outbox.error)
                .where(Outbox.connection_id == connection.id, Outbox.status.in_(["failed", "unknown"]))
                .order_by(Outbox.updated_at.desc())
                .limit(1)
            )
            last_event = db.scalar(
                select(Event)
                .where(Event.connection_id == connection.id, Event.kind.like("connection.%"))
                .order_by(Event.created_at.desc())
                .limit(1)
            )
            failed = db.scalar(
                select(func.count()).select_from(Outbox).where(
                    Outbox.connection_id == connection.id, Outbox.status.in_(["failed", "unknown"])
                )
            ) or 0
            pending = db.scalar(
                select(func.count()).select_from(Outbox).where(
                    Outbox.connection_id == connection.id, Outbox.status == "pending"
                )
            ) or 0
            token_present = bool(connection.token_env and os.environ.get(connection.token_env))
            secret_present = bool(connection.secret_env and os.environ.get(connection.secret_env))
            # Never return env values — only names + presence.
            item = {
                "id": connection.id,
                "name": connection.name,
                "platform": connection.platform,
                "role": connection.role,
                "active": connection.active,
                "token_env": connection.token_env if admin.role == "owner" else None,
                "secret_env": connection.secret_env if admin.role == "owner" else None,
                "token_present": token_present if admin.role in {"owner", "editor"} else None,
                "secret_present": secret_present if admin.role in {"owner", "editor"} else None,
                "webhook_preflight": {
                    "ok": bool(connection.active and (connection.platform == "email" or (token_present and secret_present))),
                    "active": connection.active,
                    "credentials_present": connection.platform == "email" or (token_present and secret_present),
                },
                "last_error": last_err,
                "last_connection_event": encode(last_event) if last_event else None,
                "media_writable": media_writable,
                "media_free_bytes": free,
                "outbox_failed_or_unknown": failed,
                "outbox_pending": pending,
                "capabilities": platform_capabilities(connection.platform),
            }
            if admin.role == "manager":
                # Managers: no env names, no secret presence details.
                item["token_env"] = None
                item["secret_env"] = None
                item["token_present"] = None
                item["secret_present"] = None
            rows.append(item)
        return {"items": rows, "media_writable": media_writable, "media_free_bytes": free}


@router.get("/projects/{pid}/media-assets")
def project_media_assets(pid: str, admin=Depends(crm_actor)):
    """List mediabank assets usable for campaign/broadcast on project connections."""
    with Session() as db:
        project_access(db, admin, pid)
        connection_ids = {
            c.id
            for c in db.scalars(select(Connection).where(Connection.project_id == pid))
            if _connection_visible(admin, c)
        }
        items = []
        for asset in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(200)):
            overlap = [cid for cid in (asset.connection_ids or []) if cid in connection_ids]
            if not overlap and admin.role != "owner":
                continue
            if admin.role == "owner" and asset.connection_ids and not overlap:
                # Owner may still see orphaned assets tied to other projects' bots.
                if not any(cid for cid in asset.connection_ids if db.get(Connection, cid) and db.get(Connection, cid).project_id == pid):
                    if asset.connection_ids:
                        continue
            blocker = None
            if not asset.local_path and not asset.telegram_refs:
                blocker = "Нет локального файла и file_id — рассылка невозможна"
            items.append({
                "id": asset.id,
                "name": asset.name,
                "kind": asset.kind,
                "mime": asset.mime,
                "has_local_file": bool(asset.local_path),
                "telegram_ref_connections": list((asset.telegram_refs or {}).keys()) if admin.role != "manager" else [],
                "connection_ids": overlap if admin.role != "owner" else (asset.connection_ids or []),
                "blocker": blocker,
                "created_at": asset.created_at,
            })
        return {"items": items, "admin_bot_media_policy": {
            "decision": "user_bot_import_sufficient",
            "note": "Приём кружков через user-bot forward достаточен; отдельный admin-бот опционален для UX (не вторая медиасистема).",
        }}
