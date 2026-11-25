"""CRM-R6: first screen after SSO = Projects board (not lead inbox / WorkToday)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_crm_r6_home_is_projects_board_not_leads():
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    board = (ROOT / "web" / "app" / "crm" / "sections" / "ProjectsBoardSection.tsx").read_text(encoding="utf-8")
    constants = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    api = (ROOT / "app" / "crm_routers" / "projects_board.py").read_text(encoding="utf-8")

    assert "useState('home')" in ws
    assert "writeHash('#home')" in ws
    assert "ProjectsBoardSection" in ws
    assert "/home/projects" in ws
    assert "data-crm-home=\"projects-board\"" in board
    assert "contacts_count" in board
    assert "received_minor" in board
    assert "next_tasks" in board or "Ближайшие задачи" in board
    assert "project-pick" in ws
    assert "Необязательный фильтр" not in ws
    assert "можно открыть сразу" not in ws
    assert "['home', 'Проекты']" in constants
    assert "@router.get(\"/home/projects\")" in api
    assert "project_board_row" in api

    home_block = ws.split("section === 'home'")[1].split("section === 'overview'")[0]
    assert "ProjectsBoardSection" in home_block
    assert "WorkTodayPanel" not in home_block
    overview_block = ws.split("section === 'overview'")[1].split("section === 'inbox'")[0]
    assert "WorkTodayPanel" in overview_block


def test_crm_r6_go_home_clears_project_filter():
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    # goSection('home') must clear pid so board is the face, not a project-scoped wall.
    assert "if (id === 'home')" in ws
    assert "setPid('')" in ws
    assert "setWorkToday(null)" in ws
