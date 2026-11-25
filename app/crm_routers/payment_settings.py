"""Payment provider credential forms + staff alert rules (CRM-R9 / A11–A12)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app import connection_vault as vault
from app import payment_vault
from app import portals
from app import staff_alerts
from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner
from app.crm_models import Invoice, Order, StaffAlertRule
from app.crm_service import audit, project_access, uid
from app.db import now

router = APIRouter()


class ProviderCredentials(Body):
    """Partial update — omit secrets to leave unchanged; empty string clears."""
    provider: Literal["robokassa", "yookassa"]
    fields: dict[str, str | None] = Field(default_factory=dict)
    project_id: str | None = None


class AlertRuleBody(Body):
    kind: Literal["paid", "not_paid", "amount_threshold"]
    channel: Literal["telegram", "email", "both"] = "telegram"
    amount_minor: int = Field(default=0, ge=0)
    active: bool = True
    project_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AlertRulePatch(Body):
    channel: Literal["telegram", "email", "both"] | None = None
    amount_minor: int | None = Field(default=None, ge=0)
    active: bool | None = None
    config: dict[str, Any] | None = None


ROBOKASSA_FIELD_MAP = {
    "login": "ROBOKASSA_LOGIN",
    "test_mode": "ROBOKASSA_TEST",
    "test_password1": "ROBOKASSA_TEST_PASSWORD1",
    "test_password2": "ROBOKASSA_TEST_PASSWORD2",
    "password1": "ROBOKASSA_PASSWORD1",
    "password2": "ROBOKASSA_PASSWORD2",
    "hash": "ROBOKASSA_HASH",
    "receipt_tax": "ROBOKASSA_RECEIPT_TAX",
    "receipt_sno": "ROBOKASSA_RECEIPT_SNO",
}

YOOKASSA_FIELD_MAP = {
    "enabled": "YOOKASSA_ENABLED",
    "shop_id": "YOOKASSA_SHOP_ID",
    "secret_key": "YOOKASSA_SECRET_KEY",
    "webhook_secret": "YOOKASSA_WEBHOOK_SECRET",
}


def _fields_to_vault_parts(provider: str, fields: dict[str, str | None]) -> tuple[dict[str, str], dict[str, Any]]:
    """Split CRM form fields into Connection Vault secrets + meta."""
    secrets: dict[str, str] = {}
    meta: dict[str, Any] = {}
    if provider == "robokassa":
        for key in ("test_password1", "test_password2", "password1", "password2"):
            val = fields.get(key)
            if val is not None and str(val).strip():
                secrets[key] = str(val).strip()
        if fields.get("login") is not None and str(fields.get("login") or "").strip():
            meta["login"] = str(fields["login"]).strip()
        if fields.get("test_mode") is not None and str(fields.get("test_mode") or "").strip() != "":
            raw = str(fields["test_mode"]).strip().lower()
            meta["test_mode"] = raw in {"1", "true", "yes", "on"}
        if fields.get("hash") is not None and str(fields.get("hash") or "").strip():
            meta["hash"] = str(fields["hash"]).strip()
        if fields.get("receipt_tax") is not None and str(fields.get("receipt_tax") or "").strip():
            meta["receipt_tax"] = str(fields["receipt_tax"]).strip()
        if fields.get("receipt_sno") is not None and str(fields.get("receipt_sno") or "").strip():
            meta["receipt_sno"] = str(fields["receipt_sno"]).strip()
    else:
        for key in ("secret_key", "webhook_secret"):
            val = fields.get(key)
            if val is not None and str(val).strip():
                secrets[key] = str(val).strip()
        if fields.get("shop_id") is not None and str(fields.get("shop_id") or "").strip():
            meta["shop_id"] = str(fields["shop_id"]).strip()
        if fields.get("enabled") is not None and str(fields.get("enabled") or "").strip() != "":
            raw = str(fields["enabled"]).strip().lower()
            meta["enabled"] = raw in {"1", "true", "yes", "on"}
    return secrets, meta


@router.get("/payment-providers/credentials")
def credentials_status(admin=Depends(crm_actor)):
    staff_owner(admin)
    payment_vault.ensure_runtime(force=True)
    present = payment_vault.presence()
    return {
        "vault_path_configured": bool((__import__("os").getenv("CRM_PAYMENT_SECRETS_PATH") or "").strip()),
        "robokassa": {
            "fields": {
                "login": present.get("ROBOKASSA_LOGIN"),
                "test_mode": (__import__("os").getenv("ROBOKASSA_TEST", "1") == "1"),
                "test_password1": present.get("ROBOKASSA_TEST_PASSWORD1"),
                "test_password2": present.get("ROBOKASSA_TEST_PASSWORD2"),
                "password1": present.get("ROBOKASSA_PASSWORD1"),
                "password2": present.get("ROBOKASSA_PASSWORD2"),
                "hash": __import__("os").getenv("ROBOKASSA_HASH", "sha256"),
                "receipt_tax": __import__("os").getenv("ROBOKASSA_RECEIPT_TAX", ""),
                "receipt_sno": __import__("os").getenv("ROBOKASSA_RECEIPT_SNO", ""),
            },
            "status": portals.robokassa_status(),
            "secrets_exposed": False,
        },
        "yookassa": {
            "fields": {
                "enabled": __import__("os").getenv("YOOKASSA_ENABLED", "0") == "1",
                "shop_id": present.get("YOOKASSA_SHOP_ID"),
                "secret_key": present.get("YOOKASSA_SECRET_KEY"),
                "webhook_secret": present.get("YOOKASSA_WEBHOOK_SECRET"),
            },
            "status": portals.yookassa_skeleton(),
            "secrets_exposed": False,
        },
        "live_money_note": (
            "Песочница OK (Q11). ROBOKASSA_LIVE_READY и боевой ЮKassa не включаются из UI — "
            "только решение владельца после секретов и чек-листа."
        ),
    }


@router.put("/payment-providers/credentials")
def save_credentials(body: ProviderCredentials, admin=Depends(crm_actor)):
    staff_owner(admin)
    mapping = ROBOKASSA_FIELD_MAP if body.provider == "robokassa" else YOOKASSA_FIELD_MAP
    updates: dict[str, str | None] = {}
    for field, value in body.fields.items():
        env_key = mapping.get(field)
        if not env_key:
            raise HTTPException(422, f"Неизвестное поле: {field}")
        if field in {"test_mode", "enabled"}:
            if value is None or str(value).strip() == "":
                updates[env_key] = None
            else:
                updates[env_key] = "1" if str(value).strip().lower() in {"1", "true", "yes", "on"} else "0"
        else:
            updates[env_key] = value
    try:
        present = payment_vault.apply_updates(updates)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    vault_id = None
    with Session.begin() as db:
        if body.project_id:
            project_access(db, admin, body.project_id)
            secrets, meta = _fields_to_vault_parts(body.provider, body.fields)
            row = vault.upsert_from_payment_credentials(
                db,
                project_id=body.project_id,
                provider=body.provider,
                secrets=secrets,
                meta=meta,
                actor_id=admin.telegram_id,
            )
            vault_id = row.id if row else None
        audit(
            db,
            admin,
            "payment_credentials.saved",
            provider=body.provider,
            keys=[k for k, v in updates.items() if v is not None],
            project_id=body.project_id,
            vault_id=vault_id,
        )
    payment_vault.ensure_runtime(force=True)
    return {
        "ok": True,
        "provider": body.provider,
        "present": {
            k: present.get(k)
            for k in (
                ROBOKASSA_FIELD_MAP.values() if body.provider == "robokassa" else YOOKASSA_FIELD_MAP.values()
            )
        },
        "status": portals.robokassa_status() if body.provider == "robokassa" else portals.yookassa_skeleton(),
        "vault_id": vault_id,
        "secrets_exposed": False,
    }


@router.post("/payment-providers/{provider}/test-ping")
def test_ping(provider: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    payment_vault.ensure_runtime(force=True)
    if provider == "robokassa":
        st = portals.robokassa_status()
        return {
            "ok": bool(st.get("login_set") and st.get("passwords_set")),
            "provider": "robokassa",
            "mode": st.get("mode"),
            "ready": st.get("ready"),
            "result_url": st.get("result_url"),
            "fail_url": st.get("fail_url"),
            "note": "Ping = наличие credentials for current mode; live gate unchanged (sandbox until Q11 flip).",
        }
    if provider == "yookassa":
        st = portals.yookassa_skeleton()
        present = st.get("env_present") or {}
        ok = bool(present.get("YOOKASSA_SHOP_ID") and present.get("YOOKASSA_SECRET_KEY"))
        return {
            "ok": ok,
            "provider": "yookassa",
            "adapter": st.get("adapter"),
            "webhook_url": st.get("webhook_url"),
            "note": "Sandbox OK without live money; webhook accepts when shop+secret present.",
        }
    raise HTTPException(404, "Неизвестный провайдер")


@router.get("/staff-alert-rules")
def list_alert_rules(admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session() as db:
        rows = list(db.scalars(select(StaffAlertRule).order_by(StaffAlertRule.created_at.desc())))
        return {"items": [encode(r) for r in rows]}


@router.post("/staff-alert-rules")
def create_alert_rule(body: AlertRuleBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        if body.project_id:
            project_access(db, admin, body.project_id)
        row = StaffAlertRule(
            id=uid(),
            project_id=body.project_id,
            kind=body.kind,
            channel=body.channel,
            amount_minor=body.amount_minor,
            active=body.active,
            config=body.config or {},
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        audit(db, admin, "staff_alert_rule.created", rule_id=row.id, alert_kind=body.kind)
        return encode(row)


@router.patch("/staff-alert-rules/{rid}")
def patch_alert_rule(rid: str, body: AlertRulePatch, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = must(db.get(StaffAlertRule, rid))
        if body.channel is not None:
            row.channel = body.channel
        if body.amount_minor is not None:
            row.amount_minor = body.amount_minor
        if body.active is not None:
            row.active = body.active
        if body.config is not None:
            row.config = body.config
        row.updated_at = now()
        audit(db, admin, "staff_alert_rule.updated", rule_id=rid)
        return encode(row)


@router.post("/staff-alert-rules/{rid}/test")
def test_alert_rule(rid: str, admin=Depends(crm_actor)):
    """Fire one synthetic delivery so staff can verify TG/email channels."""
    staff_owner(admin)
    with Session.begin() as db:
        row = must(db.get(StaffAlertRule, rid))
        paid = row.kind != "not_paid"
        amount = int(row.amount_minor or 0) or 10000
        results = staff_alerts.fire_payment_alerts(
            db,
            kind=row.kind,
            project_id=row.project_id,
            order_id=f"test-{uid()}",
            amount_minor=amount,
            paid=paid,
            provider="test",
        )
        audit(db, admin, "staff_alert_rule.tested", rule_id=rid, results=results)
        return {"ok": True, "rule_id": rid, "results": results}


@router.post("/staff-alert-rules/scan-unpaid")
def scan_unpaid_invoices(admin=Depends(crm_actor)):
    """CRM-R9: fire not_paid for pending invoices on open orders (deduped)."""
    staff_owner(admin)
    with Session.begin() as db:
        pending = list(db.scalars(select(Invoice).where(Invoice.state == "pending")))
        fired = []
        for inv in pending:
            order = db.get(Order, inv.order_id)
            if not order or order.state != "open":
                continue
            results = staff_alerts.fire_payment_alerts(
                db,
                kind="not_paid",
                project_id=order.project_id,
                order_id=order.id,
                amount_minor=int(inv.amount_minor or 0),
                paid=False,
                provider="pending_invoice",
            )
            if results:
                fired.append({"invoice_id": inv.id, "order_id": order.id, "results": results})
        audit(db, admin, "staff_alert_rules.scan_unpaid", count=len(fired))
        return {"ok": True, "fired": len(fired), "items": fired}
