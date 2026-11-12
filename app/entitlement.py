"""Payment → entitlement job. Payment is never rolled back on job failure."""
from __future__ import annotations

from sqlalchemy import select

from app.crm_models import AccessGrant, EntitlementJob, Order, Payment, Profile
from app.crm_service import balance, uid
from app.db import now
from app import integrations


def queue_for_payment(db, payment: Payment) -> EntitlementJob:
    prior = db.scalar(select(EntitlementJob).where(EntitlementJob.payment_id == payment.id))
    if prior:
        return prior
    job = EntitlementJob(
        id=uid(),
        payment_id=payment.id,
        order_id=payment.order_id,
        state="pending",
        attempts=0,
        last_error="",
        result={},
        created_at=now(),
        updated_at=now(),
    )
    db.add(job)
    db.flush()
    return job


def process_job(db, job: EntitlementJob) -> bool:
    if job.state in {"applied", "waiting_grant"}:
        return False
    job.attempts += 1
    job.updated_at = now()
    payment = db.get(Payment, job.payment_id)
    order = db.get(Order, job.order_id)
    if not payment or not order:
        job.state = "failed"
        job.last_error = "payment_or_order_missing"
        return True
    profile = db.get(Profile, order.profile_id)
    financial = balance(db, order)
    try:
        integrations.enqueue_deal_paid(db, payment_id=payment.id, order=order, profile=profile, financial=financial)
        grants = list(db.scalars(select(AccessGrant).where(AccessGrant.order_id == order.id)))
        applied = []
        for grant in grants:
            # Financial threshold must hold before notifying neighbors.
            if financial["paid_minor"] * 100 < order.total_minor * grant.min_paid_percent:
                continue
            integrations.enqueue_access_event(
                db, kind="access.granted", grant=grant, order=order, profile=profile, financial=financial
            )
            # LMS enrollment uses canonical course.view (or course.enter alias).
            if grant.capability in {"course.view", "course.enter"}:
                integrations.enqueue_enrollment(
                    db, grant=grant, order=order, profile=profile, revision=job.attempts, revoke=False
                )
            applied.append(grant.id)
        if grants and not applied:
            job.state = "waiting_grant"
            job.last_error = "payment_threshold_not_met"
            job.result = {"grants": [], "financial": financial}
        elif not grants:
            job.state = "waiting_grant"
            job.last_error = "no_access_grant_yet"
            job.result = {"grants": [], "deal_paid": True, "financial": financial}
        else:
            job.state = "applied"
            job.last_error = ""
            job.result = {"grants": applied, "financial": financial, "deal_paid": True}
    except Exception as exc:
        job.state = "failed"
        job.last_error = type(exc).__name__[:80]
    return True


def process_pending(db, limit: int = 20) -> int:
    # Re-check waiting_grant jobs so late AccessGrant creation unlocks neighbors.
    rows = list(
        db.scalars(
            select(EntitlementJob)
            .where(EntitlementJob.state.in_(["pending", "waiting_grant", "failed"]))
            .order_by(EntitlementJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    done = 0
    for job in rows:
        if job.state == "failed" and job.attempts >= 8:
            continue
        if job.state == "waiting_grant":
            job.state = "pending"
        process_job(db, job)
        done += 1
    return done


def redact_robokassa_params(params: dict) -> dict:
    """Keep audit fields; never store password-bearing values."""
    allow = {"OutSum", "InvId", "IsTest", "Fee", "EMail", "PaymentMethod"}
    redacted = {k: str(params.get(k, ""))[:200] for k in allow if k in params}
    redacted["SignatureValue"] = "redacted"
    return redacted
