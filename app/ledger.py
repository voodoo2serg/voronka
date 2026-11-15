"""Receivables / allocations / refunds ledger (PAY-002). Money SoR remains Voronka.

Existing crm_payments rows stay the confirmed money facts; these tables allocate them
to order lines / receivables without float math.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from app.crm_models import Order, OrderLine, Payment, PaymentAllocation, Receivable, Refund
from app.crm_service import uid
from app.db import now


def ensure_order_ledger(db, order: Order) -> tuple[OrderLine, list[Receivable]]:
    """Create OrderLine + Receivables for an order if missing (idempotent)."""
    line = db.scalar(select(OrderLine).where(OrderLine.order_id == order.id))
    if line is None:
        currency = (order.snapshot or {}).get("currency") or "RUB"
        line = OrderLine(
            id=uid(),
            order_id=order.id,
            product_id=order.product_id,
            quantity=1,
            amount_minor=order.total_minor,
            currency=currency,
            snapshot=dict(order.snapshot or {}),
        )
        db.add(line)
        db.flush()
    receivables = list(
        db.scalars(select(Receivable).where(Receivable.order_id == order.id).order_by(Receivable.sequence))
    )
    if receivables:
        return line, receivables
    installments = order.installments or []
    if installments:
        for index, part in enumerate(installments, start=1):
            due_at = part.get("due_at")
            if isinstance(due_at, str):
                due_at = datetime.fromisoformat(due_at)
            receivables.append(
                Receivable(
                    id=uid(),
                    order_id=order.id,
                    order_line_id=line.id,
                    sequence=index,
                    due_minor=int(part["amount_minor"]),
                    due_at=due_at,
                    state="open",
                    allocated_minor=0,
                    created_at=now(),
                )
            )
    else:
        receivables.append(
            Receivable(
                id=uid(),
                order_id=order.id,
                order_line_id=line.id,
                sequence=1,
                due_minor=order.total_minor,
                due_at=None,
                state="open",
                allocated_minor=0,
                created_at=now(),
            )
        )
    for row in receivables:
        db.add(row)
    db.flush()
    return line, receivables


def _refresh_receivable_state(recv: Receivable) -> None:
    if recv.allocated_minor <= 0:
        recv.state = "open"
    elif recv.allocated_minor < recv.due_minor:
        recv.state = "partial"
    else:
        recv.state = "paid"


def allocate_payment(db, payment: Payment, order: Order) -> list[PaymentAllocation]:
    """Distribute a confirmed payment across open receivables (FIFO). Overpay stays on last/open remainder."""
    if payment.kind != "payment":
        return []
    existing = list(
        db.scalars(
            select(PaymentAllocation).where(
                PaymentAllocation.payment_id == payment.id,
                PaymentAllocation.state == "active",
            )
        )
    )
    if existing:
        return existing
    _, receivables = ensure_order_ledger(db, order)
    remaining = int(payment.amount_minor)
    created: list[PaymentAllocation] = []
    for recv in receivables:
        if remaining <= 0:
            break
        open_minor = max(0, int(recv.due_minor) - int(recv.allocated_minor))
        if open_minor <= 0:
            continue
        take = min(open_minor, remaining)
        alloc = PaymentAllocation(
            id=uid(),
            payment_id=payment.id,
            order_id=order.id,
            receivable_id=recv.id,
            amount_minor=take,
            state="active",
            created_at=now(),
        )
        db.add(alloc)
        recv.allocated_minor = int(recv.allocated_minor) + take
        _refresh_receivable_state(recv)
        remaining -= take
        created.append(alloc)
    if remaining > 0:
        # Overpayment: allocate remainder without receivable (explicit overpay fact)
        alloc = PaymentAllocation(
            id=uid(),
            payment_id=payment.id,
            order_id=order.id,
            receivable_id=None,
            amount_minor=remaining,
            state="active",
            created_at=now(),
        )
        db.add(alloc)
        created.append(alloc)
    db.flush()
    return created


def record_refund_ledger(db, refund_payment: Payment, order: Order, reason: str = "") -> Refund:
    """Link a refund Payment to prior payment allocations; reverse FIFO allocations."""
    if refund_payment.kind != "refund":
        raise HTTPException(422, "Ожидается платёж kind=refund")
    prior = db.scalar(select(Refund).where(Refund.refund_payment_id == refund_payment.id))
    if prior:
        return prior
    # Prefer reversing from most recent active allocations on this order
    allocations = list(
        db.scalars(
            select(PaymentAllocation)
            .where(
                PaymentAllocation.order_id == order.id,
                PaymentAllocation.state == "active",
            )
            .order_by(PaymentAllocation.created_at.desc())
        )
    )
    remaining = int(refund_payment.amount_minor)
    source_payment_id = allocations[0].payment_id if allocations else refund_payment.id
    for alloc in allocations:
        if remaining <= 0:
            break
        take = min(int(alloc.amount_minor), remaining)
        if take == int(alloc.amount_minor):
            alloc.state = "reversed"
        else:
            alloc.amount_minor = int(alloc.amount_minor) - take
            # split remainder as reversed fact
            db.add(
                PaymentAllocation(
                    id=uid(),
                    payment_id=alloc.payment_id,
                    order_id=order.id,
                    receivable_id=alloc.receivable_id,
                    amount_minor=take,
                    state="reversed",
                    created_at=now(),
                )
            )
        if alloc.receivable_id:
            recv = db.get(Receivable, alloc.receivable_id)
            if recv:
                recv.allocated_minor = max(0, int(recv.allocated_minor) - take)
                _refresh_receivable_state(recv)
        source_payment_id = alloc.payment_id
        remaining -= take
    row = Refund(
        id=uid(),
        payment_id=source_payment_id,
        refund_payment_id=refund_payment.id,
        amount_minor=int(refund_payment.amount_minor),
        status="confirmed",
        reason=reason or refund_payment.evidence or "",
        confirmed_at=now(),
        created_at=now(),
    )
    db.add(row)
    db.flush()
    return row


def ledger_summary(db, order: Order) -> dict:
    """Integer-only ledger view; complements crm_service.balance()."""
    ensure_order_ledger(db, order)
    receivables = list(db.scalars(select(Receivable).where(Receivable.order_id == order.id)))
    allocations = list(
        db.scalars(
            select(PaymentAllocation).where(
                PaymentAllocation.order_id == order.id,
                PaymentAllocation.state == "active",
            )
        )
    )
    refunds = list(
        db.scalars(
            select(Refund)
            .join(Payment, Payment.id == Refund.refund_payment_id)
            .where(Payment.order_id == order.id, Refund.status == "confirmed")
        )
    )
    due = sum(int(r.due_minor) for r in receivables)
    allocated = sum(int(a.amount_minor) for a in allocations if a.receivable_id)
    overpay = sum(int(a.amount_minor) for a in allocations if a.receivable_id is None)
    refunded = sum(int(r.amount_minor) for r in refunds)
    lines = list(db.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
    return {
        "order_id": order.id,
        "due_minor": due,
        "allocated_minor": allocated,
        "overpayment_minor": overpay,
        "refunded_minor": refunded,
        "open_minor": max(0, due - allocated),
        "lines": [
            {
                "id": line.id,
                "product_id": line.product_id,
                "amount_minor": line.amount_minor,
                "currency": line.currency,
                "quantity": line.quantity,
            }
            for line in lines
        ],
        "receivables": [
            {
                "id": r.id,
                "sequence": r.sequence,
                "due_minor": r.due_minor,
                "allocated_minor": r.allocated_minor,
                "state": r.state,
                "due_at": r.due_at,
            }
            for r in receivables
        ],
    }
