"""CRM-R8: drop «Прогрев» outreach module as product; ordinary Project seed only."""
from pathlib import Path

from sqlalchemy import select

from app.db import Project
from app.projects_domain import (
    PROGREV_PROJECT_NAME,
    PROGREV_SYSTEM_KEY,
    ensure_progrev_project,
)

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
    keyed = [p for p in me["projects"] if p.get("system_key") == PROGREV_SYSTEM_KEY]
    assert len(keyed) == 1
    row = keyed[0]
    assert row["name"] == PROGREV_PROJECT_NAME
    assert row.get("is_system") in (False, None, 0)

    board = crm.client.get("/api/crm/home/projects").json()
    card = next(x for x in board["items"] if x.get("system_key") == PROGREV_SYSTEM_KEY)
    assert card["name"] == PROGREV_PROJECT_NAME
    assert card.get("is_system") is False


def test_ensure_progrev_idempotent_and_not_system(crm):
    with crm.db.begin() as db:
        a = ensure_progrev_project(db)
        first_id = a.id
        b = ensure_progrev_project(db)
        assert a.id == b.id == first_id
        assert a.name == PROGREV_PROJECT_NAME
        assert bool(a.is_system) is False
        assert a.system_key == PROGREV_SYSTEM_KEY
        rows = list(db.scalars(select(Project).where(Project.system_key == PROGREV_SYSTEM_KEY)))
        assert len(rows) == 1


def test_progrev_rename_does_not_reseed_duplicate(crm):
    """Stable system_key keeps seed idempotent after staff renames the display name."""
    me = crm.client.get("/api/crm/me").json()
    row = next(p for p in me["projects"] if p.get("system_key") == PROGREV_SYSTEM_KEY)
    patched = crm.client.patch(
        f"/api/crm/projects/{row['id']}",
        json={"name": "Прогрев аудитории"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["name"] == "Прогрев аудитории"
    assert patched.json().get("is_system") in (False, None, 0)

    me2 = crm.client.get("/api/crm/me").json()
    keyed = [p for p in me2["projects"] if p.get("system_key") == PROGREV_SYSTEM_KEY]
    assert len(keyed) == 1
    assert keyed[0]["id"] == row["id"]
    assert keyed[0]["name"] == "Прогрев аудитории"
    assert sum(1 for p in me2["projects"] if p["name"] == PROGREV_PROJECT_NAME) == 0
