"""Shared CRM HTTP helpers. Routers read Session/auth via this module so tests can patch once."""
import os
from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app import auth
from app.auth import actor, CRM_SESSION_COOKIE
from app.db import Session


def crm_actor(admin=Depends(actor)):
    if admin.role not in {"owner", "editor", "manager"}:
        raise HTTPException(403, "Нет доступа к CRM")
    return admin


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def encode(row):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def must(row):
    if row is None:
        raise HTTPException(404, "Не найдено")
    return row


def staff_owner(admin):
    if admin.role != "owner":
        raise HTTPException(403, "Требуется владелец")


def throttle(request, purpose, maximum=15):
    key = f"crm-limit:{purpose}:{request.client.host}"
    count = auth.cache.incr(key)
    if count == 1:
        auth.cache.expire(key, 300)
    if count > maximum:
        raise HTTPException(429, "Слишком много попыток, попробуйте позже")


def set_session_cookie(response: Response, token: str):
    secure = os.getenv("CRM_COOKIE_SECURE", "1") != "0"
    response.set_cookie(
        key=CRM_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=8 * 3600,
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(key=CRM_SESSION_COOKIE, path="/")


def robokassa_ready():
    login = os.getenv("ROBOKASSA_LOGIN")
    test = os.getenv("ROBOKASSA_TEST", "1") == "1"
    if test:
        return bool(login and os.getenv("ROBOKASSA_TEST_PASSWORD1") and os.getenv("ROBOKASSA_TEST_PASSWORD2"))
    return bool(
        login
        and os.getenv("ROBOKASSA_PASSWORD1")
        and os.getenv("ROBOKASSA_PASSWORD2")
        and os.getenv("ROBOKASSA_LIVE_READY") == "1"
    )
