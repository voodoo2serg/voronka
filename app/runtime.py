import re, secrets
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, func
from app.db import Contact, Identity, Run, Version, Entry, Funnel, Outbox, Event, Entitlement, Task, Asset, Admin, now

LIVE = ["active", "waiting", "delayed", "paused", "blocked"]
class AwaitDelivery(Exception):
    """Durably keep this input until the prompt's send result is known."""

def uid():
    return str(uuid4())

def event(db, kind, connection_id=None, contact_id=None, run_id=None, actor_id=None, **payload):
    db.add(Event(id=uid(), kind=kind, connection_id=connection_id, contact_id=contact_id,
                 run_id=run_id, actor_id=actor_id, payload=payload))

def queue(db, identity, payload, run=None):
    position = 0
    if run:
        position = (db.scalar(select(func.max(Outbox.position)).where(Outbox.run_id == run.id)) or 0) + 1
    out = Outbox(id=uid(), connection_id=identity.connection_id, identity_id=identity.id,
        run_id=run.id if run else None, chat_id=identity.chat_id, payload=payload,
        position=position, created_at=now(), due_at=now())
    db.add(out)
    return out

def prompt(db, run, identity, question, state):
    nonce = secrets.token_urlsafe(8)
    state["_prompt"] = nonce
    options = question.get("options", [])
    payload = {"type": "text", "text": question["text"]}
    if options:
        payload["choices"] = [{"text": option, "data": f"a:{nonce}:{index}"}
                              for index, option in enumerate(options)]
    queue(db, identity, payload, run)

def advance(db, run, identity):
    graph = db.get(Version, run.version_id).graph
    state = dict(run.state)
    contact = db.get(Contact, identity.contact_id)
    for _ in range(201):
        if run.status != "active":
            break
        key = run.current
        node = graph["nodes"][key]
        kind = node["type"]
        event(db, "node.entered", identity.connection_id, contact.id, run.id, node=key)
        if kind in {"text", "photo", "video", "video_note", "voice", "document"}:
            # Circle messages have no caption in Telegram: queue explanatory text first.
            if kind == "video_note" and node.get("text"):
                queue(db, identity, {"type": "text", "text": node["text"]}, run)
            queue(db, identity, node, run)
        elif kind == "question":
            prompt(db, run, identity, node, state)
            run.status = "waiting"
            break
        elif kind == "survey":
            state["_survey_index"], state["_survey_answers"] = 0, {}
            prompt(db, run, identity, node["questions"][0], state)
            run.status = "waiting"
            break
        elif kind == "condition":
            run.current = node["yes"] if state.get(node["key"]) == node.get("equals", True) else node["no"]
            continue
        elif kind == "wait":
            # Delay starts after preceding messages are sent, not when they enter outbox.
            run.due_at = None
            run.status = "delayed"
            break
        elif kind == "grant":
            if not all(state.get(x) is True for x in node["requires"]):
                run.status = "blocked"
                event(db, "grant.blocked", identity.connection_id, contact.id, run.id, node=key)
                break
            existing = db.scalar(select(Entitlement).where(
                Entitlement.contact_id == contact.id, Entitlement.product_key == node["product"]))
            if not existing:
                db.add(Entitlement(id=uid(), contact_id=contact.id, product_key=node["product"]))
            event(db, "product.granted", identity.connection_id, contact.id, run.id, product=node["product"])
        elif kind == "stage":
            contact.stage = node["text"]
        elif kind == "task":
            db.add(Task(id=uid(), contact_id=contact.id, connection_id=identity.connection_id, title=node["text"]))
        elif kind == "goal":
            event(db, "goal.reached", identity.connection_id, contact.id, run.id, goal=node["text"])
        elif kind == "finish":
            run.status = "completed"
            event(db, "funnel.completed", identity.connection_id, contact.id, run.id)
            break
        run.current = node["next"]
    else:
        raise ValueError("graph step budget exceeded")
    run.state = state

def cancel_run(db, run):
    run.status, run.due_at = "stopped", None
    for out in db.scalars(select(Outbox).where(Outbox.run_id == run.id,
        Outbox.status.in_(["pending", "held"])).with_for_update()):
        out.status = "cancelled"

def media_import(db, connection, msg):
    admin = db.get(Admin, int(msg["user_id"]))
    if not admin or not admin.active or admin.role not in {"owner", "editor"}:
        return False
    if admin.role != "owner" and connection.id not in admin.connection_ids:
        return False
    media = msg["media"]
    asset = Asset(id=uid(), name=(msg.get("text") or media["kind"])[:160],
        kind=media["kind"], mime=media.get("mime", ""), connection_ids=[connection.id],
        telegram_refs={connection.id: media["file_id"]}, metadata_json=media.get("meta", {}))
    db.add(asset)
    db.flush()
    identity = db.scalar(select(Identity).where(Identity.connection_id == connection.id,
        Identity.external_user_id == msg["user_id"]))
    if not identity:
        contact = Contact(id=uid(), stage="Администратор")
        db.add(contact); db.flush()
        identity = Identity(id=uid(), contact_id=contact.id, connection_id=connection.id,
            external_user_id=msg["user_id"], chat_id=msg["chat_id"], name=msg.get("name", ""))
        db.add(identity); db.flush()
    queue(db, identity, {"type": "text", "text": "Материал сохранён в медиабанке: " + asset.name})
    event(db, "asset.imported", connection.id, actor_id=admin.telegram_id, asset_id=asset.id)
    return True

def consume(db, connection, msg):
    if msg.get("media"):
        media_import(db, connection, msg)
        return
    identity = db.scalar(select(Identity).where(Identity.connection_id == connection.id,
        Identity.external_user_id == msg["user_id"]).with_for_update())
    if not identity:
        contact = Contact(id=uid(), stage="Новый")
        db.add(contact); db.flush()
        identity = Identity(id=uid(), contact_id=contact.id, connection_id=connection.id,
            external_user_id=msg["user_id"], chat_id=msg["chat_id"], name=msg.get("name", ""))
        db.add(identity); db.flush()
        event(db, "contact.created", connection.id, contact.id)
    elif msg.get("name"):
        identity.name = msg["name"][:200]
    run = db.scalar(select(Run).where(Run.identity_id == identity.id,
        Run.status.in_(LIVE)).with_for_update())
    text, callback = msg.get("text", ""), msg.get("callback", "")
    if text.strip().lower() in {"/stop", "стоп"}:
        # Also cancel undelivered messages of a completed run.
        for previous in db.scalars(select(Run).where(Run.identity_id == identity.id)):
            if previous.status in LIVE or previous.status == "completed":
                cancel_run(db, previous)
        event(db, "contact.stopped", connection.id, identity.contact_id)
        return
    if callback.startswith("e:"):
        msg = {**msg, "start": True, "payload": callback[2:]}
    if msg.get("start"):
        if run:
            return
        token = msg.get("payload") or connection.config.get("default_entry")
        entry = db.get(Entry, token) if token else None
        if not entry or not entry.active or entry.connection_id != connection.id:
            entries = db.scalars(select(Entry).where(Entry.connection_id == connection.id, Entry.active == True)).all()
            choices = [{"text": db.get(Funnel, e.funnel_id).name[:100], "data": "e:" + e.token}
                       for e in entries[:20] if db.get(Funnel, e.funnel_id).published_version_id]
            welcome = connection.config.get("welcome_text", "Выберите программу")
            queue(db, identity, {"type": "text", "text": welcome, "choices": choices})
            return
        funnel = db.get(Funnel, entry.funnel_id)
        if not funnel.published_version_id or connection.id not in funnel.connection_ids:
            return
        version = db.get(Version, funnel.published_version_id)
        run = Run(id=uid(), identity_id=identity.id, version_id=version.id,
            entry_token=entry.token, current=version.graph["start"], status="active", state={})
        db.add(run); db.flush()
        event(db, "funnel.started", connection.id, identity.contact_id, run.id, attribution=entry.attribution)
        advance(db, run, identity)
        return
    if not run or run.status != "waiting":
        event(db, "message.received", connection.id, identity.contact_id, run.id if run else None, text=text)
        return
    node = db.get(Version, run.version_id).graph["nodes"][run.current]
    state = dict(run.state)
    question = node if node["type"] == "question" else node["questions"][state["_survey_index"]]
    if callback:
        parts = callback.split(":")
        if len(parts) != 3 or parts[0] != "a" or parts[1] != state.get("_prompt"):
            event(db, "callback.stale", connection.id, identity.contact_id, run.id)
            return
        try:
            index = int(parts[2])
            if index < 0:
                return
            text = question.get("options", [])[index]
        except (ValueError, IndexError):
            return
    outstanding = db.scalar(select(Outbox).where(Outbox.run_id == run.id,
        Outbox.status.notin_(["sent", "cancelled"])))
    if outstanding:
        raise AwaitDelivery()
    if not text.strip():
        return
    event(db, "message.received", connection.id, identity.contact_id, run.id, text=text)
    options = question.get("options", [])
    valid = not options or text in options
    answer_type = question.get("answer_type", "text")
    if answer_type == "email":
        valid = bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text))
    if answer_type == "phone":
        valid = bool(re.fullmatch(r"\+?[\d ()-]{7,25}", text))
    if not valid:
        prompt(db, run, identity, question, state)
        run.state = state
        return
    if node["type"] == "question":
        state[node["key"]] = text
        event(db, "question.answered", connection.id, identity.contact_id, run.id, key=node["key"], answer=text)
    else:
        answers = dict(state["_survey_answers"])
        answers[question["key"]] = text
        state["_survey_answers"] = answers
        index = state["_survey_index"] + 1
        if index < len(node["questions"]):
            state["_survey_index"] = index
            prompt(db, run, identity, node["questions"][index], state)
            run.state = state
            return
        state[node["key"]], state[node["key"] + "_answers"] = True, answers
        event(db, "survey.completed", connection.id, identity.contact_id, run.id, answers=answers)
    state.pop("_prompt", None)
    nxt = node["next"]
    if node["type"] == "question":
        nxt = (node.get("routes") or {}).get(text) or nxt
    run.state, run.current, run.status = state, nxt, "active"
    advance(db, run, identity)
