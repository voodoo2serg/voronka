import time
from datetime import timedelta
from sqlalchemy import select
from app.db import Session, Inbox, Connection, Identity, Run, Version, Outbox, now
from app.runtime import consume, advance, event
from app.channels import send, DeliveryRejected

def incoming():
    with Session.begin() as db:
        row = db.scalar(select(Inbox).where(Inbox.status == "pending")
                        .order_by(Inbox.created_at).with_for_update(skip_locked=True).limit(1))
        if not row:
            return False
        try:
            with db.begin_nested():
                connection = db.get(Connection, row.connection_id)
                if connection and connection.active:
                    consume(db, connection, row.payload)
                db.flush()
            row.status = "done"
        except Exception as error:
            row.status = "failed"
            row.error = type(error).__name__ # never log credentials or request URLs
        return True

def timers():
    with Session.begin() as db:
        run = db.scalar(select(Run).where(Run.status == "delayed", Run.due_at <= now())
                        .with_for_update(skip_locked=True).limit(1))
        if not run:
            return False
        identity = db.get(Identity, run.identity_id)
        connection = db.get(Connection, identity.connection_id)
        if not connection.active:
            return False
        node = db.get(Version, run.version_id).graph["nodes"][run.current]
        run.current, run.status, run.due_at = node["next"], "active", None
        advance(db, run, identity)
        return True

def outgoing():
    # Claim before network IO. A crash leaves "sending"; never blindly resend.
    with Session.begin() as db:
        row = db.scalar(select(Outbox).where(Outbox.status == "pending")
                        .order_by(Outbox.updated_at).with_for_update(skip_locked=True).limit(1))
        if not row:
            return False
        connection = db.get(Connection, row.connection_id)
        if not connection or not connection.active:
            row.status, row.error = "failed", "connection_disabled"
            return True
        row.status, row.updated_at = "sending", now()
        rid, chat_id, payload = row.id, row.chat_id, row.payload
    status, external_id, error = "sent", None, None
    try:
        external_id = send(connection, chat_id, payload, rid)
    except DeliveryRejected as rejection:
        status, error = "failed", str(rejection)
    except Exception as exception:
        status, error = "unknown", type(exception).__name__
    with Session.begin() as db:
        row = db.get(Outbox, rid)
        row.status, row.external_message_id, row.error, row.updated_at = status, external_id, error, now()
        contact_id = db.get(Identity, row.identity_id).contact_id if row.identity_id else None
        event(db, "message." + status, row.connection_id, contact_id, row.run_id,
              outbox_id=rid, external_message_id=external_id)
    return True

def sweep():
    with Session.begin() as db:
        for row in db.scalars(select(Outbox).where(
            Outbox.status == "sending", Outbox.updated_at < now() - timedelta(minutes=5))
            .with_for_update(skip_locked=True)):
            row.status, row.error = "unknown", "worker_interrupted"
            event(db, "message.unknown", row.connection_id, run_id=row.run_id, outbox_id=row.id)

def main():
    # Baseline uses ONE worker to serialize identity creation and transitions.
    while True:
        try:
            sweep()
            did_work = outgoing()
            did_work = incoming() or did_work
            did_work = timers() or did_work
            if not did_work:
                time.sleep(1)
        except Exception as error:
            print("worker_error", type(error).__name__, flush=True)
            time.sleep(2)

if __name__ == "__main__":
    main()
