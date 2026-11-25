"""CRM-R4: Constructor «Проверить» delivers via vault Mailu SMTP + Telegram bot."""
from __future__ import annotations

import smtplib
from pathlib import Path

import pytest

from app import secret_box
from app.crm_models import ActionChainDryRun
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


@pytest.fixture(autouse=True)
def _fernet(monkeypatch, tmp_path):
    key = secret_box.derive_dev_key_from_seed("crm-r4-dry-run")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    # Ensure dry-run does not accidentally use bare env SMTP when vault is present.
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("ADMIN_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TG_MAIN_TOKEN", raising=False)
    secret_box.reset_fernet_cache()
    yield
    secret_box.reset_fernet_cache()


class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=20):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        return True

    def starttls(self):
        return True

    def login(self, user, password):
        self.user = user
        self.password = password

    def send_message(self, msg):
        _FakeSMTP.sent.append(
            {
                "host": self.host,
                "port": self.port,
                "user": getattr(self, "user", None),
                "password": getattr(self, "password", None),
                "from": msg["From"],
                "to": msg["To"],
                "subject": msg["Subject"],
            }
        )


class _FakeResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text=""):
        self._ok = ok
        self.status_code = status_code
        self._payload = payload or {"ok": ok}
        self.text = text

    @property
    def is_success(self):
        return self._ok

    def json(self):
        return self._payload


def test_dry_run_uses_vault_smtp_and_bot(crm, monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    mail = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "email_smtp",
            "label": "news@",
            "meta": {
                "from_address": "news@vodopetov.ru",
                "smtp_host": "mail.vodopetov.ru",
                "smtp_user": "news@vodopetov.ru",
                "smtp_port": "587",
            },
            "secrets": {"smtp_password": "mailu-app-token-r4"},
        },
    ).json()
    bot = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "telegram_bot",
            "label": "CRM bot",
            "meta": {"bot_username": "crm_r4_bot"},
            "secrets": {"bot_token": "123456:AA-r4-test-token"},
        },
    ).json()

    tg_calls: list[dict] = []

    def fake_post(url, json=None, timeout=15):
        tg_calls.append({"url": url, "json": json})
        assert "123456:AA-r4-test-token" in url
        return _FakeResponse(ok=True, status_code=200, payload={"ok": True, "result": {"message_id": 1}})

    monkeypatch.setattr("httpx.post", fake_post)

    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "R4 check",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "email",
                    "title": "Письмо",
                    "config": {"body": "Привет R4", "from_vault_id": mail["id"]},
                    "from_address": "news@vodopetov.ru",
                },
                {
                    "id": "n2",
                    "kind": "telegram",
                    "title": "TG",
                    "config": {"text": "TG R4", "vault_id": bot["id"]},
                },
            ],
        },
    ).json()

    dry = crm.client.post(
        f"/api/crm/action-chains/{created['id']}/dry-run",
        json={"email": "owner@example.ru", "telegram": "999001"},
    )
    assert dry.status_code == 200, dry.text
    body = dry.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert "Доставлено" in body["status_message"]
    assert body["report"]["deliveries_ok"] == 2
    assert body["report"]["deliveries_fail"] == 0

    assert len(_FakeSMTP.sent) == 1
    sent = _FakeSMTP.sent[0]
    assert sent["host"] == "mail.vodopetov.ru"
    assert sent["from"] == "news@vodopetov.ru"
    assert sent["to"] == "owner@example.ru"
    assert sent["password"] == "mailu-app-token-r4"
    assert "[Проверка]" in sent["subject"]

    assert len(tg_calls) == 1
    assert tg_calls[0]["json"]["chat_id"] == 999001

    with crm.db() as db:
        rows = list(db.scalars(select(ActionChainDryRun).where(ActionChainDryRun.chain_id == created["id"])))
        assert len(rows) == 1
        assert rows[0].report["status"] == "ok"


def test_dry_run_clear_fail_without_vault(crm):
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "R4 fail",
            "nodes": [
                {"id": "n1", "kind": "email", "title": "Письмо", "config": {"body": "x"}, "from_address": None},
            ],
        },
    ).json()
    dry = crm.client.post(
        f"/api/crm/action-chains/{created['id']}/dry-run",
        json={"email": "owner@example.ru", "telegram": ""},
    ).json()
    assert dry["ok"] is False
    assert dry["status"] == "fail"
    assert dry["report"]["deliveries_fail"] >= 1
    delivery = dry["report"]["steps"][0]["deliveries"][0]
    assert delivery["ok"] is False
    assert delivery["channel"] == "email"
    assert "Подключениях" in (delivery.get("message") or "") or "SMTP" in (delivery.get("message") or "")


def test_dry_run_probes_when_chain_has_no_send_nodes(crm, monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "email_smtp",
            "label": "info@",
            "meta": {
                "from_address": "info@solo-pr.ru",
                "smtp_host": "mail.vodopetov.ru",
                "smtp_user": "info@solo-pr.ru",
                "smtp_port": "587",
            },
            "secrets": {"smtp_password": "probe-token"},
        },
    )
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "Only finish",
            "nodes": [{"id": "n1", "kind": "finish", "title": "Финиш", "config": {}}],
        },
    ).json()
    dry = crm.client.post(
        f"/api/crm/action-chains/{created['id']}/dry-run",
        json={"email": "me@example.ru", "telegram": ""},
    ).json()
    assert dry["ok"] is True
    assert any(s.get("id") == "probe-email" for s in dry["report"]["steps"])
    assert len(_FakeSMTP.sent) == 1
    assert _FakeSMTP.sent[0]["to"] == "me@example.ru"


def test_dry_run_telegram_api_fail_message(crm, monkeypatch):
    bot = crm.client.post(
        "/api/crm/projects/p/connection-vault",
        json={
            "type": "telegram_bot",
            "label": "bot",
            "secrets": {"bot_token": "1:bad"},
        },
    ).json()

    def fake_post(url, json=None, timeout=15):
        return _FakeResponse(
            ok=False,
            status_code=400,
            payload={"ok": False, "description": "chat not found"},
        )

    monkeypatch.setattr("httpx.post", fake_post)
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "TG fail",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "telegram",
                    "title": "TG",
                    "config": {"text": "hi", "vault_id": bot["id"]},
                }
            ],
        },
    ).json()
    dry = crm.client.post(
        f"/api/crm/action-chains/{created['id']}/dry-run",
        json={"email": "", "telegram": "@ghost"},
    ).json()
    assert dry["ok"] is False
    assert dry["status"] == "fail"
    delivery = dry["report"]["steps"][0]["deliveries"][0]
    assert delivery["ok"] is False
    assert "chat not found" in delivery["message"]


def test_frontend_shows_dryrun_status():
    ctor = (ROOT / "web" / "app/crm/sections/ActionConstructorSection.tsx").read_text(encoding="utf-8")
    assert "constructor-dryrun-status" in ctor
    assert "status_message" in ctor
    assert "Не доставлено" in ctor
    assert "Доставлено" in ctor
    css = (ROOT / "web/app/globals.css").read_text(encoding="utf-8")
    assert ".constructor-dryrun-status.is-ok" in css
    assert ".constructor-dryrun-status.is-fail" in css
