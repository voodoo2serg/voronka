"""CRM contacts, deals, tasks, import/export and dialog inbox."""
import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select, or_

from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner
from app.crm_models import Profile, Deal, Activity, WorkItem, ImportBatch, Order, Product
from app.crm_service import (
    uid, STAGES, project_access, get_profile, get_deal, audit, normalize_email, normalize_phone,
    parse_csv, create_profile, balance, safe_csv_cell, assigned_profile_filter, manager_scope,
    project_ids,
)
from app.db import Admin, Project, Connection, Identity, Run, Event

router = APIRouter()


class ProfileBody(Body):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(default="", max_length=250)
    phone: str = Field(default="", max_length=80)
    company: str = Field(default="", max_length=200)
    source: str = Field(default="manual", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=30)
    extra: dict = Field(default_factory=dict)
    owner_id: int | None = None
    stopped: bool = False


class ProfileUpdate(ProfileBody):
    revision: int = Field(ge=1)


def validate_profile_values(db, values, project_id):
    try:
        values["email"] = normalize_email(values["email"])
        values["phone"] = normalize_phone(values["phone"])
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if values.get("owner_id") is not None:
        a = must(db.get(Admin, values["owner_id"]))
        if not a.active or (a.role != "owner" and project_id not in project_ids(db, a)):
            raise HTTPException(422, "Сотрудник не назначен в проект")
    if len(json.dumps(values.get("extra", {}))) > 20000 or any(len(t) > 80 for t in values.get("tags", [])):
        raise HTTPException(422, "Слишком много дополнительных данных")
    return values


@router.get("/projects/{pid}/contacts")
def contacts(pid: str, q: str = "", tag: str = "", offset: int = 0, limit: int = 100, admin=Depends(crm_actor)):
    if not 0 <= offset or not 1 <= limit <= 500:
        raise HTTPException(422, "Некорректная страница")
    with Session() as db:
        project_access(db, admin, pid)
        query = select(Profile).where(Profile.project_id == pid, assigned_profile_filter(admin))
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(or_(*[field.ilike(f"%{escaped}%", escape="\\") for field in (Profile.name, Profile.email, Profile.phone, Profile.company)]))
        rows = list(db.scalars(query.order_by(Profile.created_at.desc(), Profile.id)))
        if tag:
            rows = [r for r in rows if tag in r.tags]
        result = []
        for row in rows[offset:offset+limit]:
            last = db.scalar(select(Activity).where(Activity.profile_id == row.id).order_by(Activity.created_at.desc(), Activity.id.desc()))
            linked = select(Identity.connection_id).join(Connection).where(Identity.contact_id == row.contact_id, Connection.project_id == pid)
            event = db.scalar(select(Event).where(Event.contact_id == row.contact_id, Event.connection_id.in_(linked),
                              Event.kind.in_(["message.received", "message.sent", "manager.message_queued"])).order_by(Event.created_at.desc()).limit(1))
            summary = encode(last) if last else None
            if event and (not last or event.created_at.replace(tzinfo=timezone.utc) > last.created_at.replace(tzinfo=timezone.utc)):
                summary = {"created_at": event.created_at, "outcome": {"message.received": "Получено сообщение", "message.sent": "Платформа приняла сообщение", "manager.message_queued": "Ответ менеджера в очереди"}[event.kind]}
            result.append({**encode(row), "last_contact": summary})
        return {"items": result, "total": len(rows)}


@router.post("/projects/{pid}/contacts")
def add_contact(pid: str, body: ProfileBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        values = validate_profile_values(db, body.model_dump(), pid)
        row = create_profile(db, pid, values)
        owner = body.owner_id
        if manager_scope(admin):
            owner = admin.telegram_id
        row.owner_id, row.stopped = owner, body.stopped
        audit(db, admin, "contact.created", project_id=pid, profile_id=row.id)
        return encode(row)


@router.put("/contacts/{profile_id}")
def update_contact(profile_id: str, body: ProfileUpdate, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = get_profile(db, admin, profile_id)
        db.refresh(row, with_for_update=True)
        if row.revision != body.revision:
            raise HTTPException(409, "Карточка уже изменена. Обновите страницу")
        values = body.model_dump(exclude={"revision"})
        if manager_scope(admin):
            values["owner_id"] = row.owner_id
        validate_profile_values(db, values, row.project_id)
        for key, value in values.items():
            setattr(row, key, value)
        if row.stopped:
            from app.runtime import cancel_run
            identities = select(Identity.id).join(Connection).where(Identity.contact_id == row.contact_id, Connection.project_id == row.project_id)
            for run in db.scalars(select(Run).where(Run.identity_id.in_(identities), Run.status.in_(["active", "waiting", "delayed", "paused", "completed"]))):
                cancel_run(db, run)
        row.revision += 1
        audit(db, admin, "contact.updated", profile_id=row.id)
        return encode(row)


@router.get("/contacts/{profile_id}")
def contact_detail(profile_id: str, admin=Depends(crm_actor)):
    with Session() as db:
        row = get_profile(db, admin, profile_id)
        identities = list(db.scalars(select(Identity).join(Connection).where(Identity.contact_id == row.contact_id, Connection.project_id == row.project_id)))
        events = list(db.scalars(select(Event).where(Event.contact_id == row.contact_id, Event.connection_id.in_([i.connection_id for i in identities])).order_by(Event.created_at.desc()).limit(100)))
        return {**encode(row), "activities": [encode(x) for x in db.scalars(select(Activity).where(Activity.profile_id == row.id).order_by(Activity.created_at.desc()))],
                "deals": [encode(x) for x in db.scalars(select(Deal).where(Deal.profile_id == row.id))],
                "tasks": [encode(x) for x in db.scalars(select(WorkItem).where(WorkItem.profile_id == row.id))],
                "orders": [{**encode(o), **balance(db, o)} for o in db.scalars(select(Order).where(Order.profile_id == row.id))],
                "identities": [encode(i) for i in identities], "events": [encode(e) for e in events]}


class ActivityBody(Body):
    body: str = Field(min_length=1, max_length=20000)
    outcome: str = Field(default="", max_length=200)
    channel: Literal["manual", "phone", "email", "telegram", "max", "vk", "meeting"] = "manual"


@router.post("/contacts/{profile_id}/activities")
def add_activity(profile_id: str, body: ActivityBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        get_profile(db, admin, profile_id)
        row = Activity(id=uid(), profile_id=profile_id, actor_id=admin.telegram_id, **body.model_dump())
        db.add(row); db.flush()
        return encode(row)


class DealBody(Body):
    profile_id: str
    title: str = Field(min_length=1, max_length=250)
    product_id: str | None = None
    stage: str = "new"
    amount_minor: int = Field(default=0, ge=0, le=10**12)
    next_action: str = Field(default="", max_length=500)
    due_at: datetime | None = None
    owner_id: int | None = None


@router.get("/projects/{pid}/deals")
def deals(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        query = select(Deal).where(Deal.project_id == pid)
        if manager_scope(admin):
            query = query.join(Profile, Profile.id == Deal.profile_id).where(
                or_(Deal.owner_id == admin.telegram_id, Profile.owner_id == admin.telegram_id)
            )
        return [encode(x) for x in db.scalars(query.order_by(Deal.created_at.desc()))]


@router.post("/projects/{pid}/deals")
def add_deal(pid: str, body: DealBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        if get_profile(db, admin, body.profile_id).project_id != pid or body.stage not in STAGES:
            raise HTTPException(422, "Проект клиента или этап неверен")
        if body.product_id and must(db.get(Product, body.product_id)).project_id != pid:
            raise HTTPException(404, "Продукт недоступен")
        owner = body.owner_id
        if manager_scope(admin):
            owner = admin.telegram_id
        elif body.owner_id:
            a = must(db.get(Admin, body.owner_id))
            project_access(db, a, pid)
            if not a.active:
                raise HTTPException(422, "Сотрудник отключён")
        row = Deal(id=uid(), project_id=pid, profile_id=body.profile_id, title=body.title, product_id=body.product_id,
                   stage=body.stage, amount_minor=body.amount_minor, next_action=body.next_action, due_at=body.due_at, owner_id=owner)
        db.add(row); db.flush()
        audit(db, admin, "deal.created", deal_id=row.id)
        return encode(row)


class DealMove(Body):
    stage: str
    revision: int = Field(ge=1)
    next_action: str = Field(default="", max_length=500)
    due_at: datetime | None = None


@router.patch("/deals/{did}")
def move_deal(did: str, body: DealMove, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = get_deal(db, admin, did, for_update=True)
        if row.revision != body.revision:
            raise HTTPException(409, "Сделка уже изменена")
        if body.stage not in STAGES:
            raise HTTPException(422, "Неизвестный этап")
        previous = row.stage
        row.stage, row.next_action, row.due_at = body.stage, body.next_action, body.due_at
        row.revision += 1
        audit(db, admin, "deal.moved", deal_id=did, previous=previous, stage=body.stage)
        return encode(row)


class WorkBody(Body):
    title: str = Field(min_length=1, max_length=300)
    kind: Literal["task", "session", "deliverable", "document", "reminder"] = "task"
    due_at: datetime | None = None
    deal_id: str | None = None


@router.post("/contacts/{profile_id}/tasks")
def add_work(profile_id: str, body: WorkBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        get_profile(db, admin, profile_id)
        if body.deal_id and must(db.get(Deal, body.deal_id)).profile_id != profile_id:
            raise HTTPException(404, "Сделка недоступна")
        row = WorkItem(id=uid(), profile_id=profile_id, assignee_id=admin.telegram_id, **body.model_dump())
        db.add(row); db.flush()
        return encode(row)


class WorkUpdate(Body):
    state: Literal["open", "done", "cancelled"]
    result: str = Field(default="", max_length=20000)


@router.patch("/tasks/{tid}")
def update_work(tid: str, body: WorkUpdate, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.get(WorkItem, tid))
        get_profile(db, admin, row.profile_id)
        row.state, row.result = body.state, body.result
        return encode(row)


@router.get("/projects/{pid}/tasks")
def work(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        query = select(WorkItem).join(Profile).where(Profile.project_id == pid, assigned_profile_filter(admin))
        return [encode(x) for x in db.scalars(query)]


@router.get("/projects/{pid}/inbox")
def inbox(
    pid: str,
    offset: int = 0,
    limit: int = 50,
    connection_id: str | None = None,
    owner_id: int | None = None,
    unread: bool = False,
    delivery_errors: bool = False,
    admin=Depends(crm_actor),
):
    """Operator inbox: filters by connection/owner, unread and Outbox errors (no Chatwoot)."""
    if not 0 <= offset or not 1 <= limit <= 200:
        raise HTTPException(422, "Некорректная страница")
    with Session() as db:
        project_access(db, admin, pid)
        query = select(Profile).where(Profile.project_id == pid, assigned_profile_filter(admin))
        if owner_id is not None:
            if manager_scope(admin) and owner_id != admin.telegram_id:
                raise HTTPException(403, "Менеджер видит только свои диалоги")
            query = query.where(Profile.owner_id == owner_id)
        profiles = list(db.scalars(query.order_by(Profile.created_at.desc())))
        items = []
        from app.db import Outbox
        for row in profiles:
            identities = list(db.scalars(select(Identity).join(Connection).where(
                Identity.contact_id == row.contact_id, Connection.project_id == pid, Connection.active.is_(True))))
            if connection_id:
                identities = [i for i in identities if i.connection_id == connection_id]
            if not identities:
                continue
            event = db.scalar(select(Event).where(
                Event.contact_id == row.contact_id,
                Event.connection_id.in_([i.connection_id for i in identities]),
                Event.kind.in_(["message.received", "message.sent", "manager.message_queued"]),
            ).order_by(Event.created_at.desc()).limit(1))
            unread_hit = bool(event and event.kind == "message.received")
            if unread and not unread_hit:
                continue
            err_count = db.scalar(
                select(Outbox.id).where(
                    Outbox.identity_id.in_([i.id for i in identities]),
                    Outbox.status.in_(["failed", "unknown"]),
                ).limit(1)
            )
            if delivery_errors and not err_count:
                continue
            delivery = list(db.scalars(
                select(Outbox).where(Outbox.identity_id.in_([i.id for i in identities]))
                .order_by(Outbox.updated_at.desc()).limit(10)
            ))
            runs = list(db.scalars(select(Run).where(Run.identity_id.in_([i.id for i in identities])).order_by(Run.id.desc()).limit(5)))
            timeline = list(db.scalars(select(Activity).where(Activity.profile_id == row.id).order_by(Activity.created_at.desc()).limit(20)))
            items.append({
                **encode(row),
                "identities": [encode(i) for i in identities],
                "last_message": encode(event) if event else None,
                "unread": unread_hit,
                "delivery_errors": bool(err_count),
                "delivery": [encode(d) for d in delivery],
                "runs": [{"id": r.id, "status": r.status, "identity_id": r.identity_id, "current": r.current} for r in runs],
                "timeline": [encode(t) for t in timeline],
            })
        items.sort(key=lambda x: (x["last_message"]["created_at"] if x["last_message"] else x["created_at"]), reverse=True)
        return {"items": items[offset:offset+limit], "total": len(items)}


class AssignBody(Body):
    owner_id: int


@router.post("/contacts/{profile_id}/assign")
def assign_owner(profile_id: str, body: AssignBody, admin=Depends(crm_actor)):
    """Assign dialog/thread owner to a manager within project ACL."""
    with Session.begin() as db:
        row = get_profile(db, admin, profile_id)
        if admin.role == "manager" and row.owner_id not in {None, admin.telegram_id}:
            raise HTTPException(403, "Нельзя переназначить чужой диалог")
        target = must(db.get(Admin, body.owner_id))
        if not target.active:
            raise HTTPException(422, "Сотрудник отключён")
        if target.role != "owner" and row.project_id not in project_ids(db, target):
            raise HTTPException(422, "Сотрудник вне проекта")
        if admin.role == "manager" and body.owner_id != admin.telegram_id:
            raise HTTPException(403, "Менеджер может назначить только себя")
        row.owner_id = body.owner_id
        row.revision += 1
        audit(db, admin, "contact.assigned", profile_id=profile_id, owner_id=body.owner_id)
        return encode(row)


@router.post("/projects/{pid}/imports/preview")
async def preview_import(pid: str, file: UploadFile = File(...), mapping: str = Form("{}"), admin=Depends(crm_actor)):
    raw = await file.read(5_000_001)
    try:
        fields = json.loads(mapping)
        if not isinstance(fields, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, "Сопоставление должно быть JSON-объектом")
    headers, fields, rows = parse_csv(raw, fields)
    fingerprint = hashlib.sha256(raw + json.dumps(fields, sort_keys=True).encode()).hexdigest()
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = db.scalar(select(ImportBatch).where(ImportBatch.project_id == pid, ImportBatch.fingerprint == fingerprint))
        if not row:
            row = ImportBatch(id=uid(), project_id=pid, fingerprint=fingerprint, mapping=fields, rows=rows, actor_id=admin.telegram_id)
            db.add(row); db.flush()
        existing = list(db.scalars(select(Profile).where(Profile.project_id == pid)))
        seen_email = {x.email for x in existing if x.email}
        seen_phone = {x.phone for x in existing if x.phone}
        planned = []
        for entry in rows:
            values = entry["data"]
            duplicate = bool(values.get("email") and values["email"] in seen_email or values.get("phone") and values["phone"] in seen_phone)
            planned.append({**entry, "status": "error" if entry["error"] else "possible_duplicate" if duplicate else "new"})
            if not entry["error"]:
                if values.get("email"): seen_email.add(values["email"])
                if values.get("phone"): seen_phone.add(values["phone"])
        return {"id": row.id, "state": row.state, "headers": headers, "mapping": fields, "rows": planned[:30],
                "possible_duplicates": sum(x["status"] == "possible_duplicate" for x in planned),
                "total": len(rows), "errors": sum(bool(x["error"]) for x in rows), "report": row.report}


@router.post("/imports/{bid}/commit")
def commit_import(bid: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        batch = must(db.scalar(select(ImportBatch).where(ImportBatch.id == bid).with_for_update()))
        project_access(db, admin, batch.project_id)
        if batch.state == "done":
            return batch.report
        # Serialize batches within a project: match decisions must not race.
        db.scalar(select(Project).where(Project.id == batch.project_id).with_for_update())
        report = {"created": 0, "skipped": 0, "errors": [], "profile_ids": []}
        for entry in batch.rows or []:
            if entry["error"]:
                report["errors"].append({"line": entry["line"], "error": entry["error"]}); continue
            values = entry["data"]
            possible = []
            if values.get("email"):
                possible.append(Profile.email == values["email"])
            if values.get("phone"):
                possible.append(Profile.phone == values["phone"])
            match = list(db.scalars(select(Profile).where(Profile.project_id == batch.project_id, or_(*possible)))) if possible else []
            if match:
                report["skipped"] += 1
                report["errors"].append({"line": entry["line"], "error": "Возможный дубль: пропущен без слияния/перезаписи"}); continue
            values = {**values, "source": values.get("source") or "csv", "extra": {**entry["extra"],
                      "import_line": entry["line"], "import_batch": bid,
                      "imported_identity": {k: values.get(k, "") for k in ("external_id", "username", "connection_id")}}}
            row = create_profile(db, batch.project_id, values)
            if manager_scope(admin):
                row.owner_id = admin.telegram_id
            if values.get("notes"):
                db.add(Activity(id=uid(), profile_id=row.id, actor_id=admin.telegram_id, kind="import", body=values["notes"]))
            report["created"] += 1
            report["profile_ids"].append(row.id)
        # Drop full CSV payload after successful commit; fingerprint+report keep replay semantics.
        batch.state, batch.report, batch.rows = "done", report, []
        audit(db, admin, "import.completed", batch_id=bid, created=report["created"])
        return report


@router.get("/projects/{pid}/export")
def export_contacts(pid: str, q: str = "", admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["id", "name", "email", "phone", "company", "source", "tags"])
        rows = db.scalars(select(Profile).where(Profile.project_id == pid, assigned_profile_filter(admin)).order_by(Profile.id))
        for row in rows:
            if q and q.casefold() not in " ".join([row.name, row.email, row.phone, row.company]).casefold():
                continue
            writer.writerow([safe_csv_cell(v) for v in [row.id, row.name, row.email, row.phone, row.company, row.source, ", ".join(row.tags)]])
        audit(db, admin, "export", project_id=pid)
        return Response("\ufeff" + stream.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="clients.csv"'})


@router.post("/projects/{pid}/sync-contacts")
def sync_contacts(pid: str, admin=Depends(crm_actor)):
    from app.crm_service import ensure_profile
    with Session.begin() as db:
        project_access(db, admin, pid)
        db.scalar(select(Project).where(Project.id == pid).with_for_update())
        count = 0
        for identity in db.scalars(select(Identity).join(Connection).where(Connection.project_id == pid)):
            row = ensure_profile(db, identity, pid)
            if manager_scope(admin) and row.owner_id is None:
                row.owner_id = admin.telegram_id
            count += 1
        audit(db, admin, "contacts.synced", project_id=pid, identities=count)
        return {"identities": count}


class RagBody(Body):
    question: str = Field(min_length=1, max_length=4000)
    connection_id: str | None = None
    provider: Literal["raga", "openrouter", "ollama"] | None = None


@router.post("/contacts/{profile_id}/assistant")
def assistant(profile_id: str, body: RagBody, admin=Depends(crm_actor)):
    """Draft-only AI assistant (RAGA / OpenRouter / Ollama). Never auto-sends."""
    import os
    from app import ai_providers
    with Session() as db:
        profile = get_profile(db, admin, profile_id)
        resolved = ai_providers.resolve_rules(db, project_id=profile.project_id, connection_id=body.connection_id)
        context = {
            "name": profile.name,
            "company": profile.company,
            "deals": [
                {"title": d.title, "stage": d.stage, "next_action": d.next_action}
                for d in db.scalars(select(Deal).where(Deal.profile_id == profile_id))
            ],
        }
        project_id = profile.project_id
    provider = body.provider or resolved["provider"]
    status = ai_providers.env_status()
    configured = {
        "raga": status["raga"]["configured"],
        "openrouter": status["openrouter"]["configured"],
        "ollama": status["ollama"]["configured"],
    }
    if not configured.get(provider):
        raise HTTPException(503, "Для проекта не настроен RAGA Gateway и alias модели" if provider == "raga" else f"Провайдер {provider} не настроен")
    model = resolved["model"]
    if provider == "raga" and not model:
        try:
            aliases = json.loads(os.getenv("RAG_PROJECT_MODELS", "{}") or "{}")
        except ValueError:
            raise HTTPException(503, "Ошибка настройки RAGA")
        model = aliases.get(project_id, "")
        if not model:
            raise HTTPException(503, "Для проекта не настроен RAGA Gateway и alias модели")
    try:
        answer = ai_providers.complete_draft(
            provider=provider,
            model=model,
            system_rules=resolved["system_rules"],
            question=body.question,
            context=context,
            project_id=project_id,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "not_configured" in msg:
            raise HTTPException(503, "Для проекта не настроен RAGA Gateway и alias модели" if provider == "raga" else msg)
        raise HTTPException(503, msg)
    except Exception:
        raise HTTPException(502, "RAGA недоступна или вернула неверный ответ" if provider == "raga" else "ИИ недоступен или вернул неверный ответ")
    with Session.begin() as db:
        get_profile(db, admin, profile_id)
        db.add(Activity(id=uid(), profile_id=profile_id, actor_id=admin.telegram_id, kind="ai_draft", outcome="Черновик ИИ, не отправлен", body=answer[:20000]))
    return {"draft": answer, "sent": False, "provider": provider}
