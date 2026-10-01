from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select
from app.db import Contact, Identity, Run, Version, Entry, Funnel, Outbox, Event, Entitlement, Task, now

def uid():
    return str(uuid4())

def event(db, kind, connection_id=None, contact_id=None, run_id=None, actor_id=None, **payload):
    db.add(Event(id=uid(), kind=kind, connection_id=connection_id, contact_id=contact_id,
                 run_id=run_id, actor_id=actor_id, payload=payload))

def queue(db, identity, payload, run=None):
    db.add(Outbox(id=uid(), connection_id=identity.connection_id, identity_id=identity.id,
                 run_id=run.id if run else None, chat_id=identity.chat_id, payload=payload))

def advance(db, run, identity):
    graph = db.get(Version, run.version_id).graph
    state = dict(run.state)
    contact = db.get(Contact, identity.contact_id)
    while run.status == "active":
        key = run.current
        node = graph["nodes"][key]
        kind = node["type"]
        event(db, "node.entered", identity.connection_id, contact.id, run.id, node=key)
        if kind in {"text", "photo", "video", "voice", "document"}:
            queue(db, identity, node, run)
        elif kind == "question":
            queue(db, identity, {"type": "text", "text": node["text"]}, run)
            run.status = "waiting"
            break
        elif kind == "survey":
            state["_survey_index"] = 0
            state["_survey_answers"] = {}
            queue(db, identity, {"type": "text", "text": node["questions"][0]["text"]}, run)
            run.status = "waiting"
            break
        elif kind == "condition":
            run.current = node["yes"] if state.get(node["key"]) == node.get("equals", True) else node["no"]
            continue
        elif kind == "wait":
            run.due_at = now() + timedelta(seconds=node["seconds"])
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
        elif kind == "finish":
            run.status = "completed"
            event(db, "funnel.completed", identity.connection_id, contact.id, run.id)
            break
        run.current = node["next"]
    run.state = state

def consume(db, connection, msg):
    identity = db.scalar(select(Identity).where(
        Identity.connection_id == connection.id,
        Identity.external_user_id == msg["user_id"]).with_for_update())
    if not identity:
        contact = Contact(id=uid(), stage="Новый")
        db.add(contact)
        db.flush()
        identity = Identity(id=uid(), contact_id=contact.id, connection_id=connection.id,
                            external_user_id=msg["user_id"], chat_id=msg["chat_id"])
        db.add(identity)
        db.flush()
        event(db, "contact.created", connection.id, contact.id)
    run = db.scalar(select(Run).where(Run.identity_id == identity.id,
        Run.status.in_(["active", "waiting", "delayed", "paused", "blocked"]))
        .with_for_update())
    if msg["text"].lower() in {"/stop", "стоп"}:
        if run:
            run.status = "stopped"
        event(db, "contact.stopped", connection.id, identity.contact_id)
        return
    if msg["start"]:
        if run:
            return # No duplicate launches or silent interruption.
        token = msg["payload"] or connection.config.get("default_entry")
        entry = db.get(Entry, token) if token else None
        if not entry or not entry.active or entry.connection_id != connection.id:
            event(db, "entry.rejected", connection.id, identity.contact_id)
            return
        funnel = db.get(Funnel, entry.funnel_id)
        if not funnel.published_version_id:
            return
        version = db.get(Version, funnel.published_version_id)
        run = Run(id=uid(), identity_id=identity.id, version_id=version.id,
                  entry_token=entry.token, current=version.graph["start"], status="active", state={})
        db.add(run)
        db.flush()
        event(db, "funnel.started", connection.id, identity.contact_id, run.id,
              attribution=entry.attribution)
        advance(db, run, identity)
        return
    event(db, "message.received", connection.id, identity.contact_id, run.id if run else None,
          text=msg["text"])
    if not run or run.status != "waiting" or not msg["text"].strip():
        return
    # Do not accept input until the current prompt has successfully been sent.
    pending = db.scalar(select(Outbox).where(Outbox.run_id == run.id,
        Outbox.status != "sent"))
    if pending:
        return
    node = db.get(Version, run.version_id).graph["nodes"][run.current]
    state = dict(run.state)
    if node["type"] == "question":
        if node.get("options") and msg["text"] not in node["options"]:
            queue(db, identity, {"type": "text", "text": node["text"]}, run)
            return
        state[node["key"]] = msg["text"]
    elif node["type"] == "survey":
        index = state["_survey_index"]
        question = node["questions"][index]
        if question.get("options") and msg["text"] not in question["options"]:
            queue(db, identity, {"type": "text", "text": question["text"]}, run)
            return
        answers = dict(state["_survey_answers"])
        answers[question["key"]] = msg["text"]
        state["_survey_answers"] = answers
        index += 1
        if index < len(node["questions"]):
            state["_survey_index"] = index
            run.state = state
            queue(db, identity, {"type": "text", "text": node["questions"][index]["text"]}, run)
            return
        state[node["key"]] = True
        state[node["key"] + "_answers"] = answers
        event(db, "survey.completed", connection.id, identity.contact_id, run.id, answers=answers)
    run.state = state
    run.current = node["next"]
    run.status = "active"
    advance(db, run, identity)
