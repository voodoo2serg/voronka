"""CRM-R7: unassigned leads → «Старт»; project sales dashboard; convert; clients 10/50/Все."""
from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from sqlalchemy import select

from app.crm_models import Deal, Profile
from app.db import Project
from app.integrations import sign_payload
from app.projects_domain import START_SYSTEM_KEY, ensure_start_project
from test_crm import contact

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


def _auth_headers(secret: str, body: bytes):
    from app.db import now

    ts = str(int(now().timestamp()))
    return {
        "Authorization": f"Bearer {secret}",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sign_payload(secret, body, ts),
        "Content-Type": "application/json",
    }


def test_unassigned_lead_lands_in_start_ignoring_default_project_id(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.setenv("DEFAULT_PROJECT_ID", "p")  # legacy env must NOT steal unassigned leads
    monkeypatch.delenv("CRM_START_PROJECT_ID", raising=False)

    with crm.db.begin() as db:
        start = ensure_start_project(db)
        start_id = start.id

    payload = {
        "event_id": "r7-lead-start-1",
        "kind": "lead.created",
        "email": "r7.start@example.ru",
        "name": "Старт Лид",
        "service": "Интерес",
        "consent": True,
        "ads_consent": False,
        "source": "solo_pr",
        # no project_id → «Старт»
    }
    raw = json.dumps(payload).encode()
    r = crm.client.post(
        "/api/crm/integrations/leads",
        content=raw,
        headers=_auth_headers("inbound-secret", raw),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("duplicate") is False
    profile_id = body["profile_id"]
    with crm.db() as db:
        profile = db.get(Profile, profile_id)
        assert profile is not None
        assert profile.project_id == start_id
        project = db.get(Project, profile.project_id)
        assert project is not None and project.system_key == START_SYSTEM_KEY
        deals = list(db.scalars(select(Deal).where(Deal.profile_id == profile_id)))
        assert deals and deals[0].project_id == start_id


def test_explicit_project_id_still_wins(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.delenv("DEFAULT_PROJECT_ID", raising=False)
    payload = {
        "event_id": "r7-lead-explicit-1",
        "kind": "lead.created",
        "email": "r7.explicit@example.ru",
        "name": "Явный проект",
        "consent": True,
        "ads_consent": False,
        "source": "solo_pr",
        "project_id": "p",
    }
    raw = json.dumps(payload).encode()
    r = crm.client.post(
        "/api/crm/integrations/leads",
        content=raw,
        headers=_auth_headers("inbound-secret", raw),
    )
    assert r.status_code == 200, r.text
    with crm.db() as db:
        profile = db.get(Profile, r.json()["profile_id"])
        assert profile is not None
        assert profile.project_id == "p"


def test_start_dashboard_inbox_metrics_and_convert(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.delenv("DEFAULT_PROJECT_ID", raising=False)

    with crm.db.begin() as db:
        start = ensure_start_project(db)
        start_id = start.id

    raw = json.dumps(
        {
            "event_id": "r7-lead-convert-1",
            "kind": "lead.created",
            "email": "r7.convert@example.ru",
            "name": "К конвертации",
            "consent": True,
            "ads_consent": False,
            "source": "solo_pr",
        }
    ).encode()
    created = crm.client.post(
        "/api/crm/integrations/leads",
        content=raw,
        headers=_auth_headers("inbound-secret", raw),
    ).json()
    profile_id = created["profile_id"]

    dash = crm.client.get(f"/api/crm/projects/{start_id}/dashboard").json()
    assert dash["start_inbox"] is True
    assert dash["start_open_count"] >= 1
    assert dash["start_converted_count"] == 0
    assert any(x["id"] == profile_id for x in dash.get("start_leads") or [])
    assert "recent_deals" in dash
    assert "deals_count" in dash

    br = crm.client.post(
        "/api/crm/projects/p/branches",
        json={"name": "R7 ветка", "description": "convert"},
    ).json()
    converted = crm.client.post(
        f"/api/crm/contacts/{profile_id}/convert",
        json={"project_id": "p", "branch_id": br["id"], "person_role": "client"},
    )
    assert converted.status_code == 200, converted.text
    payload = converted.json()
    assert payload["ok"] is True
    assert payload["project_id"] == "p"
    assert payload["branch_id"] == br["id"]
    dest_id = payload["profile"]["id"]

    with crm.db() as db:
        start_row = db.get(Profile, profile_id)
        dest = db.get(Profile, dest_id)
        assert start_row is not None and dest is not None
        assert start_row.extra.get("converted_to_project_id") == "p"
        assert "converted" in (start_row.tags or [])
        assert dest.project_id == "p"
        assert dest.branch_id == br["id"]
        assert dest.person_role == "client"
        assert dest.contact_id == start_row.contact_id

    dash2 = crm.client.get(f"/api/crm/projects/{start_id}/dashboard").json()
    assert dash2["start_converted_count"] >= 1
    assert dash2["start_open_count"] == dash["start_open_count"] - 1

    # Convert again → 422
    again = crm.client.post(
        f"/api/crm/contacts/{profile_id}/convert",
        json={"project_id": "p", "person_role": "client"},
    )
    assert again.status_code == 422

    # Commercial contact cannot convert
    commercial = contact(crm, name="Уже в проекте")
    bad = crm.client.post(
        f"/api/crm/contacts/{commercial['id']}/convert",
        json={"project_id": "secret"},
    )
    assert bad.status_code == 422


def test_manager_scope_on_start_dashboard_and_convert(crm, monkeypatch):
    """Codex P1: managers only see/convert assigned Старт leads."""
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.delenv("DEFAULT_PROJECT_ID", raising=False)

    with crm.db.begin() as db:
        start = ensure_start_project(db)
        start_id = start.id

    mine_raw = json.dumps(
        {
            "event_id": "r7-mgr-mine",
            "kind": "lead.created",
            "email": "r7.mine@example.ru",
            "name": "Мой Старт",
            "consent": True,
            "source": "solo_pr",
        }
    ).encode()
    other_raw = json.dumps(
        {
            "event_id": "r7-mgr-other",
            "kind": "lead.created",
            "email": "r7.other@example.ru",
            "name": "Чужой Старт",
            "consent": True,
            "source": "solo_pr",
        }
    ).encode()
    mine = crm.client.post(
        "/api/crm/integrations/leads",
        content=mine_raw,
        headers=_auth_headers("inbound-secret", mine_raw),
    ).json()
    other = crm.client.post(
        "/api/crm/integrations/leads",
        content=other_raw,
        headers=_auth_headers("inbound-secret", other_raw),
    ).json()

    assert (
        crm.client.post(
            "/api/crm/staff",
            json={
                "login": "r7-mgr",
                "password": "manager-password-123",
                "role": "manager",
                "project_ids": [start_id, "p"],
            },
        ).status_code
        == 200
    )
    staff = crm.client.get("/api/crm/staff").json()
    mid = next(s["id"] for s in staff if s["login"] == "r7-mgr")
    with crm.db.begin() as db:
        db.get(Profile, mine["profile_id"]).owner_id = mid
        db.get(Profile, other["profile_id"]).owner_id = 123

    login = crm.client.post(
        "/api/crm/login",
        json={"login": "r7-mgr", "password": "manager-password-123"},
    ).json()
    crm.client.headers["Authorization"] = "Bearer " + login["token"]

    dash = crm.client.get(f"/api/crm/projects/{start_id}/dashboard").json()
    lead_ids = {x["id"] for x in dash.get("start_leads") or []}
    assert mine["profile_id"] in lead_ids
    assert other["profile_id"] not in lead_ids
    assert dash["start_open_count"] == 1

    denied = crm.client.post(
        f"/api/crm/contacts/{other['profile_id']}/convert",
        json={"project_id": "p", "person_role": "client"},
    )
    assert denied.status_code == 404

    ok = crm.client.post(
        f"/api/crm/contacts/{mine['profile_id']}/convert",
        json={"project_id": "p", "person_role": "client"},
    )
    assert ok.status_code == 200, ok.text


def test_contacts_and_frontend_r7_surface():
    constants = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    assert "CLIENT_PAGE_SIZES" in constants
    assert "{ value: 10, label: '10' }" in constants
    assert "{ value: 50, label: '50' }" in constants
    assert "{ value: 0, label: 'Все' }" in constants

    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "ProjectSalesDashboard" in ws
    assert "convertLeadForm" in ws
    assert "/contacts/" in ws and "/convert" in ws
    assert "dealPageSize" in ws
    assert "setDealOffset(0)" in ws
    assert "CLIENT_PAGE_SIZES" in ws
    overview = ws.split("section === 'overview'")[1].split("section === 'inbox'")[0]
    assert "ProjectSalesDashboard" in overview
    assert "WorkTodayPanel" in overview  # demoted, still present (R6 contract)

    domain = (ROOT / "app" / "projects_domain.py").read_text(encoding="utf-8")
    assert "assigned_profile_filter" in domain
    assert "message.received" in domain
    board = (ROOT / "app" / "crm_routers" / "projects_board.py").read_text(encoding="utf-8")
    assert "get_profile(db, admin, profile_id)" in board
    assert "manager_scope(admin)" in board

    dash_ui = (ROOT / "web" / "app" / "crm" / "sections" / "ProjectSalesDashboard.tsx").read_text(
        encoding="utf-8"
    )
    assert 'data-crm-r7="project-dashboard"' in dash_ui
    assert "start_inbox" in dash_ui
    assert "start_open_count" in dash_ui

    api = (ROOT / "app" / "crm_routers" / "projects_board.py").read_text(encoding="utf-8")
    assert '@router.post("/contacts/{profile_id}/convert")' in api
    assert "start_inbox_metrics" in api

    integ = (ROOT / "app" / "crm_routers" / "integrations.py").read_text(encoding="utf-8")
    assert "DEFAULT_PROJECT_ID" not in integ.split("def _apply_inbound_lead")[1].split("email, phone")[0] or (
        "start_project_id" in integ.split("def _apply_inbound_lead")[1].split("email, phone")[0]
    )
    lead_fn = integ.split("def _apply_inbound_lead")[1].split("def ")[0]
    assert "start_project_id" in lead_fn
    assert 'body.project_id or os.getenv("DEFAULT_PROJECT_ID"' not in lead_fn
