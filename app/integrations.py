"""Neighbor contracts with solo_pr / lms_end: signed M2M, no shared DB."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.crm_models import IntegrationOutbox
from app.crm_service import uid
from app.db import now

DEST_SOLO = "solo_pr"
DEST_LMS = "lms_end"

KINDS_SOLO = {"deal.paid", "access.granted", "access.revoked"}
KINDS_LMS = {"enrollment.desired", "enrollment.revoked"}


def _secret(destination: str) -> str:
    if destination == DEST_SOLO:
        return os.getenv("SOLO_PR_WEBHOOK_SECRET", "")
    if destination == DEST_LMS:
        return os.getenv("LMS_END_WEBHOOK_SECRET", "") or os.getenv("LMS_M2M_TOKEN", "")
    return ""


def _url(destination: str) -> str:
    if destination == DEST_SOLO:
        return os.getenv("SOLO_PR_EVENTS_URL", "")
    if destination == DEST_LMS:
        return os.getenv("LMS_END_ENROLLMENT_URL", "")
    return ""


def sign_payload(secret: str, body: bytes, timestamp: str) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify_signed_request(secret: str, body: bytes, timestamp: str, signature: str, max_skew_seconds: int = 300) -> bool:
    if not secret or not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    age = abs(int(now().timestamp()) - ts)
    if age > max_skew_seconds:
        return False
    expected = sign_payload(secret, body, timestamp)
    return hmac.compare_digest(expected.lower(), signature.lower())


def verify_bearer_or_hmac(request_headers: dict, body: bytes) -> bool:
    """Accept either Bearer LMS_M2M_TOKEN or HMAC headers used by solo_pr."""
    token = os.getenv("INTEGRATION_INBOUND_TOKEN") or os.getenv("LMS_M2M_TOKEN") or os.getenv("SOLO_PR_WEBHOOK_SECRET")
    auth = request_headers.get("authorization") or request_headers.get("Authorization") or ""
    if token and auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token):
        return True
    secret = os.getenv("SOLO_PR_WEBHOOK_SECRET") or token or ""
    ts = request_headers.get("x-voronka-timestamp") or request_headers.get("X-Voronka-Timestamp") or ""
    sig = request_headers.get("x-voronka-signature") or request_headers.get("X-Voronka-Signature") or ""
    return verify_signed_request(secret, body, str(ts), str(sig))


def enqueue_event(db, *, destination: str, kind: str, event_id: str, payload: dict[str, Any]) -> IntegrationOutbox | None:
    prior = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_id == event_id))
    if prior:
        return prior
    row = IntegrationOutbox(
        id=uid(),
        event_id=event_id,
        destination=destination,
        kind=kind,
        payload=payload,
        state="pending",
        attempts=0,
        next_attempt_at=now(),
        last_error="",
        created_at=now(),
        updated_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def enqueue_deal_paid(db, *, payment_id: str, order, profile, financial: dict) -> None:
    enqueue_event(
        db,
        destination=DEST_SOLO,
        kind="deal.paid",
        event_id=f"deal.paid:{payment_id}",
        payload={
            "event_id": f"deal.paid:{payment_id}",
            "kind": "deal.paid",
            "order_id": order.id,
            "payment_id": payment_id,
            "project_id": order.project_id,
            "profile_id": profile.id,
            "email": profile.email,
            "name": profile.name,
            "product": order.snapshot,
            "paid_minor": financial.get("paid_minor"),
            "total_minor": order.total_minor,
            "fully_paid": financial.get("fully_paid"),
            "currency": order.snapshot.get("currency", "RUB"),
            "source": "voronka",
        },
    )


def enqueue_access_event(db, *, kind: str, grant, order, profile, financial: dict | None = None) -> None:
    if kind not in {"access.granted", "access.revoked"}:
        raise ValueError(kind)
    enqueue_event(
        db,
        destination=DEST_SOLO,
        kind=kind,
        event_id=f"{kind}:{grant.id}",
        payload={
            "event_id": f"{kind}:{grant.id}",
            "kind": kind,
            "grant_id": grant.id,
            "order_id": order.id,
            "project_id": order.project_id,
            "profile_id": profile.id,
            "email": profile.email,
            "resource": grant.resource,
            "capability": grant.capability,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            "min_paid_percent": grant.min_paid_percent,
            "financial": financial or {},
            "source": "voronka",
        },
    )


def enqueue_enrollment(db, *, grant, order, profile, revision: int, revoke: bool = False) -> None:
    kind = "enrollment.revoked" if revoke else "enrollment.desired"
    enqueue_event(
        db,
        destination=DEST_LMS,
        kind=kind,
        event_id=f"{kind}:{grant.id}:r{revision}",
        payload={
            "event_id": f"{kind}:{grant.id}:r{revision}",
            "kind": kind,
            "grant_id": grant.id,
            "order_id": order.id,
            "voronka_profile_id": profile.id,
            "email": profile.email,
            "name": profile.name,
            "resource": grant.resource,
            "capability": grant.capability,
            "min_paid_percent": grant.min_paid_percent,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            "revision": revision,
            "revoke": revoke,
            "source": "voronka",
        },
    )


def deliver_one(db, row: IntegrationOutbox) -> bool:
    """Attempt HTTP delivery. Returns True if work was done."""
    url = _url(row.destination)
    secret = _secret(row.destination)
    if not url or not secret:
        row.state = "failed"
        row.last_error = "destination_not_configured"
        row.updated_at = now()
        return True
    body = json.dumps({"kind": row.kind, **row.payload}, ensure_ascii=False, separators=(",", ":")).encode()
    ts = str(int(now().timestamp()))
    headers = {
        "Content-Type": "application/json",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sign_payload(secret, body, ts),
        "X-Voronka-Event-Id": row.event_id,
        "Authorization": f"Bearer {secret}",
    }
    row.attempts += 1
    row.updated_at = now()
    try:
        with httpx.Client(timeout=20) as client:
            response = client.post(url, content=body, headers=headers)
        if 200 <= response.status_code < 300:
            row.state = "sent"
            row.last_error = ""
            return True
        if response.status_code in {408, 429} or response.status_code >= 500:
            raise RuntimeError(f"http_{response.status_code}")
        row.state = "dead"
        row.last_error = f"http_{response.status_code}"
        return True
    except Exception as exc:  # network / timeout
        row.last_error = type(exc).__name__[:80]
        if row.attempts >= 12:
            row.state = "dead"
        else:
            row.state = "pending"
            row.next_attempt_at = now() + timedelta(seconds=min(3600, 2 ** min(row.attempts, 10)))
        return True


def drain_outbox(db, limit: int = 20) -> int:
    rows = list(
        db.scalars(
            select(IntegrationOutbox)
            .where(IntegrationOutbox.state == "pending", IntegrationOutbox.next_attempt_at <= now())
            .order_by(IntegrationOutbox.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    done = 0
    for row in rows:
        deliver_one(db, row)
        done += 1
    return done
