"""B-01: rename/nav — Боты/Ops launcher, #connections, Запустить цепочку, Команда."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_ops_label():
    src = (ROOT / "web" / "app" / "components" / "SignalLauncher.tsx").read_text(encoding="utf-8")
    assert "Боты / Ops" in src
    assert "label:'Воронка'" not in src
    assert "voronka.solo-pr.ru/ops/" in src


def test_portal_chrome_ops_label():
    src = (ROOT / "app" / "portals.py").read_text(encoding="utf-8")
    assert '"Боты / Ops"' in src


def test_settings_is_team_only():
    src = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    assert "['settings', 'Команда']" in src
    assert "Команда и подключения" not in src


def test_dead_section_query_gone():
    for rel in (
        "web/app/crm/sections/ActionConstructorSection.tsx",
        "web/app/crm/sections/PaymentsSection.tsx",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "?section=connections" not in src
        assert "#connections" in src


def test_vault_telegram_activate_cta():
    src = (ROOT / "web" / "app" / "crm" / "sections" / "ConnectionsVaultSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "Активировать webhook" in src
    assert "voronkaOpsBotsHref" in src
    portals = (ROOT / "web" / "app" / "crm" / "portals.ts").read_text(encoding="utf-8")
    assert "voronkaOpsBotsHref" in portals
    assert "section: 'bots'" in portals or 'section: "bots"' in portals or "section?: string" in portals


def test_primary_cta_start_chain():
    crm = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "Запустить цепочку" in crm
    assert "Работа сегодня" not in crm
    ops = (ROOT / "web" / "app" / "ops" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "Запустить цепочку" in ops
    assert "Работа сегодня" not in ops
