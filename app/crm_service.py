"""Deterministic CRM rules; no network requests inside money transactions."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, func, or_
from fastapi import HTTPException
from app.db import Admin, Contact, Connection, Identity, Project, Event, now
from app.crm_models import Membership, Profile, Payment, Order

KINDS = {"audit": "Коммуникационный аудит", "mentoring": "Менторство", "consultation": "Консультация",
         "strategy": "Стратегическая сессия", "course": "Курс", "course_creation": "Разработка курса",
         "session": "Обучающая сессия", "seminar": "Семинар", "club": "Клуб", "archive": "Архив"}
STAGES = {"new": "Новый запрос", "qualified": "Уточнение задачи", "proposal": "Предложение",
          "negotiation": "Согласование", "payment": "Ожидает оплаты", "delivery": "В работе",
          "won": "Завершено", "lost": "Отказ"}


def uid():
    from uuid import uuid4
    return str(uuid4())


def password_hash(password):
    if len(password) < 12 or len(password) > 200:
        raise HTTPException(422, "Пароль: от 12 до 200 символов")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def password_ok(password, encoded):
    try:
        algorithm, salt, digest = encoded.split("$")
        if algorithm != "scrypt" or len(password) > 200:
            return False
        value = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(value, digest)
    except (ValueError, TypeError):
        return False


def project_ids(db, admin):
    if admin.role == "owner":
        return list(db.scalars(select(Project.id)))
    explicit = list(db.scalars(select(Membership.project_id).where(Membership.admin_id == admin.telegram_id)))
    inherited = list(db.scalars(select(Connection.project_id).where(Connection.id.in_(admin.connection_ids), Connection.project_id.is_not(None))))
    return list(set(explicit + inherited))


def project_access(db, admin, project_id):
    if not db.get(Project, project_id) or project_id not in project_ids(db, admin):
        raise HTTPException(404, "Проект недоступен")


def manager_scope(admin):
    """Managers only see assigned clients/deals; owners and editors keep project scope."""
    return admin.role == "manager"


def profile_visible(admin, profile):
    if not manager_scope(admin):
        return True
    return profile.owner_id == admin.telegram_id


def deal_visible(admin, deal, profile=None):
    if not manager_scope(admin):
        return True
    if deal.owner_id == admin.telegram_id:
        return True
    if profile is not None:
        return profile.owner_id == admin.telegram_id
    return False


def assigned_profile_filter(admin):
    from sqlalchemy import true
    if not manager_scope(admin):
        return true()
    return Profile.owner_id == admin.telegram_id


def get_profile(db, admin, profile_id):
    row = db.get(Profile, profile_id)
    if row is None:
        raise HTTPException(404, "Клиент не найден")
    project_access(db, admin, row.project_id)
    if not profile_visible(admin, row):
        raise HTTPException(404, "Клиент не найден")
    return row


def get_deal(db, admin, deal_id, for_update=False):
    from app.crm_models import Deal
    query = select(Deal).where(Deal.id == deal_id)
    if for_update:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, "Не найдено")
    project_access(db, admin, row.project_id)
    profile = db.get(Profile, row.profile_id)
    if not deal_visible(admin, row, profile):
        raise HTTPException(404, "Не найдено")
    return row


def audit(db, admin, kind, **payload):
    db.add(Event(id=uid(), actor_id=admin.telegram_id, kind="crm." + kind, payload=payload))


def normalize_email(value):
    value = value.strip().lower()
    if value and (len(value) > 250 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)):
        raise ValueError("Некорректный email")
    return value


def normalize_phone(value):
    value = re.sub(r"[^\d+]", "", value.strip())
    if value and not re.fullmatch(r"\+?\d{7,15}", value):
        raise ValueError("Некорректный телефон")
    return value


def parse_csv(raw, mapping):
    if len(raw) > 5_000_000:
        raise HTTPException(413, "Максимум 5 МБ")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(422, "Не удалось определить кодировку")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or len(headers) > 80:
        raise HTTPException(422, "Повторяющиеся заголовки или больше 80 колонок")
    aliases = {"name": ["имя", "фио", "name", "клиент"], "email": ["email", "e-mail", "почта"],
               "phone": ["телефон", "phone"], "company": ["компания", "company"], "source": ["источник", "source"],
               "external_id": ["external_id", "user_id", "telegram_id", "max_id"], "connection_id": ["connection_id"],
               "username": ["username", "telegram", "max"], "notes": ["комментарий", "notes", "заметка"]}
    if not mapping:
        mapping = {field: h for field, names in aliases.items() for h in headers if h.strip().lower() in names}
    if set(mapping) - set(aliases) or any(v not in headers for v in mapping.values()):
        raise HTTPException(422, "Неизвестное поле или колонка сопоставления")
    rows = []
    for index, raw_row in enumerate(reader, start=2):
        if len(rows) >= 5000:
            raise HTTPException(422, "Одна партия: максимум 5000 строк")
        row = {field: (raw_row.get(column) or "").strip() for field, column in mapping.items()}
        error = ""
        try:
            row["email"] = normalize_email(row.get("email", ""))
            row["phone"] = normalize_phone(row.get("phone", ""))
            if not any(row.get(k) for k in ("name", "email", "phone", "external_id", "username")):
                raise ValueError("Нет данных клиента")
            if any(len(v) > 4000 for v in row.values()):
                raise ValueError("Слишком длинное значение")
            if len(row.get("name", "")) > 200 or len(row.get("company", "")) > 200:
                raise ValueError("Имя/компания: максимум 200 символов")
        except ValueError as exc:
            error = str(exc)
        rows.append({"line": index, "data": row, "error": error,
                     "extra": {k: v for k, v in raw_row.items() if k and k not in mapping.values()}})
    return headers, mapping, rows


def create_profile(db, project_id, values):
    contact = Contact(id=uid())
    db.add(contact)
    db.flush()
    row = Profile(id=uid(), contact_id=contact.id, project_id=project_id,
                  name=values.get("name") or values.get("email") or values.get("phone") or "Импортированный контакт",
                  email=values.get("email", ""), phone=values.get("phone", ""), company=values.get("company", ""),
                  source=values.get("source") or "manual", tags=values.get("tags", []), extra=values.get("extra", {}))
    db.add(row)
    db.flush()
    return row


def balance(db, order):
    rows = list(db.scalars(select(Payment).where(Payment.order_id == order.id)))
    paid = sum(p.amount_minor if p.kind == "payment" else -p.amount_minor for p in rows)
    remaining = max(0, order.total_minor - paid)
    overdue = 0
    total_due = 0
    for part in order.installments:
        if datetime.fromisoformat(part["due_at"]).astimezone(timezone.utc) <= now():
            total_due += part["amount_minor"]
    overdue = max(total_due - paid, 0)
    return {"paid_minor": paid, "balance_minor": remaining, "overdue_minor": overdue,
            "overpayment_minor": max(0, paid - order.total_minor), "fully_paid": remaining == 0}


def money_minor(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount * 100 != (amount * 100).to_integral_value():
            raise ValueError()
        return int(amount * 100)
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(422, "Некорректная сумма")


def safe_csv_cell(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r")) else value


def available_identity(db, profile, connection_id):
    connection = db.get(Connection, connection_id)
    if not connection or not connection.active or connection.project_id != profile.project_id:
        return None, "Подключение недоступно проекту"
    if profile.stopped:
        return None, "Отказ от сообщений"
    if connection.platform == "email":
        from types import SimpleNamespace
        if not profile.email or not profile.extra.get("email_allowed"):
            return None, "Нет email или не отмечено разрешение на рассылку"
        return SimpleNamespace(id=None, connection_id=connection.id, chat_id=profile.email), "Email разрешён сотрудником"
    identity = db.scalar(select(Identity).where(Identity.contact_id == profile.contact_id, Identity.connection_id == connection_id))
    if not identity:
        return None, "Нет подтверждённого диалога с этим подключением"
    # An imported ID is stored in Profile.extra, never as an active Identity.
    return identity, "Доступен существующий диалог"


def ensure_profile(db, identity, project_id):
    row = db.scalar(select(Profile).where(Profile.contact_id == identity.contact_id, Profile.project_id == project_id))
    if not row:
        row = Profile(id=uid(), contact_id=identity.contact_id, project_id=project_id,
                      name=identity.name or identity.external_user_id, source="bot")
        db.add(row); db.flush()
    return row
