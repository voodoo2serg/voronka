import sys
import time
from sqlalchemy import text
from app.db import Session
from app.integrations import DEST_LMS, DEST_SIGNAL, DEST_SOLO, drain_outbox
from app.auth import cache


def main():
    destinations = (DEST_LMS, DEST_SIGNAL, DEST_SOLO)
    if len(sys.argv) != 2 or sys.argv[1] not in destinations:
        raise SystemExit("Expected destination: lms_end, signal, solo_pr")
    destination = sys.argv[1]
    lock = None
    if Session.kw["bind"].dialect.name == "postgresql":
        lock = Session.kw["bind"].connect()
        key = 730200 + destinations.index(destination)
        if not lock.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar():
            raise SystemExit("Destination worker already running")
        lock.commit()
    while True:
        if lock is not None:
            lock.execute(text("SELECT 1"))
            lock.commit()
        try:
            try:
                cache.setex("worker:integration:" + destination, 120, str(time.time()))
            except Exception:
                pass
            with Session.begin() as db:
                delivered = drain_outbox(db, limit=1, destination=destination)
            if not delivered:
                time.sleep(0.5)
        except Exception as error:
            print("integration_delivery_error", destination, type(error).__name__, flush=True)
            time.sleep(2)


if __name__ == "__main__":
    main()
