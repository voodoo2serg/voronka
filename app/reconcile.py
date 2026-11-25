"""OPS-104: detect and recover stuck payment / enrollment / publish events.

Extends crm_integration_outbox + entitlement_jobs — no second bus.
Never mutates Payment amounts or confirmation status.
"""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import or_, select

from app import entitlement, integrations
from app.crm_models import (
    AccessGrant,
    EntitlementJob,
    IntegrationInbox,
    IntegrationOutbox,
    Order,
    Payment,
    Profile,
)
from app.crm_service import balance
from app.db import now

Scope = Literal["payment", "enrollment", "publish", "all"]
SCOPES = frozenset({"payment", "enrollment", "publish", "all"})

# Manual DLQ requeue is capped per apply call; worker still caps delivery at 12.
APPLY_REQUEUE_CAP = 50


def _finding(
    *,
    finding_id: str,
    scope: str,
    code: str,
    summary: str,
    correlation_id: str | None = None,
    action: str = "none",
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "scope": scope,
        "code": code,
        "summary": summary,
        "correlation_id": correlation_id or "",
        "action": action,
        "refs": refs or {},
    }


def _project_orders(db, project_id: str | None) -> list[Order]:
    q = select(Order)
    if project_id:
        q = q.where(Order.project_id == project_id)
    return list(db.scalars(q.order_by(Order.created_at.desc()).limit(500)))


def _outbox_for_grant(db, grant_id: str, kind: str) -> list[IntegrationOutbox]:
    """Match stable event_id and legacy enrollment.desired:{grant}:rN rows."""
    stable = f"{kind}:{grant_id}"
    rows = list(
        db.scalars(
            select(IntegrationOutbox).where(
                or_(
                    IntegrationOutbox.event_id == stable,
                    IntegrationOutbox.event_id.like(f"{stable}:r%"),
                )
            )
        )
    )
    # Also match by payload.grant_id for safety.
    if not rows:
        candidates = list(
            db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == kind).limit(500))
        )
        rows = [r for r in candidates if (r.payload or {}).get("grant_id") == grant_id]
    return rows


def detect_payment(db, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    orders = {o.id: o for o in _project_orders(db, project_id)}
    if not orders:
        return findings
    payments = list(
        db.scalars(select(Payment).where(Payment.order_id.in_(list(orders.keys()))).order_by(Payment.created_at.desc()).limit(500))
    )
    for payment in payments:
        if len(findings) >= limit:
            break
        order = orders.get(payment.order_id)
        corr = payment.order_id
        job = db.scalar(select(EntitlementJob).where(EntitlementJob.payment_id == payment.id))
        if not job:
            findings.append(
                _finding(
                    finding_id=f"payment_missing_job:{payment.id}",
                    scope="payment",
                    code="payment_missing_job",
                    summary="Payment exists without EntitlementJob",
                    correlation_id=corr,
                    action="queue_entitlement_job",
                    refs={"payment_id": payment.id, "order_id": payment.order_id},
                )
            )
            continue
        if job.state == "failed" and job.attempts >= 8:
            findings.append(
                _finding(
                    finding_id=f"entitlement_exhausted:{job.id}",
                    scope="payment",
                    code="entitlement_exhausted",
                    summary="EntitlementJob failed after capped attempts",
                    correlation_id=job.correlation_id or corr,
                    action="requeue_entitlement_job",
                    refs={"job_id": job.id, "payment_id": payment.id, "order_id": payment.order_id, "attempts": job.attempts},
                )
            )
        elif job.state == "failed":
            findings.append(
                _finding(
                    finding_id=f"entitlement_failed:{job.id}",
                    scope="payment",
                    code="entitlement_failed",
                    summary="EntitlementJob failed; safe to requeue",
                    correlation_id=job.correlation_id or corr,
                    action="requeue_entitlement_job",
                    refs={"job_id": job.id, "payment_id": payment.id, "order_id": payment.order_id},
                )
            )
        deal_id = f"deal.paid:{payment.id}"
        deal = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_id == deal_id))
        if payment.kind == "payment" and job.state == "applied" and not deal:
            findings.append(
                _finding(
                    finding_id=f"deal_paid_missing:{payment.id}",
                    scope="payment",
                    code="deal_paid_missing",
                    summary="Applied entitlement but deal.paid outbox missing",
                    correlation_id=corr,
                    action="reprocess_entitlement_job",
                    refs={"payment_id": payment.id, "order_id": payment.order_id, "job_id": job.id},
                )
            )
        elif deal and deal.state == "dead":
            findings.append(
                _finding(
                    finding_id=f"outbox_dead:{deal.event_id}",
                    scope="payment",
                    code="outbox_dead",
                    summary="deal.paid in DLQ (dead)",
                    correlation_id=deal.correlation_id or corr,
                    action="requeue_outbox",
                    refs={"event_id": deal.event_id, "outbox_id": deal.id, "destination": deal.destination},
                )
            )
        # Waiting for grant too long is informational when no AccessGrant yet.
        if job.state == "waiting_grant" and order:
            grants = list(db.scalars(select(AccessGrant).where(AccessGrant.order_id == order.id)))
            if not grants:
                findings.append(
                    _finding(
                        finding_id=f"waiting_grant_no_access:{job.id}",
                        scope="payment",
                        code="waiting_grant_no_access",
                        summary="Payment applied path waiting; no AccessGrant yet (owner must grant)",
                        correlation_id=job.correlation_id or corr,
                        action="none",
                        refs={"job_id": job.id, "payment_id": payment.id, "order_id": payment.order_id},
                    )
                )
    return findings[:limit]


def detect_enrollment(db, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    orders = _project_orders(db, project_id)
    for order in orders:
        if len(findings) >= limit:
            break
        financial = balance(db, order)
        grants = list(db.scalars(select(AccessGrant).where(AccessGrant.order_id == order.id)))
        for grant in grants:
            if grant.capability not in {"course.view", "course.enter"}:
                continue
            expiry = grant.expires_at
            if expiry and expiry.tzinfo is None:
                from datetime import timezone
                expiry = expiry.replace(tzinfo=timezone.utc)
            allowed = (
                order.state == "open"
                and (not expiry or expiry > now())
                and financial["paid_minor"] * 100 >= order.total_minor * grant.min_paid_percent
            )
            desired_kind = "enrollment.desired" if allowed else "enrollment.revoked"
            history = (_outbox_for_grant(db, grant.id, "enrollment.desired")
                       + _outbox_for_grant(db, grant.id, "enrollment.revoked"))
            latest = max(history, key=lambda r: (
                int((r.payload or {}).get("revision") or 1), r.created_at, r.event_id
            ), default=None)
            if not allowed and not latest:
                continue
            if latest and latest.kind != desired_kind:
                findings.append(_finding(
                    finding_id=f"enrollment_state_mismatch:{grant.id}",
                    scope="enrollment", code="enrollment_state_mismatch",
                    summary="Latest enrollment event differs from current CRM access decision",
                    correlation_id=order.id, action="reconcile_grant_state",
                    refs={"grant_id": grant.id, "order_id": order.id},
                ))
                continue
            rows = [latest] if latest else []
            sent = [r for r in rows if r.state == "sent"]
            dead = [r for r in rows if r.state == "dead"]
            pending = [r for r in rows if r.state in {"pending", "failed"}]
            if sent:
                continue
            if dead and not pending:
                primary = dead[0]
                findings.append(
                    _finding(
                        finding_id=f"outbox_dead:{primary.event_id}",
                        scope="enrollment",
                        code="enrollment_dead",
                        summary="enrollment.desired in DLQ; LMS may have been down",
                        correlation_id=primary.correlation_id or order.id,
                        action="requeue_outbox",
                        refs={
                            "event_id": primary.event_id,
                            "outbox_id": primary.id,
                            "grant_id": grant.id,
                            "order_id": order.id,
                            "legacy_event_ids": [r.event_id for r in dead],
                        },
                    )
                )
            elif not rows:
                findings.append(
                    _finding(
                        finding_id=f"enrollment_missing:{grant.id}",
                        scope="enrollment",
                        code="enrollment_missing",
                        summary="Paid AccessGrant without enrollment.desired outbox",
                        correlation_id=order.id,
                        action="enqueue_enrollment",
                        refs={"grant_id": grant.id, "order_id": order.id},
                    )
                )
            if not allowed:
                continue
            # Dead access.granted tied to same grant (solo_pr wire key still uses dots).
            access_event_id = f"access.granted:{grant.id}"
            access_row = db.scalar(
                select(IntegrationOutbox).where(IntegrationOutbox.event_id == access_event_id)
            )
            if access_row and access_row.state == "dead":
                findings.append(
                    _finding(
                        finding_id=f"outbox_dead:{access_row.event_id}",
                        scope="enrollment",
                        code="outbox_dead",
                        summary="access.granted in DLQ",
                        correlation_id=access_row.correlation_id or order.id,
                        action="requeue_outbox",
                        refs={
                            "event_id": access_row.event_id,
                            "outbox_id": access_row.id,
                            "grant_id": grant.id,
                        },
                    )
                )
            # Signal CE: modern course_access_granted:{grant} + legacy dotted key.
            signal_rows = integrations.signal_access_outbox_for_grant(db, grant.id)
            dead_signal = [r for r in signal_rows if r.state == "dead"]
            if dead_signal:
                primary = dead_signal[0]
                findings.append(
                    _finding(
                        finding_id=f"outbox_dead:{primary.event_id}",
                        scope="enrollment",
                        code="outbox_dead",
                        summary="course.access_granted in DLQ (Signal CE notify)",
                        correlation_id=primary.correlation_id or order.id,
                        action="requeue_outbox",
                        refs={
                            "event_id": primary.event_id,
                            "outbox_id": primary.id,
                            "grant_id": grant.id,
                            "legacy_event_ids": [r.event_id for r in dead_signal],
                        },
                    )
                )
    return findings[:limit]


def detect_publish(db, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Voronka-owned publish-adjacent gaps. wi-ted remote_status recovery is Signal-side."""
    findings: list[dict[str, Any]] = []
    q = select(IntegrationOutbox).where(
        IntegrationOutbox.state == "dead",
        IntegrationOutbox.kind.in_(["course.access_granted"]),
    ).order_by(IntegrationOutbox.updated_at.desc()).limit(200)
    for row in db.scalars(q):
        if len(findings) >= limit:
            break
        payload = row.payload or {}
        if project_id and payload.get("project_id") not in {None, project_id}:
            # course.access_granted payload may omit project_id — allow when order_id present.
            order_id = payload.get("order_id")
            if order_id:
                order = db.get(Order, order_id)
                if order and order.project_id != project_id:
                    continue
            elif payload.get("project_id") is not None:
                continue
        findings.append(
            _finding(
                finding_id=f"outbox_dead:{row.event_id}",
                scope="publish",
                code="signal_access_dead",
                summary="course.access_granted in DLQ (Signal CE notify)",
                correlation_id=row.correlation_id or payload.get("order_id") or "",
                action="requeue_outbox",
                refs={"event_id": row.event_id, "outbox_id": row.id, "destination": row.destination},
            )
        )
    # scenario.publish inbox is already idempotent; surface count for OPS-105 only when failed state appears.
    inbox_q = select(IntegrationInbox).where(
        IntegrationInbox.kind.in_(["scenario.publish", "scenario.published"]),
        IntegrationInbox.state.in_(["failed", "pending"]),
    ).limit(50)
    for inbox in db.scalars(inbox_q):
        if len(findings) >= limit:
            break
        payload = inbox.payload or {}
        if project_id and payload.get("project_id") not in {None, project_id}:
            continue
        findings.append(
            _finding(
                finding_id=f"publish_inbox_stuck:{inbox.id}",
                scope="publish",
                code="scenario_publish_stuck",
                summary="scenario.publish inbox not applied",
                correlation_id=inbox.correlation_id or payload.get("correlation_id") or "",
                action="none",
                refs={"inbox_id": inbox.id, "event_key": inbox.event_key, "note": "wi-ted remote_status recovery lives in Signal"},
            )
        )
    # Boundary note when nothing else — only if explicitly scoped publish and empty.
    return findings[:limit]


def detect_dlq(db, project_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    rows = list(
        db.scalars(
            select(IntegrationOutbox)
            .where(IntegrationOutbox.state == "dead")
            .order_by(IntegrationOutbox.updated_at.desc())
            .limit(300)
        )
    )
    for row in rows:
        if len(findings) >= limit:
            break
        payload = row.payload or {}
        if project_id:
            pid = payload.get("project_id")
            if pid is not None and pid != project_id:
                continue
            if pid is None and payload.get("order_id"):
                order = db.get(Order, payload["order_id"])
                if order and order.project_id != project_id:
                    continue
        findings.append(
            _finding(
                finding_id=f"outbox_dead:{row.event_id}",
                scope="payment" if row.kind in {"deal.paid", "access.granted", "access.revoked"} else (
                    "enrollment" if row.kind.startswith("enrollment.") else "publish"
                ),
                code="outbox_dead",
                summary=f"{row.kind} → {row.destination} dead after {row.attempts} attempts",
                correlation_id=row.correlation_id or payload.get("order_id") or "",
                action="requeue_outbox",
                refs={
                    "event_id": row.event_id,
                    "outbox_id": row.id,
                    "destination": row.destination,
                    "kind": row.kind,
                    "last_error": row.last_error,
                    "attempts": row.attempts,
                },
            )
        )
    return findings[:limit]


def preview(
    db,
    *,
    scope: Scope = "all",
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise ValueError(f"invalid scope: {scope}")
    findings: list[dict[str, Any]] = []
    if scope in {"payment", "all"}:
        findings.extend(detect_payment(db, project_id=project_id, limit=limit))
    if scope in {"enrollment", "all"}:
        findings.extend(detect_enrollment(db, project_id=project_id, limit=limit))
    if scope in {"publish", "all"}:
        findings.extend(detect_publish(db, project_id=project_id, limit=limit))
    # Attach unmatched DLQ rows, filtered to the requested scope (payment-only
    # apply must not requeue enrollment/publish dead rows).
    seen = {f["finding_id"] for f in findings}
    for f in detect_dlq(db, project_id=project_id, limit=limit):
        if scope != "all" and f["scope"] != scope:
            continue
        if f["finding_id"] not in seen:
            findings.append(f)
            seen.add(f["finding_id"])
    findings = findings[:limit]
    return {
        "scope": scope,
        "project_id": project_id,
        "dry_run": True,
        "count": len(findings),
        "findings": findings,
        "boundary": {
            "payment_immutable": True,
            "wi_ted_status_recovery": "signal",
            "bus": "crm_integration_outbox + crm_entitlement_jobs",
        },
    }


def requeue_outbox_row(db, row: IntegrationOutbox) -> dict[str, Any]:
    """dead/failed → pending with attempt counter reset for a fresh capped window."""
    prior_event_id = row.event_id
    prior_state = row.state
    # Adopt dotted Signal CE keys before retry so delivery is not doomed to http_422.
    if row.kind == "course.access_granted":
        row = integrations.adopt_signal_access_outbox(db, row)
        grant_id = (row.payload or {}).get("grant_id")
        if grant_id:
            integrations.adopt_signal_access_interaction(db, grant_id=str(grant_id))
    row.state = "pending"
    row.attempts = 0
    row.next_attempt_at = now()
    row.last_error = ""
    row.updated_at = now()
    result = {"event_id": row.event_id, "from": prior_state, "to": "pending"}
    if prior_event_id != row.event_id:
        result["adopted_from"] = prior_event_id
    return result


def apply_findings(
    db,
    *,
    scope: Scope = "all",
    project_id: str | None = None,
    finding_ids: list[str] | None = None,
    limit: int = APPLY_REQUEUE_CAP,
) -> dict[str, Any]:
    """Mutating reconcile. Never creates a second Payment or changes payment status."""
    plan = preview(db, scope=scope, project_id=project_id, limit=max(limit, 200))
    wanted = set(finding_ids) if finding_ids else None
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for finding in plan["findings"]:
        if wanted is not None and finding["finding_id"] not in wanted:
            continue
        if len(applied) >= limit:
            skipped.append({**finding, "reason": "apply_cap"})
            continue
        action = finding["action"]
        refs = finding["refs"]
        if action == "none":
            skipped.append({**finding, "reason": "no_mutate_action"})
            continue
        if action == "queue_entitlement_job":
            payment = db.get(Payment, refs["payment_id"])
            if not payment:
                skipped.append({**finding, "reason": "payment_missing"})
                continue
            job = entitlement.queue_for_payment(db, payment)
            if not job.correlation_id:
                job.correlation_id = payment.order_id
            applied.append({"finding_id": finding["finding_id"], "result": "queued_job", "job_id": job.id})
        elif action in {"requeue_entitlement_job", "reprocess_entitlement_job"}:
            job = db.get(EntitlementJob, refs["job_id"])
            if not job:
                skipped.append({**finding, "reason": "job_missing"})
                continue
            job.state = "pending"
            job.attempts = 0
            job.last_error = ""
            job.updated_at = now()
            if not job.correlation_id:
                job.correlation_id = job.order_id
            entitlement.process_job(db, job)
            applied.append({"finding_id": finding["finding_id"], "result": "reprocessed_job", "job_id": job.id, "state": job.state})
        elif action == "requeue_outbox":
            event_id = refs.get("event_id")
            row = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.event_id == event_id))
            if not row and refs.get("outbox_id"):
                row = db.get(IntegrationOutbox, refs["outbox_id"])
            if not row:
                skipped.append({**finding, "reason": "outbox_missing"})
                continue
            # Also requeue legacy enrollment :rN / Signal dotted CE siblings when present.
            siblings = []
            grant_id = refs.get("grant_id")
            if grant_id and row.kind.startswith("enrollment."):
                siblings = [r for r in _outbox_for_grant(db, grant_id, row.kind) if r.state == "dead"]
            elif grant_id and row.kind == "course.access_granted":
                siblings = [
                    r for r in integrations.signal_access_outbox_for_grant(db, grant_id) if r.state == "dead"
                ]
            targets = siblings or [row]
            # Requeue only existing rows (legacy or stable). Do not insert a second event_id.
            # Signal requeue adopts dotted → CE-safe event_id inside requeue_outbox_row.
            results = [requeue_outbox_row(db, r) for r in targets]
            applied.append({"finding_id": finding["finding_id"], "result": "requeued_outbox", "rows": results})
        elif action == "reconcile_grant_state":
            order = db.scalar(select(Order).where(Order.id == refs["order_id"]).with_for_update())
            grant = db.get(AccessGrant, refs["grant_id"])
            if not order or not grant or grant.order_id != order.id:
                skipped.append({**finding, "reason": "grant_or_order_missing"})
                continue
            profile = db.get(Profile, order.profile_id)
            if not profile:
                skipped.append({**finding, "reason": "profile_missing"})
                continue
            from datetime import timezone
            expiry = grant.expires_at
            if expiry and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            financial = balance(db, order)
            allowed = (order.state == "open" and (not expiry or expiry > now())
                       and financial["paid_minor"] * 100 >= order.total_minor * grant.min_paid_percent)
            integrations.enqueue_access_event(
                db, kind="access.granted" if allowed else "access.revoked",
                grant=grant, order=order, profile=profile, financial=financial)
            integrations.enqueue_enrollment(
                db, grant=grant, order=order, profile=profile, revision=1, revoke=not allowed)
            applied.append({"finding_id": finding["finding_id"], "result": "reconciled_grant", "grant_id": grant.id})
        elif action == "enqueue_enrollment":
            grant = db.get(AccessGrant, refs["grant_id"])
            order = db.get(Order, refs["order_id"])
            if not grant or not order:
                skipped.append({**finding, "reason": "grant_or_order_missing"})
                continue
            profile = db.get(Profile, order.profile_id)
            if not profile:
                skipped.append({**finding, "reason": "profile_missing"})
                continue
            integrations.enqueue_enrollment(
                db, grant=grant, order=order, profile=profile, revision=1, revoke=False
            )
            applied.append({"finding_id": finding["finding_id"], "result": "enqueued_enrollment", "grant_id": grant.id})
        else:
            skipped.append({**finding, "reason": f"unknown_action:{action}"})
    return {
        "scope": scope,
        "project_id": project_id,
        "dry_run": False,
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped,
        "payment_immutable": True,
    }


def _outbox_matches_project(db, row: IntegrationOutbox, project_id: str | None) -> bool:
    if not project_id:
        return True
    payload = row.payload or {}
    pid = payload.get("project_id")
    if pid is not None:
        return pid == project_id
    order_id = payload.get("order_id") or row.correlation_id
    if order_id:
        order = db.get(Order, order_id)
        return bool(order and order.project_id == project_id)
    # No project hint — exclude from project-scoped counts (avoid leaking other projects).
    return False


def _count_outbox_for_project(db, state: str, project_id: str | None) -> int:
    rows = list(
        db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.state == state).limit(2000))
    )
    return sum(1 for r in rows if _outbox_matches_project(db, r, project_id))


def _count_entitlement_for_project(db, states: list[str], project_id: str | None) -> int:
    q = select(EntitlementJob).where(EntitlementJob.state.in_(states))
    if project_id:
        order_ids = [o.id for o in _project_orders(db, project_id)]
        if not order_ids:
            return 0
        q = q.where(EntitlementJob.order_id.in_(order_ids))
    return len(list(db.scalars(q.limit(2000))))


def aggregate_status(db, project_id: str | None = None) -> dict[str, Any]:
    """Scoped M2M feed for OPS-105 — counts only, no secrets/payloads."""
    dead = _count_outbox_for_project(db, "dead", project_id)
    pending = _count_outbox_for_project(db, "pending", project_id)
    failed = _count_outbox_for_project(db, "failed", project_id)
    entitlement_stuck = _count_entitlement_for_project(db, ["failed", "waiting_grant"], project_id)
    entitlement_pending = _count_entitlement_for_project(db, ["pending"], project_id)
    preview_all = preview(db, scope="all", project_id=project_id, limit=50)
    by_code: dict[str, int] = {}
    for f in preview_all["findings"]:
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    dlq_sample = [
        {
            "event_id": f["refs"].get("event_id"),
            "correlation_id": f["correlation_id"],
            "kind": f["refs"].get("kind") or f["code"],
            "destination": f["refs"].get("destination"),
            "attempts": f["refs"].get("attempts"),
            "last_error": f["refs"].get("last_error"),
        }
        for f in preview_all["findings"]
        if f["code"] in {"outbox_dead", "enrollment_dead", "signal_access_dead"}
    ][:20]
    return {
        "project_id": project_id,
        "integration_outbox": {"pending": pending, "failed": failed, "dead": dead},
        "entitlement_jobs": {"pending": entitlement_pending, "stuck": entitlement_stuck},
        "findings_sample_count": preview_all["count"],
        "findings_by_code": by_code,
        "dlq_sample": dlq_sample,
        "auto_retry_cap_attempts": 12,
        "shared_database": False,
    }
