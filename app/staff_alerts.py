"""Staff payment alerts: paid / not paid / amount threshold → owner channel."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select

from app.crm_models import StaffAlertEvent, StaffAlertRule
from app.crm_service import uid
from app.db import now


def _owner_telegram_id() -> int | None:
    raw = (os.getenv("OWNER_TELEGRAM_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _notify_email(subject: str, body: str) -> dict[str, Any]:
    host = os.getenv("SMTP_HOST")
    frm = os.getenv("SMTP_FROM")
    to = (os.getenv("STAFF_ALERT_EMAIL") or frm or "").strip()
    if not host or not frm or not to:
        return {"channel": "email", "ok": False, "reason": "smtp_or_recipient_missing"}
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = frm
    msg["To"] = to
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or "587"), timeout=20) as smtp:
            if os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
                smtp.starttls()
                smtp.login(os.getenv("SMTP_USER") or "", os.getenv("SMTP_PASSWORD") or "")
            smtp.send_message(msg)
        return {"channel": "email", "ok": True, "to": to}
    except Exception as exc:  # noqa: BLE001 — report to audit payload
        return {"channel": "email", "ok": False, "reason": str(exc)[:200]}


def _notify_telegram(text: str) -> dict[str, Any]:
    """Queue-free best-effort: record intent; TG delivery via ADMIN_BOT when configured."""
    token = os.getenv("ADMIN_BOT_TOKEN") or os.getenv("TG_MAIN_TOKEN")
    chat = _owner_telegram_id()
    if not token or not chat:
        return {"channel": "telegram", "ok": False, "reason": "bot_or_owner_missing"}
    try:
        import httpx

        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text[:3500]},
            timeout=15,
        )
        return {"channel": "telegram", "ok": r.is_success, "status": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"channel": "telegram", "ok": False, "reason": str(exc)[:200]}


def _deliver_rule(
    db,
    rule: StaffAlertRule,
    *,
    kind: str,
    project_id: str | None,
    order_id: str,
    amount_minor: int,
    paid: bool,
    provider: str,
    skip_dedupe: bool = False,
) -> dict | None:
    if rule.project_id and project_id and rule.project_id != project_id:
        return None
    if kind == "amount_threshold" and amount_minor < int(rule.amount_minor or 0):
        return None
    if kind == "paid" and not paid:
        return None
    if kind == "not_paid" and paid:
        return None
    dedupe = f"{kind}:{order_id}:{amount_minor}:{provider}"
    if not skip_dedupe:
        prior = db.scalar(
            select(StaffAlertEvent).where(
                StaffAlertEvent.rule_id == rule.id,
                StaffAlertEvent.dedupe_key == dedupe,
            )
        )
        if prior:
            return None
    subject = f"CRM платёж · {kind} · {amount_minor / 100:.2f} ₽"
    body = (
        f"Событие: {kind}\nЗаказ: {order_id}\nПроект: {project_id or '—'}\n"
        f"Сумма (коп.): {amount_minor}\nПровайдер: {provider}\nОплачено: {paid}"
    )
    deliveries = []
    if rule.channel in {"telegram", "both"}:
        deliveries.append(_notify_telegram(f"{subject}\n{body}"))
    if rule.channel in {"email", "both"}:
        deliveries.append(_notify_email(subject, body))
    row = StaffAlertEvent(
        id=uid(),
        rule_id=rule.id,
        dedupe_key=dedupe if not skip_dedupe else f"test:{dedupe}:{uid()}",
        payload={"deliveries": deliveries, "order_id": order_id, "amount_minor": amount_minor},
        state="sent" if any(d.get("ok") for d in deliveries) else "failed",
        created_at=now(),
    )
    db.add(row)
    return {"rule_id": rule.id, "state": row.state, "deliveries": deliveries}


def fire_payment_alerts(
    db,
    *,
    kind: str,
    project_id: str | None,
    order_id: str,
    amount_minor: int,
    paid: bool,
    provider: str,
) -> list[dict]:
    """Evaluate active rules and send once per (rule, dedupe_key)."""
    rules = list(
        db.scalars(
            select(StaffAlertRule).where(
                StaffAlertRule.active.is_(True),
                StaffAlertRule.kind == kind,
            )
        )
    )
    results = []
    for rule in rules:
        item = _deliver_rule(
            db,
            rule,
            kind=kind,
            project_id=project_id,
            order_id=order_id,
            amount_minor=amount_minor,
            paid=paid,
            provider=provider,
        )
        if item:
            results.append(item)
    return results


def fire_single_rule(
    db,
    rule: StaffAlertRule,
    *,
    order_id: str,
    amount_minor: int,
    paid: bool,
    provider: str = "test",
) -> list[dict]:
    """CRM-R9: test/fire exactly one rule (even if inactive); never fan-out to siblings."""
    item = _deliver_rule(
        db,
        rule,
        kind=rule.kind,
        project_id=rule.project_id,
        order_id=order_id,
        amount_minor=amount_minor,
        paid=paid,
        provider=provider,
        skip_dedupe=True,
    )
    return [item] if item else []
