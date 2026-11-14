"""Ops chrome, identities for messenger audience, preflight snapshot."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app import portals
from app.crm_deps import Session
from app.crm_models import Profile
from app.crm_service import project_access, ensure_profile
from app.db import Connection, Identity
from app.ops_routers.deps import ops_actor

router = APIRouter()


@router.get("/portals")
def portals_chrome(admin=Depends(ops_actor)):
    return {
        "host_aware": True,
        "voronka_host": portals.PORTAL_HOSTS["voronka"],
        "ops_path": "/ops/",
        "mini_app_path": "/",
        "links": portals.portal_chrome(admin.role),
        "crm_url": portals.PORTAL_URLS["crm"] + "/",
        "role": admin.role,
        "language": ["Боты", "Сценарии", "Рассылки", "Поддержка", "AI", "Каналы"],
    }


@router.get("/projects/{pid}/identities")
def project_identities(pid: str, connection_id: str | None = None, admin=Depends(ops_actor)):
    """Messenger audience for instant broadcast: Identity rows (and linked Profile)."""
    with Session() as db:
        project_access(db, admin, pid)
        q = (
            select(Identity, Connection, Profile)
            .join(Connection, Connection.id == Identity.connection_id)
            .outerjoin(
                Profile,
                (Profile.contact_id == Identity.contact_id) & (Profile.project_id == pid),
            )
            .where(Connection.project_id == pid, Connection.platform != "email")
        )
        if connection_id:
            q = q.where(Identity.connection_id == connection_id)
        items = []
        for identity, connection, profile in db.execute(q).all():
            if admin.role != "owner" and connection.id not in (admin.connection_ids or []):
                continue
            items.append(
                {
                    "id": identity.id,
                    "name": identity.name or (profile.name if profile else "") or identity.external_user_id,
                    "connection_id": connection.id,
                    "connection_name": connection.name,
                    "platform": connection.platform,
                    "external_user_id": identity.external_user_id,
                    "profile_id": profile.id if profile else None,
                    "stopped": bool(profile.stopped) if profile else False,
                }
            )
        return {"items": items, "total": len(items)}


@router.post("/projects/{pid}/identities/{iid}/ensure-profile")
def ensure_identity_profile(pid: str, iid: str, admin=Depends(ops_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        identity = db.get(Identity, iid)
        if not identity:
            raise HTTPException(404, "Identity не найден")
        connection = db.get(Connection, identity.connection_id)
        if not connection or connection.project_id != pid:
            raise HTTPException(404, "Identity другого проекта")
        if admin.role != "owner" and connection.id not in (admin.connection_ids or []):
            raise HTTPException(403, "Подключение недоступно")
        profile = ensure_profile(db, identity, pid)
        return {"profile_id": profile.id, "identity_id": identity.id}
