import hashlib, hmac, json, os, re, secrets
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.db import Session, Admin, Connection, Destination, Funnel, Version, Entry, Inbox, Outbox, Event, Identity, Run, Task
from app.auth import actor, permit, allowed, validate_init_data, issue_session, cache
from app.channels import normalize
from app.graph import validate_graph, validate_bindings
from app.runtime import uid, event, advance, cancel_run, prompt
from app.media import router as media_router
from app.db import Asset, Contact, Destination, now
from datetime import timedelta

app = FastAPI(title="Воронки", docs_url=None, redoc_url=None)
app.include_router(media_router)

@app.get("/editor.js")
def editor_script():
    from fastapi.responses import Response
    return Response(Path("app/editor.js").read_text(encoding="utf-8"), media_type="application/javascript")

def require(obj):
    if obj is None:
        raise HTTPException(404, "Не найдено")
    return obj

class Login(BaseModel):
    init_data: str = Field(max_length=16384)

@app.get("/", response_class=HTMLResponse)
def index():
    return Path("app/admin.html").read_text(encoding="utf-8")

@app.get("/health")
def health():
    with Session() as db:
        db.execute(select(1))
    cache.ping()
    return {"status": "ok"}

@app.post("/auth")
def login(body: Login, request: Request):
    # Remote address comes from reverse proxy only in a trusted single-proxy deployment.
    key = "auth-limit:" + request.client.host
    count = cache.incr(key)
    if count == 1:
        cache.expire(key, 60)
    if count > 60:
        raise HTTPException(429, "Повторите позже")
    try:
        admin_id = validate_init_data(body.init_data, os.environ["ADMIN_BOT_TOKEN"])
    except (ValueError, KeyError, TypeError):
        raise HTTPException(401, "Подпись Telegram не прошла проверку")
    with Session() as db:
        admin = db.get(Admin, admin_id)
        if not admin or not admin.active:
            raise HTTPException(403, "Администратор не добавлен")
    digest = hashlib.sha256(body.init_data.encode()).hexdigest()
    if not cache.set("login-used:" + digest, "1", nx=True, ex=360):
        raise HTTPException(401, "Откройте панель заново")
    return {"token": issue_session(admin_id), "role": admin.role}

@app.get("/api/me")
def me(admin=Depends(actor)):
    return {"telegram_id": admin.telegram_id, "role": admin.role,
            "connection_ids": admin.connection_ids}

class AdminBody(BaseModel):
    telegram_id: int
    role: str
    connection_ids: list[str] = Field(default_factory=list)
    active: bool = True

@app.put("/api/admins")
def save_admin(body: AdminBody, admin=Depends(actor)):
    permit(admin, owner=True)
    if body.role not in {"owner", "editor", "manager", "viewer"}:
        raise HTTPException(422, "Неверная роль")
    # Bootstrap owner remains owner and active; prevents accidental lockout.
    if body.telegram_id == int(os.environ["OWNER_TELEGRAM_ID"]) and (body.role != "owner" or not body.active):
        raise HTTPException(422, "Основной владелец должен остаться активным")
    with Session.begin() as db:
        for cid in body.connection_ids:
            require(db.get(Connection, cid))
        target = db.get(Admin, body.telegram_id)
        if not target:
            target = Admin(telegram_id=body.telegram_id)
            db.add(target)
        target.role, target.active, target.connection_ids = body.role, body.active, body.connection_ids
        event(db, "admin.updated", actor_id=admin.telegram_id, target=body.telegram_id, role=body.role)
    return {"ok": True}

class ConnectionBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    platform: str
    token_env: str
    secret_env: str
    config: dict = Field(default_factory=dict)

@app.post("/api/connections")
def add_connection(body: ConnectionBody, admin=Depends(actor)):
    permit(admin, owner=True)
    if body.platform not in {"telegram", "max", "vk"}:
        raise HTTPException(422, "Поддерживаются Telegram, MAX, VK")
    prefix = {"telegram": "TG", "max": "MAX", "vk": "VK"}[body.platform]
    if not re.fullmatch(prefix + r"_[A-Z0-9_]+_TOKEN", body.token_env):
        raise HTTPException(422, "Имя token_env не соответствует платформе")
    if not re.fullmatch(prefix + r"_[A-Z0-9_]+_WEBHOOK_SECRET", body.secret_env):
        raise HTTPException(422, "Неверное имя secret_env")
    valid_config = {"username", "group_id", "confirmation_env", "default_entry", "target_type", "welcome_text"}
    if set(body.config) - valid_config:
        raise HTTPException(422, "Неизвестный параметр")
    if body.platform == "vk" and not re.fullmatch(r"VK_[A-Z0-9_]+_CONFIRMATION", body.config.get("confirmation_env", "")):
        raise HTTPException(422, "Нужен confirmation_env")
    if body.platform == "max" and body.config.get("target_type", "user_id") != "user_id":
        raise HTTPException(422, "Воронки MAX используют user_id")
    with Session.begin() as db:
        connection = Connection(id=uid(), **body.model_dump())
        db.add(connection)
        event(db, "connection.created", connection.id, actor_id=admin.telegram_id)
    return {"id": connection.id}

@app.get("/api/connections")
def connections(admin=Depends(actor)):
    with Session() as db:
        return [{"id": c.id, "name": c.name, "platform": c.platform, "active": c.active}
                for c in db.scalars(select(Connection)) if allowed(admin, c.id)]

class DestinationBody(BaseModel):
    connection_id: str
    name: str
    external_chat_id: str
    kind: str

@app.post("/api/destinations")
def add_destination(body: DestinationBody, admin=Depends(actor)):
    permit(admin, [body.connection_id], edit=True)
    if body.kind not in {"channel", "group", "community"}:
        raise HTTPException(422, "Неверный тип")
    with Session.begin() as db:
        c = require(db.get(Connection, body.connection_id))
        if c.platform != "telegram":
            raise HTTPException(422, "Публичные публикации пока доступны для Telegram")
        d = Destination(id=uid(), **body.model_dump())
        db.add(d)
        event(db, "destination.created", c.id, actor_id=admin.telegram_id, destination=d.id)
    return {"id": d.id}

class BroadcastBody(BaseModel):
    destination_ids: list[str] = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=4000)

@app.post("/api/publications")
def publication(body: BroadcastBody, admin=Depends(actor)):
    with Session.begin() as db:
        destinations = [require(db.get(Destination, x)) for x in set(body.destination_ids)]
        permit(admin, [x.connection_id for x in destinations], edit=True)
        for d in destinations:
            if not d.active or not require(db.get(Connection, d.connection_id)).active:
                raise HTTPException(409, "Подключение отключено")
            db.add(Outbox(id=uid(), connection_id=d.connection_id,
                chat_id=d.external_chat_id, payload={"type": "text", "text": body.text}))
            event(db, "publication.queued", d.connection_id, actor_id=admin.telegram_id, destination=d.id)
    return {"queued": len(destinations)}

class FunnelBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    connection_ids: list[str] = Field(min_length=1, max_length=100)
    graph: dict

@app.post("/api/funnels")
def create_funnel(body: FunnelBody, admin=Depends(actor)):
    permit(admin, body.connection_ids, edit=True)
    try:
        validate_graph(body.graph)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error))
    with Session.begin() as db:
        for cid in body.connection_ids:
            require(db.get(Connection, cid))
        try:
            validate_bindings(db, body.graph, body.connection_ids)
        except ValueError as error:
            raise HTTPException(422, str(error))
        funnel = Funnel(id=uid(), name=body.name, connection_ids=body.connection_ids, draft=body.graph)
        db.add(funnel)
        event(db, "funnel.created", actor_id=admin.telegram_id, funnel=funnel.id)
    return {"id": funnel.id, "revision": funnel.revision}

class DraftBody(BaseModel):
    revision: int
    graph: dict
    name: str | None = Field(default=None, min_length=1, max_length=160)
    connection_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)

@app.put("/api/funnels/{fid}/draft")
def update_draft(fid: str, body: DraftBody, admin=Depends(actor)):
    try:
        validate_graph(body.graph)
    except (ValueError, TypeError, KeyError) as error:
        raise HTTPException(422, str(error))
    with Session.begin() as db:
        funnel = require(db.scalar(select(Funnel).where(Funnel.id == fid).with_for_update()))
        permit(admin, funnel.connection_ids, edit=True)
        if funnel.revision != body.revision:
            raise HTTPException(409, "Сценарий уже изменён другим администратором")
        requested_connections = body.connection_ids or funnel.connection_ids
        permit(admin, requested_connections, edit=True)
        try:
            validate_bindings(db, body.graph, requested_connections)
        except ValueError as error:
            raise HTTPException(422, str(error))
        if body.name is not None:
            funnel.name = body.name
        if body.connection_ids is not None:
            for old_entry in db.scalars(select(Entry).where(Entry.funnel_id == fid)):
                if old_entry.connection_id not in requested_connections:
                    old_entry.active = False
            funnel.connection_ids = requested_connections
        funnel.draft = body.graph
        funnel.revision += 1
        event(db, "funnel.edited", actor_id=admin.telegram_id, funnel=fid, revision=funnel.revision)
    return {"revision": funnel.revision}

@app.post("/api/funnels/{fid}/publish")
def publish(fid: str, admin=Depends(actor)):
    with Session.begin() as db:
        funnel = require(db.scalar(select(Funnel).where(Funnel.id == fid).with_for_update()))
        permit(admin, funnel.connection_ids, edit=True)
        try:
            validate_graph(funnel.draft)
            validate_bindings(db, funnel.draft, funnel.connection_ids)
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, str(error))
        version = Version(id=uid(), funnel_id=fid, graph=funnel.draft)
        db.add(version)
        funnel.published_version_id = version.id
        event(db, "funnel.published", actor_id=admin.telegram_id, funnel=fid, version=version.id)
    return {"version_id": version.id}

@app.get("/api/funnels")
def funnels(admin=Depends(actor)):
    with Session() as db:
        return [{"id": f.id, "name": f.name, "revision": f.revision,
                 "connection_ids": f.connection_ids, "graph": f.draft,
                 "version_id": f.published_version_id}
                for f in db.scalars(select(Funnel))
                if all(allowed(admin, c) for c in f.connection_ids)]

class EntryBody(BaseModel):
    funnel_id: str
    connection_id: str
    attribution: dict = Field(default_factory=dict)

@app.post("/api/entries")
def entry(body: EntryBody, admin=Depends(actor)):
    permit(admin, [body.connection_id], edit=True)
    with Session.begin() as db:
        funnel = require(db.get(Funnel, body.funnel_id))
        permit(admin, funnel.connection_ids, edit=True)
        c = require(db.get(Connection, body.connection_id))
        if c.id not in funnel.connection_ids or not funnel.published_version_id:
            raise HTTPException(409, "Нужно опубликовать воронку и выбрать подключение")
        token = secrets.token_urlsafe(18)
        db.add(Entry(token=token, **body.model_dump()))
        event(db, "entry.created", c.id, actor_id=admin.telegram_id, token=token)
    link = None
    if c.platform == "telegram" and c.config.get("username"):
        link = f"https://t.me/{c.config['username']}?start={token}"
    return {"payload": token, "telegram_link": link}

@app.get("/api/timeline")
def timeline(admin=Depends(actor)):
    with Session() as db:
        query = select(Event).order_by(Event.created_at.desc()).limit(200)
        if admin.role != "owner":
            query = select(Event).where(Event.connection_id.in_(admin.connection_ids)).order_by(Event.created_at.desc()).limit(200)
        return [{"kind": e.kind, "contact_id": e.contact_id, "connection_id": e.connection_id,
                 "run_id": e.run_id, "payload": e.payload, "time": e.created_at.isoformat()}
                for e in db.scalars(query)]

@app.get("/api/tasks")
def tasks(admin=Depends(actor)):
    with Session() as db:
        query = select(Task).limit(200)
        if admin.role != "owner":
            query = query.where(Task.connection_id.in_(admin.connection_ids))
        return [{"id": t.id, "contact_id": t.contact_id, "title": t.title, "status": t.status}
                for t in db.scalars(query)]

class ManualBody(BaseModel):
    identity_id: str
    text: str = Field(min_length=1, max_length=4000)

@app.post("/api/messages")
def manual_message(body: ManualBody, admin=Depends(actor)):
    if admin.role not in {"owner", "editor", "manager"}:
        raise HTTPException(403, "Нет права отправки")
    with Session.begin() as db:
        identity = require(db.get(Identity, body.identity_id))
        permit(admin, [identity.connection_id])
        db.add(Outbox(id=uid(), identity_id=identity.id, connection_id=identity.connection_id,
                      chat_id=identity.chat_id, payload={"type": "text", "text": body.text}))
        # Pause automatic run before human communication.
        for run in db.scalars(select(Run).where(Run.identity_id == identity.id,
            Run.status.in_(["active", "waiting", "delayed"])).with_for_update()):
            run.state = {**run.state, "_before_pause": run.status}
            run.status = "paused"
        # Unsent automation is cancelled; in-flight sends cannot be recalled.
        for out in db.scalars(select(Outbox).where(Outbox.identity_id == identity.id,
            Outbox.run_id.is_not(None), Outbox.status == "pending").with_for_update()):
            out.status = "held"
        event(db, "manager.message_queued", identity.connection_id, identity.contact_id,
              actor_id=admin.telegram_id, text=body.text)
    return {"ok": True}

@app.post("/hooks/{cid}", response_class=PlainTextResponse)
async def webhook(cid: str, request: Request):
    # The proxy should additionally set a small request-body size limit.
    raw_bytes = await request.body()
    if len(raw_bytes) > 262144:
        raise HTTPException(413)
    try:
        raw = json.loads(raw_bytes)
    except ValueError:
        raise HTTPException(400)
    if not isinstance(raw, dict):
        raise HTTPException(400)
    with Session() as db:
        c = require(db.get(Connection, cid))
        if not c.active:
            raise HTTPException(403)
        expected = os.environ.get(c.secret_env, "")
        supplied = (request.headers.get("x-telegram-bot-api-secret-token", "") if c.platform == "telegram"
                    else request.headers.get("x-max-bot-api-secret", "") if c.platform == "max"
                    else raw.get("secret", ""))
        if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
            raise HTTPException(403)
        if c.platform == "vk":
            if str(raw.get("group_id")) != str(c.config.get("group_id")):
                raise HTTPException(403)
            if raw.get("type") == "confirmation":
                value = os.environ.get(c.config.get("confirmation_env", ""), "")
                if not value:
                    raise HTTPException(503)
                return value
        try:
            msg = normalize(c.platform, raw)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Событие не соответствует контракту")
        if msg:
            db.add(Inbox(id=uid(), connection_id=cid, external_event_id=msg["event_id"], payload=msg))
            if msg.get("callback_id"):
                db.add(Outbox(id=uid(), connection_id=cid, chat_id=msg["chat_id"],
                    payload={"type":"ack", "callback_id":msg["callback_id"]}))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
    return "ok"


@app.get("/api/contacts")
def contacts(admin=Depends(actor)):
    from app.db import Contact
    with Session() as db:
        query = select(Identity).limit(200)
        if admin.role != "owner":
            query = query.where(Identity.connection_id.in_(admin.connection_ids))
        result = []
        for identity in db.scalars(query):
            c = db.get(Contact, identity.contact_id)
            result.append({"contact_id": c.id, "identity_id": identity.id,
                "connection_id": identity.connection_id, "external_user_id": identity.external_user_id, "name": identity.name,
                "stage": c.stage, "score": c.score})
        return result

@app.get("/api/outbox")
def deliveries(admin=Depends(actor)):
    with Session() as db:
        query = select(Outbox).order_by(Outbox.updated_at.desc()).limit(200)
        if admin.role != "owner":
            query = query.where(Outbox.connection_id.in_(admin.connection_ids))
        return [{"id": o.id, "connection_id": o.connection_id, "status": o.status,
                 "error": o.error, "external_message_id": o.external_message_id}
                for o in db.scalars(query)]

class RetryBody(BaseModel):
    accept_duplicate_risk: bool = False

@app.post("/api/outbox/{oid}/retry")
def retry_delivery(oid: str, body: RetryBody, admin=Depends(actor)):
    with Session.begin() as db:
        row = require(db.scalar(select(Outbox).where(Outbox.id == oid).with_for_update()))
        permit(admin, [row.connection_id], edit=True)
        if row.status not in {"failed", "unknown"}:
            raise HTTPException(409, "Повтор доступен для failed и unknown")
        if row.status == "unknown" and not body.accept_duplicate_risk:
            raise HTTPException(409, "Проверьте канал и подтвердите риск повторной отправки")
        row.status, row.error, row.due_at, row.attempts = "pending", None, now(), 0
        event(db, "message.retry_requested", row.connection_id, actor_id=admin.telegram_id,
              outbox_id=row.id, accept_duplicate_risk=body.accept_duplicate_risk)
    return {"ok": True}


@app.get("/api/destinations")
def destinations(admin=Depends(actor)):
    with Session() as db:
        return [{"id":d.id,"name":d.name,"connection_id":d.connection_id,
                 "external_chat_id":d.external_chat_id,"kind":d.kind}
            for d in db.scalars(select(Destination)) if allowed(admin,d.connection_id)]

@app.get("/api/runs")
def runs(admin=Depends(actor)):
    with Session() as db:
        query = select(Run,Identity).join(Identity,Run.identity_id==Identity.id).order_by(Run.id).limit(200)
        if admin.role != "owner":
            query = query.where(Identity.connection_id.in_(admin.connection_ids))
        return [{"id":r.id,"identity_id":i.id,"contact_id":i.contact_id,"name":i.name,
                 "connection_id":i.connection_id,"status":r.status,"current":r.current,
                 "due_at":r.due_at.isoformat() if r.due_at else None} for r,i in db.execute(query)]

class RunAction(BaseModel):
    action: str

@app.post("/api/runs/{rid}/action")
def run_action(rid: str, body: RunAction, admin=Depends(actor)):
    if admin.role not in {"owner","editor","manager"}:
        raise HTTPException(403)
    with Session.begin() as db:
        run = require(db.scalar(select(Run).where(Run.id==rid).with_for_update()))
        identity = db.get(Identity,run.identity_id)
        permit(admin,[identity.connection_id])
        if body.action == "stop":
            cancel_run(db,run)
        elif body.action == "pause" and run.status in {"active","waiting","delayed"}:
            run.state={**run.state,"_before_pause":run.status}
            run.status="paused"
            for out in db.scalars(select(Outbox).where(Outbox.run_id==run.id,Outbox.status=="pending")):
                out.status="held"
        elif body.action == "resume" and run.status=="paused":
            state=dict(run.state)
            previous=state.pop("_before_pause","waiting")
            run.state,run.status=state,previous
            if previous=="delayed":
                # Resume with a full new interval; never instantly flush overdue follow-ups.
                run.due_at=None
            for out in db.scalars(select(Outbox).where(Outbox.run_id==run.id,Outbox.status=="held")):
                out.status,out.due_at="pending",now()
            if previous=="active":
                advance(db,run,identity)
        else:
            raise HTTPException(409,"Операция недоступна для текущего состояния")
        event(db,"run."+body.action,identity.connection_id,identity.contact_id,run.id,actor_id=admin.telegram_id)
    return {"ok":True}

@app.get("/api/entries")
def entries(admin=Depends(actor)):
    with Session() as db:
        return [{"token":e.token,"funnel_id":e.funnel_id,"connection_id":e.connection_id,
                 "attribution":e.attribution,"active":e.active} for e in db.scalars(select(Entry))
                 if allowed(admin,e.connection_id)]

class ToggleBody(BaseModel):
    active: bool

@app.put("/api/entries/{token}")
def toggle_entry(token: str, body: ToggleBody, admin=Depends(actor)):
    with Session.begin() as db:
        e=require(db.get(Entry,token))
        permit(admin,[e.connection_id],edit=True)
        e.active=body.active
        event(db,"entry.updated",e.connection_id,actor_id=admin.telegram_id,token=token,active=e.active)
    return {"ok":True}

@app.get("/api/admins")
def admins(admin=Depends(actor)):
    permit(admin,owner=True)
    with Session() as db:
        return [{"telegram_id":a.telegram_id,"role":a.role,"active":a.active,
                 "connection_ids":a.connection_ids} for a in db.scalars(select(Admin))]

@app.put("/api/tasks/{tid}")
def complete_task(tid: str, admin=Depends(actor)):
    if admin.role not in {"owner","editor","manager"}:
        raise HTTPException(403)
    with Session.begin() as db:
        task=require(db.get(Task,tid))
        permit(admin,[task.connection_id])
        task.status="done"
        event(db,"task.completed",task.connection_id,task.contact_id,actor_id=admin.telegram_id,task_id=tid)
    return {"ok":True}

@app.get("/api/inbox")
def failed_inputs(admin=Depends(actor)):
    with Session() as db:
        query=select(Inbox).where(Inbox.status=="failed").order_by(Inbox.created_at.desc()).limit(200)
        if admin.role!="owner":
            query=query.where(Inbox.connection_id.in_(admin.connection_ids))
        return [{"id":i.id,"connection_id":i.connection_id,"error":i.error} for i in db.scalars(query)]

@app.post("/api/inbox/{iid}/retry")
def replay_input(iid: str, admin=Depends(actor)):
    with Session.begin() as db:
        row=require(db.scalar(select(Inbox).where(Inbox.id==iid).with_for_update()))
        permit(admin,[row.connection_id],edit=True)
        if row.status!="failed":
            raise HTTPException(409)
        row.status,row.error,row.available_at="pending",None,now()
        event(db,"inbox.replayed",row.connection_id,actor_id=admin.telegram_id,inbox_id=iid)
    return {"ok":True}

class TestAssetBody(BaseModel):
    connection_id: str
    chat_id: str
    asset_id: str
    text: str = "Проверка материала"

@app.post("/api/assets/test-send")
def test_asset(body: TestAssetBody, admin=Depends(actor)):
    permit(admin,[body.connection_id],edit=True)
    with Session.begin() as db:
        c=require(db.get(Connection,body.connection_id))
        if c.platform!="telegram":
            raise HTTPException(422,"Нативная тестовая отправка доступна для Telegram")
        asset=require(db.get(Asset,body.asset_id))
        permit(admin,asset.connection_ids,edit=True)
        if body.connection_id not in asset.connection_ids:
            raise HTTPException(403)
        payload={"type":asset.kind,"text":body.text,"asset_id":asset.id}
        if asset.kind=="video_note":
            payload["text"]=""
        out=Outbox(id=uid(),connection_id=c.id,chat_id=body.chat_id,payload=payload)
        db.add(out)
        event(db,"asset.test_queued",c.id,actor_id=admin.telegram_id,asset_id=asset.id)
    return {"outbox_id":out.id}

@app.get("/api/analytics")
def analytics(admin=Depends(actor)):
    with Session() as db:
        q=select(Event)
        if admin.role!="owner":
            q=q.where(Event.connection_id.in_(admin.connection_ids))
        counts={}
        for item in db.scalars(q):
            key=item.kind
            counts[key]=counts.get(key,0)+1
        return {"events":counts,"note":"Отправка не означает просмотр видео"}

@app.get("/api/templates")
def templates(admin=Depends(actor)):
    return json.loads(Path("app/templates.json").read_text(encoding="utf-8"))

@app.post("/api/connections/{cid}/activate")
def activate_connection(cid: str, admin=Depends(actor)):
    permit(admin,owner=True)
    import httpx
    from app.channels import credential, result
    with Session.begin() as db:
        c=require(db.get(Connection,cid))
        try:
            token,secret=credential(c.token_env),credential(c.secret_env)
        except Exception:
            raise HTTPException(422,"Заполните токен и webhook-секрет в окружении сервера")
        domain=os.environ.get("DOMAIN","")
        if not domain or domain=="funnels.example.ru":
            raise HTTPException(422,"Задайте DOMAIN в окружении сервера")
        url="https://"+domain+"/hooks/"+cid
        try:
            with httpx.Client(timeout=30) as client:
                if c.platform=="telegram":
                    info=result(client.post(f"https://api.telegram.org/bot{token}/getMe"),"telegram")["result"]
                    c.config={**c.config,"username":info["username"]}
                    result(client.post(f"https://api.telegram.org/bot{token}/setWebhook",json={
                        "url":url,"secret_token":secret,"allowed_updates":["message","callback_query"]}),"telegram")
                elif c.platform=="max":
                    response=client.post("https://platform-api2.max.ru/subscriptions",
                        headers={"Authorization":token},json={"url":url,"secret":secret,
                            "update_types":["bot_started","message_created","message_callback"]})
                    if not result(response,"max").get("success"):
                        raise ValueError()
                else:
                    return {"ok":False,"manual_setup":True,"webhook_url":url}
        except Exception:
            raise HTTPException(502,"Платформа не подтвердила подключение. Проверьте токен, секрет и сертификаты")
        event(db,"connection.activated",cid,actor_id=admin.telegram_id)
    return {"ok":True,"webhook_url":url}

@app.get("/api/preflight")
def preflight(admin=Depends(actor)):
    from app.media import ROOT
    import shutil
    checks={"database":False,"redis":False,"media_writable":False,"worker_alive":False}
    with Session() as db:
        db.execute(select(1))
        checks["database"]=True
        checks["connections"]=[{"id":c.id,"name":c.name,"platform":c.platform,
            "token_present":bool(os.environ.get(c.token_env)),"secret_present":bool(os.environ.get(c.secret_env))}
            for c in db.scalars(select(Connection)) if allowed(admin,c.id)]
    try:
        checks["redis"]=bool(cache.ping())
        checks["worker_alive"]=bool(cache.get("worker:heartbeat"))
    except Exception:
        pass
    ROOT.mkdir(parents=True,exist_ok=True)
    checks["media_writable"]=os.access(ROOT,os.W_OK)
    checks["media_free_bytes"]=shutil.disk_usage(ROOT).free
    return checks
