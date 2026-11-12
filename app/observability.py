"""Home-server queue metrics and stale-worker signals (VOR-5)."""
from __future__ import annotations

import os
import shutil
import time
from datetime import timezone

from sqlalchemy import func, select

from app import auth as auth_mod
from app.crm_models import EntitlementJob, IntegrationOutbox
from app.db import Outbox, Session, now
from app import media as media_mod


STALE_WORKER_SECONDS = int(os.getenv("WORKER_STALE_SECONDS", "200"))


def worker_heartbeat_age() -> float | None:
    try:
        raw = auth_mod.cache.get("worker:heartbeat")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return max(0.0, time.time() - float(raw))
    except (TypeError, ValueError):
        return None


def queue_metrics(db=None) -> dict:
    own = db is None
    session = db or Session()
    try:
        pending = session.scalar(select(func.count()).select_from(Outbox).where(Outbox.status == "pending")) or 0
        failed = session.scalar(select(func.count()).select_from(Outbox).where(Outbox.status == "failed")) or 0
        unknown = session.scalar(select(func.count()).select_from(Outbox).where(Outbox.status == "unknown")) or 0
        oldest = session.scalar(
            select(Outbox.created_at).where(Outbox.status == "pending").order_by(Outbox.created_at).limit(1)
        )
        oldest_age = None
        if oldest:
            ts = oldest if oldest.tzinfo else oldest.replace(tzinfo=timezone.utc)
            oldest_age = max(0, int((now() - ts).total_seconds()))
        integ_pending = session.scalar(
            select(func.count()).select_from(IntegrationOutbox).where(IntegrationOutbox.state == "pending")
        ) or 0
        entitlement_pending = session.scalar(
            select(func.count()).select_from(EntitlementJob).where(EntitlementJob.state.in_(["pending", "waiting_grant"]))
        ) or 0
    finally:
        if own:
            session.close()
    root = media_mod.ROOT
    root.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(root)
    age = worker_heartbeat_age()
    stale = age is None or age > STALE_WORKER_SECONDS
    return {
        "outbox_pending": pending,
        "outbox_failed": failed,
        "outbox_unknown": unknown,
        "outbox_oldest_pending_age_seconds": oldest_age,
        "integration_outbox_pending": integ_pending,
        "entitlement_jobs_pending": entitlement_pending,
        "worker_heartbeat_age_seconds": None if age is None else int(age),
        "worker_stale": stale,
        "media_free_bytes": disk.free,
        "media_total_bytes": disk.total,
        "stale_threshold_seconds": STALE_WORKER_SECONDS,
    }


def metrics_log_line(metrics: dict) -> str:
    # Structured single-line event for journald/cron scrapers; no secrets.
    parts = [f"{k}={metrics[k]}" for k in sorted(metrics)]
    return "voronka_metrics " + " ".join(parts)
