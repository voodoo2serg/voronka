"""Neighbor contracts with solo_pr / lms_end: signed M2M, no shared DB.

Wire contract v1: dual-read inbound fields/headers; dual-emit outbound
(type+kind, event_id+idempotency_key, Voronka + peer header aliases).
Money ledger paths stay unchanged — only neighbor envelopes/headers.
"""
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

LMS_SCHEMA_VERSION = "1"


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


def _header_ci(headers: dict, *names: str) -> str:
    """Case-insensitive header lookup across common alias spellings."""
    lowered = {str(k).lower(): v for k, v in headers.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return str(value)
    return ""


def normalize_signature(signature: str) -> str:
    """Accept raw hex or `sha256=<hex>` (wire contract / solo_pr / LMS)."""
    sig = (signature or "").strip()
    if sig.lower().startswith("sha256="):
        return sig.split("=", 1)[1].strip()
    return sig


def format_signature(hex_digest: str) -> str:
    return f"sha256={hex_digest}"


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
    actual = normalize_signature(str(signature))
    return hmac.compare_digest(expected.lower(), actual.lower())


def verify_bearer_or_hmac(request_headers: dict, body: bytes) -> bool:
    """Accept Bearer shared secret or timestamp HMAC under Voronka/Solo/LMS header names."""
    token = os.getenv("INTEGRATION_INBOUND_TOKEN") or os.getenv("LMS_M2M_TOKEN") or os.getenv("SOLO_PR_WEBHOOK_SECRET")
    auth = _header_ci(request_headers, "authorization")
    if token and auth.startswith("Bearer ") and hmac.compare_digest(auth[7:], token):
        return True
    secret = os.getenv("SOLO_PR_WEBHOOK_SECRET") or os.getenv("LMS_END_WEBHOOK_SECRET") or token or ""
    ts = _header_ci(
        request_headers,
        "x-voronka-timestamp",
        "x-solo-timestamp",
        "x-lms-timestamp",
    )
    sig = _header_ci(
        request_headers,
        "x-voronka-signature",
        "x-solo-signature",
        "x-lms-signature",
    )
    return verify_signed_request(secret, body, ts, sig)


def _product_hints(order) -> dict[str, Any]:
    """Optional course/club/commerce ids from order snapshot / product config — additive only."""
    snap = order.snapshot if isinstance(getattr(order, "snapshot", None), dict) else {}
    config = snap.get("config") if isinstance(snap.get("config"), dict) else {}
    hints: dict[str, Any] = {}
    course_id = config.get("course_id") or snap.get("course_id") or ""
    club = config.get("club") or snap.get("club") or ""
    commerce_product_id = getattr(order, "product_id", None) or snap.get("commerce_product_id") or ""
    if course_id:
        hints["course_id"] = str(course_id)
    if club:
        hints["club"] = str(club)
    if commerce_product_id:
        hints["commerce_product_id"] = str(commerce_product_id)
    return hints


def _dual_event_fields(kind: str, event_id: str) -> dict[str, str]:
    return {
        "type": kind,
        "kind": kind,
        "event_id": event_id,
        "idempotency_key": event_id,
    }


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
    event_id = f"deal.paid:{payment_id}"
    hints = _product_hints(order)
    enqueue_event(
        db,
        destination=DEST_SOLO,
        kind="deal.paid",
        event_id=event_id,
        payload={
            **_dual_event_fields("deal.paid", event_id),
            "order_id": order.id,
            "payment_id": payment_id,
            "project_id": order.project_id,
            "profile_id": profile.id,
            "external_id": profile.id,
            "email": profile.email,
            "name": profile.name,
            "product": order.snapshot,
            "paid_minor": financial.get("paid_minor"),
            "total_minor": order.total_minor,
            "fully_paid": financial.get("fully_paid"),
            "currency": order.snapshot.get("currency", "RUB") if isinstance(order.snapshot, dict) else "RUB",
            "source": "voronka",
            **hints,
        },
    )


def enqueue_access_event(db, *, kind: str, grant, order, profile, financial: dict | None = None) -> None:
    if kind not in {"access.granted", "access.revoked"}:
        raise ValueError(kind)
    event_id = f"{kind}:{grant.id}"
    hints = _product_hints(order)
    if grant.resource and "course_id" not in hints:
        hints["course_id"] = grant.resource
    enqueue_event(
        db,
        destination=DEST_SOLO,
        kind=kind,
        event_id=event_id,
        payload={
            **_dual_event_fields(kind, event_id),
            "grant_id": grant.id,
            "order_id": order.id,
            "project_id": order.project_id,
            "profile_id": profile.id,
            "external_id": profile.id,
            "email": profile.email,
            "resource": grant.resource,
            "capability": grant.capability,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            "min_paid_percent": grant.min_paid_percent,
            "financial": financial or {},
            "source": "voronka",
            **hints,
        },
    )


def enqueue_enrollment(db, *, grant, order, profile, revision: int, revoke: bool = False) -> None:
    kind = "enrollment.revoked" if revoke else "enrollment.desired"
    event_id = f"{kind}:{grant.id}:r{revision}"
    hints = _product_hints(order)
    course_id = hints.get("course_id") or grant.resource or ""
    commerce_product_id = hints.get("commerce_product_id") or order.product_id
    capability = grant.capability if grant.capability in {"course.view", "course.enter"} else "course.view"
    if capability == "course.enter":
        # Canonical wire capability is course.view; keep enter as alias field for LMS dual-accept.
        capability = "course.view"
    extra = profile.extra if isinstance(getattr(profile, "extra", None), dict) else {}
    keycloak_sub = str(extra.get("keycloak_sub") or "")
    payload: dict[str, Any] = {
        **_dual_event_fields(kind, event_id),
        # LMS EnrollmentBody
        "schema_version": LMS_SCHEMA_VERSION,
        "action": "revoke" if revoke else "grant",
        "email": profile.email,
        "voronka_profile_id": profile.id,
        "course_id": course_id,
        "commerce_product_id": commerce_product_id,
        "capability": capability,
        # Internal / dual-read fields LMS may ignore
        "grant_id": grant.id,
        "order_id": order.id,
        "name": profile.name,
        "resource": grant.resource,
        "min_paid_percent": grant.min_paid_percent,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "revision": revision,
        "revoke": revoke,
        "source": "voronka",
    }
    if keycloak_sub:
        payload["keycloak_sub"] = keycloak_sub
    if hints.get("club"):
        payload["club"] = hints["club"]
    enqueue_event(
        db,
        destination=DEST_LMS,
        kind=kind,
        event_id=event_id,
        payload=payload,
    )


def build_wire_payload(row: IntegrationOutbox) -> dict[str, Any]:
    """Ensure dual type/kind + event_id/idempotency_key on the wire body."""
    payload = dict(row.payload or {})
    kind = row.kind or payload.get("kind") or payload.get("type") or ""
    event_id = row.event_id or payload.get("event_id") or payload.get("idempotency_key") or ""
    payload.update(_dual_event_fields(kind, event_id))
    return payload


def delivery_headers(destination: str, secret: str, body: bytes, event_id: str) -> dict[str, str]:
    ts = str(int(now().timestamp()))
    digest = sign_payload(secret, body, ts)
    sig = format_signature(digest)
    headers = {
        "Content-Type": "application/json",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sig,
        "X-Voronka-Event-Id": event_id,
        "Authorization": f"Bearer {secret}",
    }
    if destination == DEST_SOLO:
        headers["X-Solo-Timestamp"] = ts
        headers["X-Solo-Signature"] = sig
    elif destination == DEST_LMS:
        headers["X-Lms-Timestamp"] = ts
        headers["X-Lms-Signature"] = sig
    return headers


def deliver_one(db, row: IntegrationOutbox) -> bool:
    """Attempt HTTP delivery. Returns True if work was done."""
    url = _url(row.destination)
    secret = _secret(row.destination)
    if not url or not secret:
        row.state = "failed"
        row.last_error = "destination_not_configured"
        row.updated_at = now()
        return True
    payload = build_wire_payload(row)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = delivery_headers(row.destination, secret, body, row.event_id)
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
