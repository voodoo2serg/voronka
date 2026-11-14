"""Ops portal auth: owner/editor only (managers stay in CRM)."""
from fastapi import Depends, HTTPException

from app.auth import actor


def ops_actor(admin=Depends(actor)):
    if admin.role not in {"owner", "editor"}:
        raise HTTPException(403, "Нет доступа к порталу Voronka ops")
    return admin
