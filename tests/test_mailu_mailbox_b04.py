"""B-04 / VR-473: one-click Mailu mailbox → vault email_smtp (mock Mailu)."""
import os
from pathlib import Path
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest

from app import mailu_client
from app import secret_box
from app.crm_models import ConnectionVault

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


@pytest.fixture(autouse=True)
def _fernet_and_mailu_env(monkeypatch, tmp_path):
    key = secret_box.derive_dev_key_from_seed("b04-mailu-test")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    monkeypatch.setenv("MAILU_API_URL", "https://mail.test/api")
    monkeypatch.setenv("MAILU_API_TOKEN", "test-mailu-api-token-xxxx")
    monkeypatch.setenv("MAILU_SMTP_HOST", "mail.vodopetov.ru")
    monkeypatch.setenv("MAILU_SMTP_PORT", "587")
    secret_box.reset_fernet_cache()
    yield
    secret_box.reset_fernet_cache()


def _mock_mailu_ok(monkeypatch, *, token="aabbccddeeff00112233445566778899"):
    def fake_request(method, path, *, json=None):
        if method == "POST" and path == "/v1/user":
            return 200, {"code": 200, "message": "User " + json["email"] + " has been created"}
        if method == "POST" and path == "/v1/token":
            return 200, {
                "id": "1",
                "token": token,
                "email": json["email"],
                "comment": json.get("comment"),
            }
        return 500, {"message": f"unexpected {method} {path}"}

    monkeypatch.setattr(mailu_client, "_request", fake_request)
    return token


def _mock_mailu_no_token_api(monkeypatch):
    """Older Mailu: user create works, /v1/token is 404 → fall back to account password."""

    def fake_request(method, path, *, json=None):
        if method == "POST" and path == "/v1/user":
            return 200, {"code": 200, "message": "User " + json["email"] + " has been created"}
        if method == "POST" and path == "/v1/token":
            return 404, "<html>Not Found</html>"
        return 500, {"message": f"unexpected {method} {path}"}

    monkeypatch.setattr(mailu_client, "_request", fake_request)


def test_mailbox_create_shape_and_masks(crm, monkeypatch):
    token = _mock_mailu_ok(monkeypatch)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "newsdesk", "label": "newsdesk@"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "email_smtp"
    assert body["from_address"] == "newsdesk@vodopetov.ru"
    assert body["meta"]["from_address"] == "newsdesk@vodopetov.ru"
    assert body["meta"]["smtp_host"] == "mail.vodopetov.ru"
    assert body["meta"]["smtp_user"] == "newsdesk@vodopetov.ru"
    assert str(body["meta"]["smtp_port"]) == "587"
    assert body["has_secret"] is True
    assert body["secrets_exposed"] is False
    assert body["mailu_created"] is True
    assert body["legacy_source"] == "mailu_one_click"
    assert token not in r.text
    assert "smtp_password" not in body or body.get("smtp_password") in (None, "")
    assert "••••" in body["secret_masks"]["smtp_password"]
    assert token[-4:] in body["secret_masks"]["smtp_password"]

    with crm.db() as db:
        row = db.get(ConnectionVault, body["id"])
        assert row is not None
        assert token not in (row.secrets_blob or "")
        secrets = secret_box.decrypt_mapping(row.secrets_blob)
        assert secrets["smtp_password"] == token


def test_mailbox_appears_in_constructor_from_identities(crm, monkeypatch):
    _mock_mailu_ok(monkeypatch)
    created = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "solo-pr.ru", "local_part": "ops"},
    ).json()
    meta = crm.client.get("/api/crm/constructor/meta?project_id=p").json()
    assert any(v.get("id") == created["id"] for v in meta["vault_email"])
    assert any(
        f.get("address") == "ops@solo-pr.ru" and f.get("vault_id") == created["id"]
        for f in meta["from_identities"]
    )
    assert "aabbccddeeff00112233445566778899" not in str(meta)


def test_mailbox_rejects_unknown_domain(crm, monkeypatch):
    _mock_mailu_ok(monkeypatch)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "evil.example", "local_part": "x"},
    )
    assert r.status_code == 422
    assert "Домен" in r.json()["detail"] or "домен" in r.json()["detail"].lower()


def test_mailbox_rejects_bad_local_part(crm, monkeypatch):
    _mock_mailu_ok(monkeypatch)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "bad name!"},
    )
    assert r.status_code == 422


def test_mailbox_mailu_unreachable(crm, monkeypatch):
    def boom(method, path, *, json=None):
        raise mailu_client.MailuError("Почта Mailu недоступна. Проверьте сервер или обратитесь к ops.")

    monkeypatch.setattr(mailu_client, "_request", boom)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "temp"},
    )
    assert r.status_code == 503
    assert "Mailu" in r.json()["detail"]
    assert "недоступна" in r.json()["detail"]


def test_mailbox_mailu_duplicate(crm, monkeypatch):
    def fake_request(method, path, *, json=None):
        if path == "/v1/user":
            return 409, {"code": 409, "message": "User already exists"}
        return 500, {}

    monkeypatch.setattr(mailu_client, "_request", fake_request)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "serg"},
    )
    assert r.status_code == 409
    assert "существует" in r.json()["detail"]


def test_mailbox_requires_owner(crm, monkeypatch):
    _mock_mailu_ok(monkeypatch)
    with crm.db.begin() as db:
        from app.db import Admin

        db.get(Admin, 123).role = "editor"
    denied = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "ed"},
    )
    assert denied.status_code == 403
    with crm.db.begin() as db:
        from app.db import Admin

        db.get(Admin, 123).role = "owner"


def test_list_exposes_mail_domains(crm):
    listed = crm.client.get("/api/crm/projects/p/connection-vault").json()
    assert "solo-pr.ru" in listed["mail_domains"]
    assert "vodopetov.ru" in listed["mail_domains"]
    assert listed["mailu_configured"] is True


def test_mailu_not_configured(crm, monkeypatch):
    monkeypatch.delenv("MAILU_API_URL", raising=False)
    monkeypatch.delenv("MAILU_API_TOKEN", raising=False)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "x"},
    )
    assert r.status_code == 503
    assert "MAILU" in r.json()["detail"] or "недоступна" in r.json()["detail"]


def test_frontend_mailbox_ux():
    section = (ROOT / "web" / "app" / "crm" / "sections" / "ConnectionsVaultSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "Новая почта" in section
    assert "Создать почту" in section
    assert "connection-vault/mailboxes" in section
    assert "smtp_host" not in section or "CREATE_TYPE_OPTIONS" in section
    # Owner create must not offer email_smtp SMTP host/port fields.
    assert "CREATE_TYPE_OPTIONS" in section
    assert "mailDomains" in section


def test_client_validate_helpers():
    assert mailu_client.validate_local_part("Serg") == "serg"
    assert mailu_client.validate_domain("Solo-PR.ru") == "solo-pr.ru"
    with pytest.raises(mailu_client.MailuError):
        mailu_client.validate_domain("gmail.com")
    with pytest.raises(mailu_client.MailuError):
        mailu_client.validate_local_part("")


def test_mailbox_falls_back_when_token_api_missing(crm, monkeypatch):
    _mock_mailu_no_token_api(monkeypatch)
    r = crm.client.post(
        "/api/crm/projects/p/connection-vault/mailboxes",
        json={"domain": "vodopetov.ru", "local_part": "legacybox"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_address"] == "legacybox@vodopetov.ru"
    assert body["has_secret"] is True
    assert body["mailu_created"] is True
    with crm.db() as db:
        row = db.get(ConnectionVault, body["id"])
        secrets = secret_box.decrypt_mapping(row.secrets_blob)
        assert secrets.get("smtp_password")
        assert len(secrets["smtp_password"]) >= 16
