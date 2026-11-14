"""Voronka ops portal (VOR-20…25): host chrome, identities, wizards, identity campaigns."""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, contact  # noqa: F401
from app.db import Funnel, Identity, Contact, Connection
from app.crm_models import Campaign, Profile


@pytest.fixture
def ops(crm, monkeypatch):
    from app import ops_routers
    from app.ops_routers import portal as ops_portal
    from app.ops_routers import wizards as ops_wizards
    from app.ops_routers import deps as ops_deps

    for mod in (ops_portal, ops_wizards, ops_deps):
        if hasattr(mod, "Session"):
            monkeypatch.setattr(mod, "Session", crm.db)
    # Wizards import Session from crm_deps; crm fixture already patches crm_deps.Session
    return crm


def test_ops_host_keeps_mini_app_and_portals(ops):
    r = ops.client.get("/", headers={"Host": "voronka.solo-pr.ru"})
    assert r.status_code == 200
    assert len(r.text) > 100

    chrome = ops.client.get("/api/ops/portals").json()
    assert chrome["voronka_host"] == "voronka.solo-pr.ru"
    assert chrome["ops_path"] == "/ops/"
    assert chrome["mini_app_path"] == "/"
    assert "Боты" in chrome["language"]
    assert "Продажи" not in " ".join(chrome["language"])
    assert {x["id"] for x in chrome["links"]} >= {"cms", "crm", "voronka", "lms"}
    assert any(x["id"] == "voronka" and "/ops/" in x["url"] for x in chrome["links"])
    assert "crm.solo-pr.ru" in chrome["crm_url"]

    crm_chrome = ops.client.get("/api/crm/portals").json()
    assert crm_chrome["voronka_ops"].endswith("/ops/")


def test_ops_manager_forbidden(ops):
    assert (
        ops.client.post(
            "/api/crm/staff",
            json={"login": "mgr", "password": "manager-password1", "project_ids": ["p"], "role": "manager"},
        ).status_code
        == 200
    )
    r = ops.client.post("/api/crm/login", json={"login": "mgr", "password": "manager-password1"})
    assert r.status_code == 200
    ops.client.headers["Authorization"] = "Bearer " + r.json()["token"]
    assert ops.client.get("/api/ops/portals").status_code == 403


def test_channel_dm_and_support_wizards(ops):
    # Ensure connection active for validate_bindings
    with ops.db.begin() as db:
        for cid in ("tg",):
            c = db.get(Connection, cid)
            c.active = True
            c.project_id = "p"

    channel = ops.client.post(
        "/api/ops/projects/p/wizard/channel-dm",
        json={
            "name": "Канал DM",
            "channel_connection_id": "tg",
            "dm_connection_id": "tg",
            "wait_days": 2,
            "dm_text": "Личное после канала",
        },
    )
    assert channel.status_code == 200, channel.text
    body = channel.json()
    assert body["wait_seconds"] == 2 * 86400
    assert body["graph"]["nodes"]["wait_dm"]["type"] == "wait"
    assert body["graph"]["nodes"]["personal"]["text"] == "Личное после канала"

    support = ops.client.post(
        "/api/ops/projects/p/wizard/support",
        json={
            "name": "Support",
            "connection_id": "tg",
            "menu_text": "Чем помочь?",
            "branches": [
                {"label": "Оплата", "reply": "См. CRM оплаты", "escalate": False},
                {"label": "Другое", "reply": "Зовём менеджера", "escalate": True},
            ],
        },
    )
    assert support.status_code == 200, support.text
    s = support.json()
    assert "crm_inbox_url" in s
    assert "inbox" in s["crm_inbox_url"]
    assert s["graph"]["nodes"]["menu"]["type"] == "question"
    assert set(s["graph"]["nodes"]["menu"]["routes"]) == {"Оплата", "Другое"}

    with ops.db() as db:
        funnels = list(db.scalars(select(Funnel).where(Funnel.project_id == "p")))
        assert len(funnels) >= 2


def test_identities_and_campaign_by_identity(ops):
    with ops.db.begin() as db:
        db.get(Connection, "tg").project_id = "p"
        db.get(Connection, "tg").active = True
        db.add(Contact(id="ct1"))
        db.add(
            Identity(
                id="id1",
                contact_id="ct1",
                connection_id="tg",
                external_user_id="900",
                chat_id="900",
                name="Bot User",
            )
        )

    listed = ops.client.get("/api/ops/projects/p/identities?connection_id=tg").json()
    assert listed["total"] >= 1
    assert any(i["id"] == "id1" for i in listed["items"])

    camp = ops.client.post(
        "/api/crm/projects/p/campaigns",
        json={
            "connection_id": "tg",
            "title": "Blast",
            "text": "Hello identity",
            "identity_ids": ["id1"],
        },
    )
    assert camp.status_code == 200, camp.text
    data = camp.json()
    assert len(data["profile_ids"]) == 1
    with ops.db() as db:
        assert db.get(Campaign, data["id"]).profile_ids == data["profile_ids"]
        assert db.scalar(select(Profile).where(Profile.contact_id == "ct1")) is not None


def test_ops_templates_include_channel_and_support():
    from pathlib import Path
    import json

    templates = json.loads(Path("app/templates.json").read_text(encoding="utf-8"))
    ids = {t["id"] for t in templates}
    assert {"channel_wait_dm", "support_menu", "meeting_reminder"} <= ids


def test_health_on_voronka_host(ops, monkeypatch):
    ops.cache.set("worker:heartbeat", str(__import__("time").time()))
    r = ops.client.get("/health", headers={"Host": "voronka.solo-pr.ru"})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
