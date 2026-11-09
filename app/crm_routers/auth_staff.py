"""CRM login, recovery and staff management."""
import hashlib
import os
import secrets
import smtplib
from datetime import timedelta, timezone
from email.message import EmailMessage
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import Field
from sqlalchemy import select, func

from app import auth
from app.crm_deps import Body, Session, crm_actor, encode, must, set_session_cookie, clear_session_cookie, staff_owner, throttle
from app.crm_models import Account, Membership, Recovery
from app.crm_service import uid, KINDS, STAGES, password_hash, password_ok, project_ids, project_access, audit, normalize_email
from app.db import Admin, Project, Connection, now

router = APIRouter()


class Login(Body):
    login: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    throttle(request, "login")
    with Session() as db:
        account = db.scalar(select(Account).where(Account.login == body.login.strip().lower()))
        # Use the same expensive KDF for missing users, to avoid a fast username oracle.
        dummy = "scrypt$00000000000000000000000000000000$" + "0" * 128
        valid = password_ok(body.password, account.password_hash if account else dummy)
        admin = db.get(Admin, account.admin_id) if account else None
        if not valid or not admin or not admin.active:
            raise HTTPException(401, "Неверный логин или пароль")
        token = auth.issue_session(admin.telegram_id)
        set_session_cookie(response, token)
        return {"token": token, "role": admin.role}


@router.post("/logout")
def logout(request: Request, response: Response, admin=Depends(crm_actor)):
    token = auth.read_session_token(request.headers.get("authorization", ""), request.cookies.get(auth.CRM_SESSION_COOKIE))
    if token:
        auth.cache.delete(auth.session_key(token))
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def me(admin=Depends(crm_actor)):
    with Session() as db:
        return {"id": admin.telegram_id, "role": admin.role, "projects": [encode(p) for p in db.scalars(select(Project).where(Project.id.in_(project_ids(db, admin))))],
                "product_kinds": KINDS, "stages": STAGES}


class StaffBody(Body):
    login: str = Field(min_length=3, max_length=120)
    password: str = Field(min_length=12, max_length=200)
    email: str = Field(default="", max_length=250)
    role: Literal["editor", "manager"] = "manager"
    project_ids: list[str] = Field(default_factory=list, max_length=100)


@router.get("/staff")
def staff(admin=Depends(crm_actor)):
    with Session() as db:
        ids = project_ids(db, admin)
        allowed_ids = list(db.scalars(select(Membership.admin_id).where(Membership.project_id.in_(ids))))
        q = select(Admin)
        if admin.role != "owner":
            q = q.where(Admin.telegram_id.in_(allowed_ids + [admin.telegram_id]))
        return [{"id": a.telegram_id, "role": a.role, "active": a.active,
                 "login": (db.scalar(select(Account.login).where(Account.admin_id == a.telegram_id)) or str(a.telegram_id))} for a in db.scalars(q)]


@router.post("/staff")
def add_staff(body: StaffBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    if body.role == "owner":
        raise HTTPException(422, "Владельца создаёт только create_crm_owner / OWNER_TELEGRAM_ID")
    with Session.begin() as db:
        if db.scalar(select(func.count()).select_from(Admin).where(Admin.active.is_(True))) >= 12:
            raise HTTPException(409, "Первая версия: до 12 активных сотрудников")
        for pid in body.project_ids:
            project_access(db, admin, pid)
        login_name = body.login.strip().lower()
        if db.scalar(select(Account).where(Account.login == login_name)):
            raise HTTPException(409, "Логин занят")
        aid = -(secrets.randbelow(2**50) + 1)
        connections = list(db.scalars(select(Connection.id).where(Connection.project_id.in_(body.project_ids))))
        db.add(Admin(telegram_id=aid, role=body.role, active=True, connection_ids=connections))
        db.flush()
        try:
            email = normalize_email(body.email)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        account = Account(id=uid(), admin_id=aid, login=login_name, password_hash=password_hash(body.password), email=email)
        db.add(account)
        for pid in set(body.project_ids):
            db.add(Membership(id=uid(), admin_id=aid, project_id=pid))
        audit(db, admin, "staff.created", admin_id=aid)
        return {"id": aid}


@router.post("/staff/{aid}/disable")
def disable_staff(aid: int, admin=Depends(crm_actor)):
    staff_owner(admin)
    if aid == admin.telegram_id:
        raise HTTPException(409, "Нельзя отключить себя")
    with Session.begin() as db:
        must(db.get(Admin, aid)).active = False
        audit(db, admin, "staff.disabled", admin_id=aid)
    auth.revoke_sessions(aid)
    return {"ok": True}


class RecoveryStart(Body):
    login: str = Field(min_length=1, max_length=120)
    purpose: Literal["reset", "verify"] = "reset"


def mail_code(email, code):
    host = os.getenv("SMTP_HOST")
    if not host or not os.getenv("SMTP_FROM"):
        raise HTTPException(503, "Почта не настроена; обратитесь к владельцу")
    message = EmailMessage()
    message["From"] = os.environ["SMTP_FROM"]
    message["To"] = email
    message["Subject"] = "Код доступа Соло-PR"
    message.set_content(f"Одноразовый код: {code}\nСрок действия — 10 минут.")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=15) as smtp:
        smtp.starttls()
        if os.getenv("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)


@router.post("/recovery/start")
def recovery_start(body: RecoveryStart, request: Request):
    throttle(request, "recovery", 5)
    if not os.getenv("SMTP_HOST") or not os.getenv("SMTP_FROM"):
        raise HTTPException(503, "Почта не настроена; обратитесь к владельцу")
    with Session.begin() as db:
        account = db.scalar(select(Account).where(Account.login == body.login.strip().lower()))
        if account and account.email and (body.purpose == "verify" or account.email_verified):
            code = f"{secrets.randbelow(100000000):08d}"
            for old in db.scalars(select(Recovery).where(Recovery.account_id == account.id, Recovery.purpose == body.purpose)):
                old.consumed = True
            digest = hashlib.sha256(f"{account.id}:{body.purpose}:{code}".encode()).hexdigest()
            db.add(Recovery(id=uid(), account_id=account.id, token_hash=digest, purpose=body.purpose, expires_at=now()+timedelta(minutes=10)))
            try:
                mail_code(account.email, code)
            except (OSError, smtplib.SMTPException):
                raise HTTPException(503, "Письмо не отправлено")
    return {"ok": True, "message": "Если аккаунт подходит для операции, код отправлен"}


class RecoveryFinish(RecoveryStart):
    code: str = Field(pattern=r"^\d{8}$")
    password: str = Field(default="", max_length=200)


@router.post("/recovery/finish")
def recovery_finish(body: RecoveryFinish, request: Request):
    throttle(request, "recovery-finish", 10)
    with Session.begin() as db:
        account = db.scalar(select(Account).where(Account.login == body.login.strip().lower()).with_for_update())
        digest = hashlib.sha256(f"{account.id if account else ''}:{body.purpose}:{body.code}".encode()).hexdigest()
        row = db.scalar(select(Recovery).where(Recovery.token_hash == digest).with_for_update())
        if not account or not row or row.consumed or row.expires_at.replace(tzinfo=timezone.utc) <= now():
            raise HTTPException(400, "Код недействителен")
        row.consumed = True
        if body.purpose == "verify":
            account.email_verified = True
        else:
            if not account.email_verified:
                raise HTTPException(400, "Email не подтверждён")
            account.password_hash = password_hash(body.password)
            auth.revoke_sessions(account.admin_id)
    return {"ok": True}
