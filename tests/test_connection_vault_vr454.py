"""VR-454 Connection Vault: mask secrets, owner write, rotate audit, Constructor picker."""
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import select

from app.crm_models import ConnectionVault
from app.db import Event
from app import secret_box

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch, tmp_path):
    key = secret_box.derive_dev_key_from_seed("vr454-test")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(tmp_path / "pay.env"))
    secret_box.reset_fernet_cache()
    yield
    secret_box.reset_fernet_cache()


def test_create_list_masks_and_no_raw_secret(crm):
    token = "123456:ABCDEF-secret-token-XYZ9"
    created = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "telegram_bot",
            "label": "Solo bot",
            "meta": {"bot_username": "Solo_pr_bot"},
            "secrets": {"bot_token": token},
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["has_secret"] is True
    assert body["secrets_exposed"] is False
    assert "secrets_blob" not in body
    assert token not in created.text
    assert body["secret_masks"]["bot_token"].startswith("••••")
    assert body["secret_masks"]["bot_token"].endswith("XYZ9") or body["secret_masks"]["bot_token"].endswith(
        token[-4:]
    )
    # webhook_secret auto-generated
    assert "webhook_secret" in body["secret_masks"]

    listed = crm.client.get("/api/crm/projects/p/connection-vault").json()
    assert listed["secrets_exposed"] is False
    assert any(i["id"] == body["id"] for i in listed["items"])
    assert token not in str(listed)
    assert "••••" in str(listed["items"])


def test_email_smtp_and_payment_create(crm):
    mail = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "email_smtp",
            "label": "serg@",
            "meta": {
                "from_address": "serg@vodopetov.ru",
                "smtp_host": "mail.vodopetov.ru",
                "smtp_user": "serg@vodopetov.ru",
                "smtp_port": "587",
            },
            "secrets": {"smtp_password": "mail-app-token-7788"},
        },
    ).json()
    assert mail["type"] == "email_smtp"
    assert "7788" in mail["secret_masks"]["smtp_password"]
    assert "mail-app-token-7788" not in str(mail)

    pay = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "robokassa",
            "label": "RK sandbox",
            "meta": {"login": "shop", "test_mode": True, "hash": "sha256"},
            "secrets": {"test_password1": "rk-pass-1111", "test_password2": "rk-pass-2222"},
        },
    ).json()
    assert pay["type"] == "robokassa"
    assert pay["has_secret"] is True
    assert "rk-pass-1111" not in str(pay)


def test_rotate_audits_and_masks(crm):
    row = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "yookassa",
            "label": "YK",
            "meta": {"shop_id": "42"},
            "secrets": {"secret_key": "old-secret-AAAA"},
        },
    ).json()
    rotated = crm.client.post(
        f"/api/crm/connection-vault/{row['id']}/rotate",
        json={"secrets": {"secret_key": "new-secret-BBBB"}},
    )
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert "BBBB" in body["secret_masks"]["secret_key"]
    assert "new-secret-BBBB" not in rotated.text
    with crm.db() as db:
        events = list(
            db.scalars(select(Event).where(Event.kind == "crm.connection_vault.rotated"))
        )
        assert events
        assert events[-1].payload.get("vault_id") == row["id"]
        assert "new-secret-BBBB" not in str(events[-1].payload)


def test_non_owner_cannot_write(crm):
    # Create editor session via ops-style role downgrade on a second account is heavy;
    # patch current admin role briefly.
    with crm.db.begin() as db:
        from app.db import Admin

        admin = db.get(Admin, 123)
        admin.role = "editor"
    denied = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={"type": "telegram_bot", "label": "x", "secrets": {"bot_token": "t"}},
    )
    assert denied.status_code == 403
    with crm.db.begin() as db:
        from app.db import Admin

        db.get(Admin, 123).role = "owner"


def test_constructor_meta_includes_vault_pickers(crm):
    crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "email_smtp",
            "label": "info@",
            "meta": {"from_address": "info@solo-pr.ru"},
            "secrets": {"smtp_password": "pwd-9999"},
        },
    )
    crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "yookassa",
            "label": "YK pick",
            "meta": {"shop_id": "1"},
            "secrets": {"secret_key": "yk-zzzz"},
        },
    )
    crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "telegram_bot",
            "label": "TG pick",
            "meta": {"bot_username": "@pick_bot"},
            "secrets": {"bot_token": "123:AAAsecret-zzzz"},
        },
    )
    meta = crm.client.get("/api/crm/constructor/meta?project_id=p").json()
    assert meta["secrets_exposed"] is False
    assert any(v.get("type") == "email_smtp" for v in meta["vault_email"])
    assert any(v.get("type") == "yookassa" for v in meta["vault_payment"])
    assert any(v.get("type") == "telegram_bot" for v in meta.get("vault_messaging") or [])
    assert "yk-zzzz" not in str(meta)
    assert "pwd-9999" not in str(meta)
    assert "AAAsecret" not in str(meta)


def test_sync_legacy_payment_and_smtp(crm, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example")
    monkeypatch.setenv("SMTP_FROM", "crm@example.ru")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp-secret-4242")
    from app import payment_vault

    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "legacy-shop",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "leg1-xxxx",
            "ROBOKASSA_TEST_PASSWORD2": "leg2-yyyy",
        }
    )
    synced = crm.client.post("/api/crm/projects/p/connection-vault/sync-legacy").json()
    assert synced["ok"] is True
    assert synced["created"] >= 1
    assert "smtp-secret-4242" not in str(synced)
    assert "leg1-xxxx" not in str(synced)
    listed = crm.client.get("/api/crm/projects/p/connection-vault").json()
    types = {i["type"] for i in listed["items"]}
    assert "email_smtp" in types
    assert "robokassa" in types


def test_action_chain_stores_vault_id(crm):
    pay = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "robokassa",
            "label": "Ctor RK",
            "meta": {"login": "shop"},
            "secrets": {"test_password1": "a" * 12, "test_password2": "b" * 12},
        },
    ).json()
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "Pay chain",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "payment",
                    "title": "Оплата",
                    "config": {"provider": "robokassa", "tariff_mode": "fixed", "vault_id": pay["id"]},
                },
                {"id": "n2", "kind": "finish", "title": "Финиш", "config": {}},
            ],
        },
    ).json()
    assert created["nodes"][0]["config"]["vault_id"] == pay["id"]


def test_frontend_nav_and_vault_section():
    constants = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    assert "['connections', 'Подключения']" in constants
    section = (ROOT / "web" / "app" / "crm" / "sections" / "ConnectionsVaultSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "Сменить секрет" in section
    assert "connection-vault" in section
    ctor = (ROOT / "web" / "app" / "crm" / "sections" / "ActionConstructorSection.tsx").read_text(
        encoding="utf-8"
    )
    # CART-01: pickers for already-configured vault only; no credential forms on #today
    assert "Выбрать подключение" in ctor
    assert "from_vault_id" in ctor or "vault_id" in ctor
    assert "Настроить в Подключениях" in ctor
    assert "smtp_password" not in ctor
    assert "bot_token" not in ctor
    assert "Сменить секрет" not in ctor
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "ConnectionsVaultSection" in ws
    assert "section === 'connections'" in ws
    assert "onOpenConnections" in ws


def test_secrets_not_in_db_plaintext(crm):
    token = "PLAINTEXT-SHOULD-NOT-APPEAR-7788"
    row = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "telegram_bot",
            "label": "enc",
            "secrets": {"bot_token": token},
        },
    ).json()
    with crm.db() as db:
        stored = db.get(ConnectionVault, row["id"])
        assert stored is not None
        assert stored.secrets_blob
        assert token not in stored.secrets_blob
        assert "7788" in (stored.secret_masks or {}).get("bot_token", "")
