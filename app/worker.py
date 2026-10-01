import os, time
from datetime import timedelta, timezone
from sqlalchemy import select, exists, and_
from sqlalchemy.orm import aliased
from app.db import Session, Inbox, Connection, Identity, Run, Version, Outbox, Asset, now
from app.runtime import consume, advance, event, AwaitDelivery
from app.channels import send, DeliveryRejected, RateLimited
from app.auth import cache

def incoming():
    earlier = aliased(Inbox)
    with Session.begin() as db:
        prior = exists(select(earlier.id).where(earlier.connection_id == Inbox.connection_id,
            earlier.payload["user_id"].as_string() == Inbox.payload["user_id"].as_string(),
            earlier.status == "pending", earlier.created_at < Inbox.created_at))
        row = db.scalar(select(Inbox).where(Inbox.status == "pending", Inbox.available_at <= now(), ~prior)
            .order_by(Inbox.created_at, Inbox.id).with_for_update(skip_locked=True).limit(1))
        if not row:
            return False
        try:
            with db.begin_nested():
                connection = db.get(Connection, row.connection_id)
                if connection and connection.active:
                    consume(db, connection, row.payload)
                db.flush()
            row.status = "done"
        except AwaitDelivery:
            row.available_at = now() + timedelta(seconds=1)
        except Exception as error:
            row.status, row.error = "failed", type(error).__name__
        return True

def timers():
    with Session.begin() as db:
        runs = db.scalars(select(Run).where(Run.status == "delayed")
            .order_by(Run.due_at).with_for_update(skip_locked=True).limit(100)).all()
        for run in runs:
            identity = db.get(Identity, run.identity_id)
            connection = db.get(Connection, identity.connection_id)
            if not connection.active:
                continue
            outstanding = db.scalar(select(Outbox.id).where(Outbox.run_id == run.id,
                Outbox.status.notin_(["sent", "cancelled"])).limit(1))
            if outstanding:
                continue
            node = db.get(Version, run.version_id).graph["nodes"][run.current]
            if run.due_at is None:
                run.due_at = now() + timedelta(seconds=node["seconds"])
                return True
            if (run.due_at if run.due_at.tzinfo else run.due_at.replace(tzinfo=timezone.utc)) > now():
                continue
            try:
                with db.begin_nested():
                    run.current, run.status, run.due_at = node["next"], "active", None
                    advance(db, run, identity)
                    db.flush()
            except Exception as error:
                run.status = "blocked"
                event(db, "run.error", identity.connection_id, identity.contact_id, run.id,
                    error=type(error).__name__)
            return True
    return False

def resolved_payload(db, connection, payload):
    if not payload.get("asset_id"):
        return payload
    asset = db.get(Asset, payload["asset_id"])
    if not asset or connection.id not in asset.connection_ids:
        raise DeliveryRejected("asset_unavailable")
    result = dict(payload)
    if connection.platform == "telegram":
        result["_file_id"] = asset.telegram_refs.get(connection.id)
        if asset.local_path and not result["_file_id"]:
            from app.media import local_file
            result["_local_path"] = str(local_file(asset.local_path))
            result["_mime"] = asset.mime
        if not result.get("_file_id") and not result.get("_local_path"):
            raise DeliveryRejected("asset_not_prepared_for_bot")
    elif not result.get("media"):
        raise DeliveryRejected("asset_fallback_missing")
    return result

def outgoing():
    with Session.begin() as db:
        rows = db.scalars(select(Outbox).where(Outbox.status == "pending", Outbox.due_at <= now())
            .order_by(Outbox.created_at, Outbox.position).with_for_update(skip_locked=True).limit(100)).all()
        row = None
        for candidate in rows:
            if candidate.run_id:
                run = db.get(Run, candidate.run_id)
                if run.status == "paused":
                    candidate.status = "held"
                    continue
                if run.status == "stopped":
                    candidate.status = "cancelled"
                    continue
                predecessor = db.scalar(select(Outbox.id).where(Outbox.run_id == candidate.run_id,
                    Outbox.position < candidate.position, Outbox.status.notin_(["sent", "cancelled"])).limit(1))
                if predecessor:
                    continue
            # One message per second per conversation; explicit platform 429 is persisted below.
            if candidate.payload.get("type") != "ack" and not cache.set(
                f"send-rate:{candidate.connection_id}:{candidate.chat_id}", "1", nx=True, ex=1):
                continue
            row = candidate
            break
        if row is None:
            return False
        connection = db.get(Connection, row.connection_id)
        if not connection or not connection.active:
            row.status, row.error = "failed", "connection_disabled"
            return True
        try:
            payload = resolved_payload(db, connection, row.payload)
        except DeliveryRejected as error:
            row.status, row.error = "failed", str(error)
            return True
        row.status, row.updated_at = "sending", now()
        row.attempts += 1
        rid, chat_id = row.id, row.chat_id
    status, external_id, error, retry_seconds = "sent", None, None, None
    try:
        external_id = send(connection, chat_id, payload, rid)
    except RateLimited as limited:
        status, error, retry_seconds = "pending", "rate_limited", limited.seconds
    except DeliveryRejected as rejection:
        status, error = "failed", str(rejection)
    except Exception as exception:
        status, error = "unknown", type(exception).__name__
    with Session.begin() as db:
        row = db.get(Outbox, rid)
        if status == "pending" and row.attempts >= 8:
            status, error = "failed", "rate_limit_retries_exhausted"
        row.status, row.external_message_id, row.error, row.updated_at = status, str(external_id) if external_id else None, error, now()
        if retry_seconds:
            row.due_at = now() + timedelta(seconds=retry_seconds)
        if status == "sent" and row.payload.get("asset_id") and getattr(external_id, "file_id", None):
            asset = db.get(Asset, row.payload["asset_id"])
            asset.telegram_refs = {**asset.telegram_refs, row.connection_id: external_id.file_id}
        contact_id = db.get(Identity, row.identity_id).contact_id if row.identity_id else None
        event(db, "message." + status, row.connection_id, contact_id, row.run_id,
            outbox_id=rid, external_message_id=row.external_message_id)
    return True

def sweep():
    with Session.begin() as db:
        for row in db.scalars(select(Outbox).where(Outbox.status == "sending",
            Outbox.updated_at < now() - timedelta(minutes=10)).with_for_update(skip_locked=True)):
            row.status, row.error = "unknown", "worker_interrupted"
            event(db, "message.unknown", row.connection_id, run_id=row.run_id, outbox_id=row.id)

def main():
    # PostgreSQL advisory lock prevents accidental deployment of two worker processes.
    lock = None
    if Session.kw.get("bind").dialect.name == "postgresql":
        from sqlalchemy import text
        lock = Session.kw["bind"].connect()
        if not lock.execute(text("SELECT pg_try_advisory_lock(730191)")).scalar():
            raise SystemExit("Another worker is already running")
    while True:
        try:
            cache.setex("worker:heartbeat", 240, str(time.time()))
            sweep()
            did_work = incoming()
            did_work = outgoing() or did_work
            did_work = timers() or did_work
            time.sleep(0.05 if did_work else 0.5)
        except Exception as error:
            print("worker_error", type(error).__name__, flush=True)
            time.sleep(2)

if __name__ == "__main__":
    main()
