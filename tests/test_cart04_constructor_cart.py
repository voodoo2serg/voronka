"""CART-04: Constructor consumes CMS chain cart; «+» from cart kinds; no paste asset_id."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _login(crm):
    r = crm.client.post("/api/crm/login", json={"login": "owner", "password": "owner-password-123"})
    assert r.status_code == 200
    crm.client.headers["Authorization"] = "Bearer " + r.json()["token"]


def test_cart_kind_map_and_enrich():
    from app.chain_cart import CART_KIND_TO_NODE, enrich_cart_payload, map_cart_kind_to_node

    assert map_cart_kind_to_node("tg_post") == "telegram"
    assert map_cart_kind_to_node("email_body") == "email"
    assert map_cart_kind_to_node("poll") == "survey"
    assert CART_KIND_TO_NODE["tg_circle"] == "telegram"

    enriched = enrich_cart_payload(
        {
            "id": "cart-1",
            "project_id": "p",
            "items": [
                {"asset_id": "asset-tg-1", "kind": "tg_post", "title": "Пост 1"},
                {"asset_id": "asset-mail-1", "kind": "email_body", "title": "Письмо"},
                {"asset_id": "asset-poll-1", "kind": "poll"},
            ],
            "kinds": ["tg_post", "email_body", "poll"],
        }
    )
    assert enriched["empty"] is False
    assert enriched["cart_id"] == "cart-1"
    assert set(enriched["node_kinds"]) == {"telegram", "email", "survey"}
    assert enriched["items"][0]["node_kind"] == "telegram"
    assert enriched["items"][1]["node_kind"] == "email"
    assert enriched["items"][2]["node_kind"] == "survey"


def test_constructor_cart_unconfigured_soft_empty(crm, monkeypatch):
    _login(crm)
    monkeypatch.delenv("SOLO_PR_WEBHOOK_SECRET", raising=False)
    r = crm.client.get("/api/crm/constructor/cart?project_id=p")
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert body["configured"] is False
    assert body["error"] == "cms_hmac_unconfigured"
    assert "/#/admin/cart/p" in body["cms_cart_url"]


def test_constructor_cart_fetches_cms_by_project(crm, monkeypatch):
    _login(crm)
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "cart-secret-test")
    monkeypatch.setenv("SOLO_PR_CMS_URL", "https://cms.test")

    cms_payload = {
        "cart": {
            "id": "c-uuid",
            "cart_id": "c-uuid",
            "project_id": "p",
            "items": [
                {"asset_id": "a1", "kind": "tg_post", "title": "TG пост"},
                {"asset_id": "a2", "kind": "email_body", "title": "Email"},
            ],
            "kinds": ["tg_post", "email_body"],
        }
    }

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return cms_payload

    captured = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return _Resp()

    with patch("app.chain_cart.httpx.Client", _Client):
        r = crm.client.get("/api/crm/constructor/cart?project_id=p")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["configured"] is True
    assert body["cart_id"] == "c-uuid"
    assert body["empty"] is False
    assert [i["node_kind"] for i in body["items"]] == ["telegram", "email"]
    assert captured["url"] == "https://cms.test/api/integrations/carts"
    assert captured["params"] == {"project_id": "p"}
    assert "X-Voronka-Timestamp" in captured["headers"]
    assert "X-Voronka-Signature" in captured["headers"]
    assert captured["headers"]["X-Voronka-Signature"].startswith("sha256=")


def test_constructor_cart_prefers_cart_id(crm, monkeypatch):
    _login(crm)
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "cart-secret-test")
    monkeypatch.setenv("CHAIN_CART_API_URL", "https://cms.test/api/integrations/carts")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "cart": {
                    "id": "cart-xyz",
                    "project_id": "p",
                    "items": [{"asset_id": "poll-1", "kind": "poll", "title": "Опрос"}],
                }
            }

    captured = {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            captured["params"] = params
            return _Resp()

    with patch("app.chain_cart.httpx.Client", _Client):
        r = crm.client.get("/api/crm/constructor/cart?project_id=p&cart_id=cart-xyz")
    assert r.status_code == 200
    assert captured["params"] == {"cart_id": "cart-xyz"}
    assert r.json()["items"][0]["node_kind"] == "survey"


def test_constructor_cart_404_is_empty(crm, monkeypatch):
    _login(crm)
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "cart-secret-test")
    monkeypatch.setenv("SOLO_PR_CMS_URL", "https://cms.test")

    class _Resp:
        status_code = 404

        def raise_for_status(self):
            raise AssertionError("404 must not raise")

        def json(self):
            return {"error": "cart not found", "cart": None}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    with patch("app.chain_cart.httpx.Client", _Client):
        r = crm.client.get("/api/crm/constructor/cart?project_id=p")
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert body.get("not_found") is True


def test_constructor_cart_rejects_foreign_project(crm, monkeypatch):
    _login(crm)
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "cart-secret-test")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "cart": {
                    "id": "c1",
                    "project_id": "secret",
                    "items": [{"asset_id": "x", "kind": "tg_post"}],
                }
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return _Resp()

    with patch("app.chain_cart.httpx.Client", _Client):
        r = crm.client.get("/api/crm/constructor/cart?project_id=p&cart_id=c1")
    assert r.status_code == 403


def test_hmac_empty_body_matches_cms_contract(monkeypatch):
    """CMS GET signs over empty string; Voronka must match."""
    from app.chain_cart import _signed_headers
    from app.integrations import normalize_signature, sign_payload, verify_signed_request

    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "shared-secret")
    headers = _signed_headers(b"")
    ts = headers["X-Voronka-Timestamp"]
    sig = headers["X-Voronka-Signature"]
    assert verify_signed_request("shared-secret", b"", ts, sig)
    assert normalize_signature(sig) == sign_payload("shared-secret", b"", ts)


def test_frontend_no_paste_asset_id_and_cart_driven_plus():
    ctor = (ROOT / "web" / "app" / "crm" / "sections" / "ActionConstructorSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "constructor/cart" in ctor
    assert "addFromCartItem" in ctor
    assert "Набрать корзину в CMS" in ctor
    assert "Шаги из корзины" in ctor
    assert "media_asset_id: item.asset_id" in ctor or "media_asset_id: item.asset_id," in ctor
    # No manual paste of asset_id
    assert "вставьте asset_id" not in ctor.lower()
    assert "placeholder=\"вставьте" not in ctor
    assert "Настроить в Подключениях" in ctor
    assert "smtp_password" not in ctor
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "cartId" in ws
    assert "q.get('cart')" in ws
    # Codex P2: deep-linked cart must not stick after project switch
    assert "cartForProject" in ws
    assert "setCartId('')" in ws or 'setCartId("")' in ws


def test_meta_exposes_cart_kind_map(crm):
    _login(crm)
    meta = crm.client.get("/api/crm/constructor/meta?project_id=p").json()
    assert meta["cart_kind_map"]["tg_post"] == "telegram"
    assert meta["cart_kind_map"]["email_body"] == "email"
    assert meta["cart_kind_map"]["poll"] == "survey"


def test_save_chain_with_cart_attached_asset(crm):
    _login(crm)
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "Из корзины",
            "nodes": [
                {
                    "id": "n1",
                    "kind": "telegram",
                    "title": "TG пост",
                    "config": {"cart_kind": "tg_post"},
                    "media_asset_id": "asset-from-cart-001",
                },
                {"id": "n2", "kind": "finish", "title": "Финиш", "config": {}},
            ],
        },
    ).json()
    assert created["nodes"][0]["media_asset_id"] == "asset-from-cart-001"
    assert created["nodes"][0]["config"]["cart_kind"] == "tg_post"
