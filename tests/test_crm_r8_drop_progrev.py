"""CRM-R8: drop «Прогрев» outreach module as product; ordinary Project seed only."""
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import Project
from app.projects_domain import PROGREV_PROJECT_NAME, ensure_progrev_project

ROOT = Path(__file__).resolve().parents[1]

pytest_plugins = ("test_operational", "test_crm")


def test_workspace_has_no_progrev_product_surface():
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    constants = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    icons = (ROOT / "web" / "app" / "crm" / "icons.tsx").read_text(encoding="utf-8")
    analytics = (ROOT / "web" / "app" / "crm" / "sections" / "AnalyticsSection.tsx").read_text(
        encoding="utf-8"
    )

    assert "['outreach'" not in constants
    assert "Прогрев" not in constants
    assert "outreachForm" not in ws
    assert "Прогрев (dry_run)" not in ws
    assert "section === 'outreach'" not in ws
    assert "outreach-batches" not in ws
    assert "Прогрев телефонной базы" not in ws
    assert "outreach:" not in icons
    assert "Прогрев (партии)" not in analytics
    assert "legacy" in analytics.lower()


def test_progrev_ordinary_project_seeded_on_me_and_home(crm):
    me = crm.client.get("/api/crm/me").json()
    names = {p["name"] for p in me["projects"]}
    assert PROGREV_PROJECT_NAME in names
    row = next(p for p in me["projects"] if p["name"] == PROGREV_PROJECT_NAME)
    assert row.get("is_system") in (False, None, 0)
    assert not row.get("system_key")

    board = crm.client.get("/api/crm/home/projects").json()
    board_names = {x["name"] for x in board["items"]}
    assert PROGREV_PROJECT_NAME in board_names
    card = next(x for x in board["items"] if x["name"] == PROGREV_PROJECT_NAME)
    assert card.get("is_system") is False
    assert card.get("system_key") in (None, "")


def test_ensure_progrev_idempotent_and_not_system(crm):
    with crm.db.begin() as db:
        a = ensure_progrev_project(db)
        first_id = a.id
        b = ensure_progrev_project(db)
        assert a.id == b.id == first_id
        assert a.name == PROGREV_PROJECT_NAME
        assert bool(a.is_system) is False
        assert a.system_key is None
        rows = list(db.scalars(select(Project).where(Project.name == PROGREV_PROJECT_NAME)))
        assert len(rows) == 1
