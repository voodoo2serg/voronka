"""CRM-R9: payment vault reload, dual-write, webhooks, staff alerts."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import select

from app import payment_vault
from app import secret_box
from app.crm_models import Invoice, ProviderEvent, StaffAlertEvent, StaffAlertRule
from test_crm import contact, order, product

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


@pytest.fixture(autouse=True)
def _payment_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(tmp_path / "pay.env"))
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    key = secret_box.derive_dev_key_from_seed("crm-r9-test")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    secret_box.reset_fernet_cache()
    for key_name in list(payment_vault.ALLOWED_KEYS):
        monkeypatch.delenv(key_name, raising=False)
    monkeypatch.delenv("ROBOKASSA_LIVE_READY", raising=False)
    yield
    secret_box.reset_fernet_cache()


def test_vault_force_reload_overrides_stale_environ(monkeypatch, tmp_path):
    path = tmp_path / "pay.env"
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(path))
    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "shop-a",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "aaa111111111",
            "ROBOKASSA_TEST_PASSWORD2": "bbb222222222",
        }
    )
    assert os.getenv("ROBOKASSA_LOGIN") == "shop-a"
    path.write_text(
        "\n".join(
            [
                "ROBOKASSA_LOGIN=shop-b",
                "ROBOKASSA_TEST=1",
                "ROBOKASSA_TEST_PASSWORD1=ccc333333333",
                "ROBOKASSA_TEST_PASSWORD2=ddd444444444",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    payment_vault.load_into_environ(force=False)
    assert os.getenv("ROBOKASSA_LOGIN") == "shop-a"
    payment_vault.ensure_runtime(force=True)
    assert os.getenv("ROBOKASSA_LOGIN") == "shop-b"
    assert os.getenv("ROBOKASSA_TEST_PASSWORD1") == "ccc333333333"
    # Revoke password2 in file → force must unset process env.
    path.write_text(
        "\n".join(
            [
                "ROBOKASSA_LOGIN=shop-b",
                "ROBOKASSA_TEST=1",
                "ROBOKASSA_TEST_PASSWORD1=ccc333333333",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    payment_vault.ensure_runtime(force=True)
    assert os.getenv("ROBOKASSA_TEST_PASSWORD2") in (None, "")


def test_invalid_project_does_not_write_vault(crm, tmp_path, monkeypatch):
    path = tmp_path / "guard.env"
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(path))
    denied = crm.client.put(
        "/api/crm/payment-providers/credentials",
        json={
            "provider": "robokassa",
            "project_id": "no-such-project",
            "fields": {"login": "should-not-persist", "test_password1": "x" * 12},
        },
    )
    assert denied.status_code in {403, 404}
    assert not path.is_file() or "should-not-persist" not in path.read_text(encoding="utf-8")


def test_clear_credentials_clears_project_vault(crm):
    saved = crm.client.put(
        "/api/crm/payment-providers/credentials",
        json={
            "provider": "yookassa",
            "project_id": "p",
            "fields": {"shop_id": "keep-me", "secret_key": "secret-to-clear-ZZZZ"},
        },
    )
    assert saved.status_code == 200, saved.text
    cleared = crm.client.put(
        "/api/crm/payment-providers/credentials",
        json={
            "provider": "yookassa",
            "project_id": "p",
            "fields": {"secret_key": ""},
        },
    )
    assert cleared.status_code == 200, cleared.text
    assert os.getenv("YOOKASSA_SECRET_KEY") in (None, "")
    listed = crm.client.get("/api/crm/projects/p/connection-vault").json()
    yk = next(i for i in listed["items"] if i["type"] == "yookassa")
    assert "secret_key" not in (yk.get("secret_masks") or {})


def test_payments_credentials_upsert_vault_and_ping(crm):
    saved = crm.client.put(
        "/api/crm/payment-providers/credentials",
        json={
            "provider": "robokassa",
            "project_id": "p",
            "fields": {
                "login": "demo-shop",
                "test_mode": "1",
                "test_password1": "pass1-r9-aaaa",
                "test_password2": "pass2-r9-bbbb",
                "hash": "sha256",
            },
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["ok"] is True
    assert body["secrets_exposed"] is False
    assert "pass1-r9-aaaa" not in saved.text
    assert body.get("vault_id")
    ping = crm.client.post("/api/crm/payment-providers/robokassa/test-ping").json()
    assert ping["ok"] is True
    assert ping["mode"] == "test"
    assert "/robokassa/result" in ping["result_url"]
    assert "/robokassa/fail" in ping["fail_url"]
    listed = crm.client.get("/api/crm/projects/p/connection-vault").json()
    assert any(i["type"] == "robokassa" and i["id"] == body["vault_id"] for i in listed["items"])


def test_connection_vault_dual_write_makes_yookassa_ready(crm):
    created = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "yookassa",
            "label": "YK R9",
            "meta": {"shop_id": "yk-shop-9", "enabled": False},
            "secrets": {"secret_key": "yk-secret-R9xxxx", "webhook_secret": "wh-R9"},
        },
    )
    assert created.status_code == 200, created.text
    assert "yk-secret-R9xxxx" not in created.text
    ping = crm.client.post("/api/crm/payment-providers/yookassa/test-ping").json()
    assert ping["ok"] is True
    assert "/yookassa/webhook" in ping["webhook_url"]


def test_robokassa_result_fires_paid_and_threshold_alerts(crm, monkeypatch):
    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "merchant",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "test-first",
            "ROBOKASSA_TEST_PASSWORD2": "test-second",
            "ROBOKASSA_HASH": "sha256",
        }
    )
    for kind in ("paid", "amount_threshold"):
        r = crm.client.post(
            "/api/crm/staff-alert-rules",
            json={
                "kind": kind,
                "channel": "telegram",
                "amount_minor": 100 if kind == "amount_threshold" else 0,
            },
        )
        assert r.status_code == 200, r.text
    o = order(crm)
    pay = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert pay.status_code == 200, pay.text
    with crm.db() as db:
        invoice = db.get(Invoice, pay.json()["invoice_id"])
        number = invoice.number
    data = {
        "OutSum": "1000.00",
        "InvId": number,
        "SignatureValue": hashlib.sha256(f"1000.00:{number}:test-second".encode()).hexdigest(),
    }
    assert crm.client.post("/api/crm/robokassa/result", data=data).text == "OK" + number
    with crm.db() as db:
        events = list(db.scalars(select(StaffAlertEvent)))
        assert events
        kinds = {db.get(StaffAlertRule, ev.rule_id).kind for ev in events}
        assert "paid" in kinds
        assert "amount_threshold" in kinds


def test_robokassa_fail_fires_not_paid(crm):
    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "merchant",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "test-first",
            "ROBOKASSA_TEST_PASSWORD2": "test-second",
        }
    )
    assert (
        crm.client.post(
            "/api/crm/staff-alert-rules",
            json={"kind": "not_paid", "channel": "email", "amount_minor": 0},
        ).status_code
        == 200
    )
    o = order(crm)
    pay = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert pay.status_code == 200, pay.text
    with crm.db() as db:
        invoice = db.get(Invoice, pay.json()["invoice_id"])
        number = invoice.number
    fail = crm.client.get("/api/crm/robokassa/fail", params={"InvId": number, "OutSum": "1000.00"})
    assert fail.status_code == 200, fail.text
    assert fail.json()["state"] == "failed"
    with crm.db() as db:
        inv = db.scalar(select(Invoice).where(Invoice.number == number))
        assert inv.state == "failed"
        assert list(db.scalars(select(StaffAlertEvent)))


def test_yookassa_webhook_paid_and_canceled(crm):
    payment_vault.apply_updates(
        {
            "YOOKASSA_SHOP_ID": "shop-yk",
            "YOOKASSA_SECRET_KEY": "secret-yk",
            "YOOKASSA_WEBHOOK_SECRET": "whsec",
            "YOOKASSA_ENABLED": "0",
        }
    )
    crm.client.post("/api/crm/staff-alert-rules", json={"kind": "paid", "channel": "telegram"})
    crm.client.post("/api/crm/staff-alert-rules", json={"kind": "not_paid", "channel": "telegram"})
    o = order(crm, person=contact(crm, name="YK Buyer"), item=product(crm, amount=20000))
    with crm.db.begin() as db:
        from app.crm_service import uid

        inv = Invoice(
            id=uid(),
            order_id=o["id"],
            number="yk-inv-9",
            amount_minor=20000,
            test=True,
            state="pending",
        )
        db.add(inv)
        inv_id = inv.id
    paid = crm.client.post(
        "/api/crm/yookassa/webhook",
        headers={"x-yookassa-signature": "whsec"},
        json={
            "event": "payment.succeeded",
            "object": {
                "id": "yk-pay-1",
                "status": "succeeded",
                "amount": {"value": "200.00", "currency": "RUB"},
                "metadata": {"invoice_id": inv_id},
            },
        },
    )
    assert paid.status_code == 200, paid.text
    # Second invoice for cancel path (first already paid).
    with crm.db.begin() as db:
        from app.crm_service import uid

        inv2 = Invoice(
            id=uid(),
            order_id=o["id"],
            number="yk-inv-10",
            amount_minor=5000,
            test=True,
            state="pending",
        )
        db.add(inv2)
        inv2_id = inv2.id
    canceled = crm.client.post(
        "/api/crm/yookassa/webhook",
        headers={"x-yookassa-signature": "whsec"},
        json={
            "event": "payment.canceled",
            "object": {
                "id": "yk-pay-2",
                "status": "canceled",
                "amount": {"value": "50.00", "currency": "RUB"},
                "metadata": {"invoice_id": inv2_id},
            },
        },
    )
    assert canceled.status_code == 200, canceled.text
    with crm.db() as db:
        assert db.get(Invoice, inv_id).state == "paid"
        assert list(db.scalars(select(ProviderEvent).where(ProviderEvent.provider == "yookassa")))
        kinds = {
            db.get(StaffAlertRule, ev.rule_id).kind for ev in db.scalars(select(StaffAlertEvent))
        }
        assert "paid" in kinds
        assert "not_paid" in kinds


def test_alert_rule_test_and_toggle(crm, monkeypatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "123")
    rule = crm.client.post(
        "/api/crm/staff-alert-rules",
        json={"kind": "paid", "channel": "telegram", "amount_minor": 0},
    ).json()
    sibling = crm.client.post(
        "/api/crm/staff-alert-rules",
        json={"kind": "paid", "channel": "email", "amount_minor": 0},
    ).json()
    tested = crm.client.post(f"/api/crm/staff-alert-rules/{rule['id']}/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["ok"] is True
    assert len(body["results"]) == 1
    assert body["results"][0]["rule_id"] == rule["id"]
    with crm.db() as db:
        events = list(db.scalars(select(StaffAlertEvent)))
        assert all(ev.rule_id == rule["id"] for ev in events)
        assert sibling["id"]  # sibling exists but was not fired
    patched = crm.client.patch(
        f"/api/crm/staff-alert-rules/{rule['id']}",
        json={"active": False},
    ).json()
    assert patched["active"] is False
    # Inactive rule can still be tested in isolation.
    retest = crm.client.post(f"/api/crm/staff-alert-rules/{rule['id']}/test")
    assert retest.status_code == 200
    assert len(retest.json()["results"]) == 1


def test_payment_providers_expose_fail_and_webhook(crm):
    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "shop",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "a" * 12,
            "ROBOKASSA_TEST_PASSWORD2": "b" * 12,
            "YOOKASSA_SHOP_ID": "s",
            "YOOKASSA_SECRET_KEY": "k",
        }
    )
    providers = crm.client.get("/api/crm/projects/p/payment-providers").json()
    assert "/robokassa/result" in providers["robokassa"]["result_url"]
    assert "/robokassa/fail" in providers["robokassa"]["fail_url"]
    assert "/yookassa/webhook" in providers["yookassa"]["webhook_url"]
    assert providers["robokassa"]["secrets_exposed"] is False


def test_scan_unpaid_fires_not_paid(crm):
    crm.client.post(
        "/api/crm/staff-alert-rules",
        json={"kind": "not_paid", "channel": "telegram", "amount_minor": 0},
    )
    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "merchant",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "test-first",
            "ROBOKASSA_TEST_PASSWORD2": "test-second",
        }
    )
    o = order(crm)
    pay = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert pay.status_code == 200
    scanned = crm.client.post("/api/crm/staff-alert-rules/scan-unpaid")
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["fired"] >= 1
