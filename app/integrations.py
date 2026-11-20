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
from sqlalchemy import or_, select

from app.crm_models import IntegrationOutbox
from app.crm_service import uid
from app.db import now

DEST_SOLO = "solo_pr"
DEST_LMS = "lms_end"
DEST_SIGNAL = "signal"

KINDS_SOLO = {"deal.paid", "access.granted", "access.revoked"}
KINDS_LMS = {"enrollment.desired", "enrollment.revoked"}
KINDS_SIGNAL = {"course.access_granted"}

LMS_SCHEMA_VERSION = "1"


def _secret(destination: str) -> str:
    if destination == DEST_SOLO:
        return os.getenv("SOLO_PR_WEBHOOK_SECRET", "")
    if destination == DEST_LMS:
        return os.getenv("LMS_END_WEBHOOK_SECRET", "") or os.getenv("LMS_M2M_TOKEN", "")
    if destination == DEST_SIGNAL:
        return os.getenv("SIGNAL_EVENTS_SECRET", "") or os.getenv("SIGNAL_EVENT_SOURCE_KEY", "")
    return ""


def _url(destination: str) -> str:
    if destination == DEST_SOLO:
        return os.getenv("SOLO_PR_EVENTS_URL", "")
    if destination == DEST_LMS:
        return os.getenv("LMS_END_ENROLLMENT_URL", "")
    if destination == DEST_SIGNAL:
        return os.getenv(
            "SIGNAL_EVENTS_URL",
            "https://signal.vodopetov.ru/api/v1/communications/events",
        )
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


def _inbound_auth_secrets() -> list[str]:
    """All inbound M2M secrets producers may sign with (order = preference for Bearer).

    HMAC must try every configured secret: RAGA signs with INTEGRATION_INBOUND_TOKEN while
    SOLO_PR_WEBHOOK_SECRET is also set for solo_pr — preferring only Solo would 401 RAGA.
    """
    ordered = [
        os.getenv("INTEGRATION_INBOUND_TOKEN", ""),
        os.getenv("LMS_M2M_TOKEN", ""),
        os.getenv("SOLO_PR_WEBHOOK_SECRET", ""),
        os.getenv("LMS_END_WEBHOOK_SECRET", ""),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for value in ordered:
        secret = (value or "").strip()
        if secret and secret not in seen:
            seen.add(secret)
            out.append(secret)
    return out


def verify_bearer_or_hmac(request_headers: dict, body: bytes) -> bool:
    """Accept Bearer shared secret or timestamp HMAC under Voronka/Solo/LMS header names."""
    secrets = _inbound_auth_secrets()
    auth = _header_ci(request_headers, "authorization")
    if auth.startswith("Bearer "):
        presented = auth[7:]
        for secret in secrets:
            if hmac.compare_digest(presented, secret):
                return True
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
    for secret in secrets:
        if verify_signed_request(secret, body, ts, sig):
            return True
    return False


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


def enqueue_event(
    db,
    *,
    destination: str,
    kind: str,
    event_id: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> IntegrationOutbox | None:
    prior = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_id == event_id))
    if prior:
        # Backfill correlation_id on idempotent hit without mutating delivery state.
        if correlation_id and not prior.correlation_id:
            prior.correlation_id = correlation_id
            prior.updated_at = now()
        return prior
    corr = correlation_id or str(payload.get("correlation_id") or payload.get("order_id") or "") or None
    if corr and "correlation_id" not in payload:
        payload = {**payload, "correlation_id": corr}
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
        correlation_id=corr,
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
        correlation_id=order.id,
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
            "correlation_id": order.id,
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
        correlation_id=order.id,
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
            "correlation_id": order.id,
            **hints,
        },
    )
    # CJ-107: transactional «доступ открыт» via Signal CE (idempotent per grant).
    if kind == "access.granted":
        enqueue_signal_access_granted(db, grant=grant, order=order, profile=profile, hints=hints)
        _record_access_interaction(db, grant=grant, order=order, profile=profile, hints=hints)


def signal_access_granted_event_id(grant_id: str) -> str:
    """CE-safe idempotency key for Signal intake.

    Signal `event_id` allowlist is `[a-zA-Z0-9:_-]{8,120}` — dots are rejected
    (`invalid_event_fields`). Keep `event_type=course.access_granted` on the wire;
    only the idempotency key uses underscores.
    """
    return f"course_access_granted:{grant_id}"


def legacy_signal_access_granted_event_id(grant_id: str) -> str:
    """Pre-fix dotted key — invalid for Signal CE intake, may exist in outbox/Interaction."""
    return f"course.access_granted:{grant_id}"


def signal_access_outbox_for_grant(db, grant_id: str) -> list[IntegrationOutbox]:
    """Modern + legacy Signal access rows for one grant (kind + event_id + payload)."""
    modern = signal_access_granted_event_id(grant_id)
    legacy = legacy_signal_access_granted_event_id(grant_id)
    rows = list(
        db.scalars(
            select(IntegrationOutbox).where(
                IntegrationOutbox.kind == "course.access_granted",
                or_(
                    IntegrationOutbox.event_id == modern,
                    IntegrationOutbox.event_id == legacy,
                ),
            )
        )
    )
    if rows:
        return rows
    candidates = list(
        db.scalars(
            select(IntegrationOutbox).where(IntegrationOutbox.kind == "course.access_granted").limit(500)
        )
    )
    return [r for r in candidates if (r.payload or {}).get("grant_id") == grant_id]


def adopt_signal_access_outbox(db, row: IntegrationOutbox, *, grant_id: str | None = None) -> IntegrationOutbox:
    """Rewrite dotted CE-invalid event_id → course_access_granted:{grant}; avoid duplicates."""
    if row.kind != "course.access_granted":
        return row
    payload = dict(row.payload or {})
    gid = str(grant_id or payload.get("grant_id") or "")
    if not gid and ":" in (row.event_id or ""):
        gid = row.event_id.split(":", 1)[-1]
    if not gid:
        return row
    modern = signal_access_granted_event_id(gid)
    if row.event_id == modern:
        if payload.get("event_id") != modern:
            payload["event_id"] = modern
            row.payload = payload
            row.updated_at = now()
        return row
    existing = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_id == modern))
    if existing and existing.id != row.id:
        # Prefer modern row; leave legacy as historical dead-end without CE retry.
        return existing
    row.event_id = modern
    payload["event_id"] = modern
    payload.setdefault("grant_id", gid)
    row.payload = payload
    row.updated_at = now()
    db.flush()
    return row


def adopt_signal_access_interaction(db, *, grant_id: str) -> None:
    """Move Interaction.external_event_key from dotted legacy → CE-safe modern key."""
    from app.crm_models import Interaction

    modern = signal_access_granted_event_id(grant_id)
    legacy = legacy_signal_access_granted_event_id(grant_id)
    modern_row = db.scalar(
        select(Interaction).where(
            Interaction.source_service == "voronka",
            Interaction.external_event_key == modern,
        )
    )
    legacy_row = db.scalar(
        select(Interaction).where(
            Interaction.source_service == "voronka",
            Interaction.external_event_key == legacy,
        )
    )
    if legacy_row and modern_row and legacy_row.id != modern_row.id:
        # Keep modern; clear legacy key so unique constraint allows a single timeline fact.
        legacy_row.external_event_key = None
        db.flush()
        return
    if legacy_row and not modern_row:
        legacy_row.external_event_key = modern
        db.flush()


def enqueue_signal_access_granted(db, *, grant, order, profile, hints: dict | None = None) -> None:
    """Outbox → Signal Communication Engine `course.access_granted`."""
    email = (getattr(profile, "email", None) or "").strip()
    # Same guard as interaction: CE mail needs a usable recipient.
    if not email or "@" not in email:
        return
    hints = hints or _product_hints(order)
    event_id = signal_access_granted_event_id(grant.id)
    service = str(hints.get("service") or hints.get("course_id") or grant.resource or "курс")
    payload = {
        "event_id": event_id,
        "event_type": "course.access_granted",
        "email": email,
        "service": service[:120],
        "grant_id": grant.id,
        "order_id": order.id,
        "project_id": order.project_id,
        "source": "voronka",
        "correlation_id": order.id,
    }
    # Adopt legacy dotted row instead of inserting a second CE-safe event_id.
    existing_rows = signal_access_outbox_for_grant(db, grant.id)
    if existing_rows:
        row = adopt_signal_access_outbox(db, existing_rows[0], grant_id=grant.id)
        # Drop extra legacy siblings after adoption to keep one fact per grant.
        for sibling in existing_rows[1:]:
            if sibling.id == row.id:
                continue
            if sibling.event_id == legacy_signal_access_granted_event_id(grant.id):
                # Leave undeliverable duplicate as dead without CE retry noise.
                if sibling.state != "dead":
                    sibling.state = "dead"
                    sibling.last_error = "superseded_by_ce_safe_event_id"
                    sibling.updated_at = now()
        row.payload = {**(row.payload or {}), **payload}
        if not row.correlation_id:
            row.correlation_id = order.id
        if row.state in {"dead", "failed"}:
            row.state = "pending"
            row.attempts = 0
            row.next_attempt_at = now()
            row.last_error = ""
        row.updated_at = now()
        db.flush()
        return
    enqueue_event(
        db,
        destination=DEST_SIGNAL,
        kind="course.access_granted",
        event_id=event_id,
        correlation_id=order.id,
        payload=payload,
    )


def _record_access_interaction(db, *, grant, order, profile, hints: dict | None = None) -> None:
    person_id = getattr(profile, "person_id", None)
    if not person_id or not profile.email:
        return
    from app import person_graph

    hints = hints or {}
    adopt_signal_access_interaction(db, grant_id=grant.id)
    person_graph.record_interaction(
        db,
        person_id=person_id,
        kind="course.access_granted",
        direction="system",
        channel="email",
        project_id=order.project_id,
        source_service="voronka",
        external_event_key=signal_access_granted_event_id(grant.id),
        payload={
            "grant_id": grant.id,
            "order_id": order.id,
            "email": profile.email,
            "resource": grant.resource,
            "service": hints.get("service") or hints.get("course_id") or grant.resource,
        },
        evidence_status="recorded",
    )


def enrollment_event_id(grant_id: str, *, revoke: bool = False) -> str:
    """Stable idempotency key — revision stays in payload, not event_id (OPS-104)."""
    kind = "enrollment.revoked" if revoke else "enrollment.desired"
    return f"{kind}:{grant_id}"


def _enrollment_rows_for_grant(db, grant_id: str, *, revoke: bool = False) -> list[IntegrationOutbox]:
    """Stable + legacy enrollment.desired:{grant}:rN rows for one grant."""
    kind = "enrollment.revoked" if revoke else "enrollment.desired"
    stable = enrollment_event_id(grant_id, revoke=revoke)
    rows = list(
        db.scalars(
            select(IntegrationOutbox).where(
                IntegrationOutbox.kind == kind,
                or_(
                    IntegrationOutbox.event_id == stable,
                    IntegrationOutbox.event_id.like(f"{stable}:r%"),
                ),
            )
        )
    )
    if rows:
        return rows
    candidates = list(db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == kind).limit(500)))
    return [r for r in candidates if (r.payload or {}).get("grant_id") == grant_id]


def _prefer_enrollment_row(rows: list[IntegrationOutbox]) -> IntegrationOutbox | None:
    if not rows:
        return None
    # Prefer stable event_id (no :rN); among equals, prefer already-sent then in-flight.
    stable_rows = [r for r in rows if ":r" not in r.event_id]
    pool = list(stable_rows or rows)
    rank = {"sent": 0, "pending": 1, "failed": 2, "dead": 3}
    pool.sort(key=lambda r: (rank.get(r.state, 9), r.created_at))
    return pool[0]


def enqueue_enrollment(db, *, grant, order, profile, revision: int, revoke: bool = False) -> IntegrationOutbox | None:
    kind = "enrollment.revoked" if revoke else "enrollment.desired"
    # Prefer existing stable or legacy :rN row — never insert a second peer event_id for the same grant.
    existing = _prefer_enrollment_row(_enrollment_rows_for_grant(db, grant.id, revoke=revoke))
    hints = _product_hints(order)
    course_id = hints.get("course_id") or grant.resource or ""
    commerce_product_id = hints.get("commerce_product_id") or order.product_id
    capability = grant.capability if grant.capability in {"course.view", "course.enter"} else "course.view"
    if capability == "course.enter":
        # Canonical wire capability is course.view; keep enter as alias field for LMS dual-accept.
        capability = "course.view"
    extra = profile.extra if isinstance(getattr(profile, "extra", None), dict) else {}
    keycloak_sub = str(extra.get("keycloak_sub") or "")
    # Wire event_id must match the outbox row peer already knows (legacy or stable).
    event_id = existing.event_id if existing else enrollment_event_id(grant.id, revoke=revoke)
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
        "project_id": order.project_id,
        "name": profile.name,
        "resource": grant.resource,
        "min_paid_percent": grant.min_paid_percent,
        "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
        "revision": revision,
        "revoke": revoke,
        "source": "voronka",
        "correlation_id": order.id,
    }
    if keycloak_sub:
        payload["keycloak_sub"] = keycloak_sub
    if hints.get("club"):
        payload["club"] = hints["club"]
    if existing:
        # Reuse legacy/stable row: refresh payload only; do not change delivery state here.
        existing.payload = payload
        if not existing.correlation_id:
            existing.correlation_id = order.id
        existing.updated_at = now()
        db.flush()
        return existing
    return enqueue_event(
        db,
        destination=DEST_LMS,
        kind=kind,
        event_id=event_id,
        correlation_id=order.id,
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
    if destination == DEST_SIGNAL:
        # Signal CE: HMAC over `timestamp.body` (same as solo_pr / raga7 emitters).
        return {
            "Content-Type": "application/json",
            "X-Signal-Source": "voronka",
            "X-Signal-Timestamp": ts,
            "X-Signal-Signature": digest,
        }
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
        # Keep neighbor money/access/CE events retryable when env is filled later
        # (solo_pr URL historically missing → permanent failed was wrong).
        if row.destination in {DEST_SIGNAL, DEST_SOLO, DEST_LMS}:
            row.state = "pending"
            row.last_error = "destination_not_configured"
            row.updated_at = now()
            row.next_attempt_at = now() + timedelta(seconds=60)
            return True
        row.state = "failed"
        row.last_error = "destination_not_configured"
        row.updated_at = now()
        return True
    if row.destination == DEST_SIGNAL:
        # CE intake expects a slim event body (not dual solo/lms envelope).
        payload = {
            "event_id": row.event_id,
            "event_type": "course.access_granted",
            "email": (row.payload or {}).get("email") or "",
            "service": (row.payload or {}).get("service") or "",
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    else:
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
