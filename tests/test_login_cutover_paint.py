"""VR-256 / VR-273: cutover paint + CRM help (no project wall, access path, today CTA)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _login_sources():
    return [
        (ROOT / "web" / "app" / "crm" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8"),
        (ROOT / "web" / "app" / "ops" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8"),
    ]


def test_no_peer_emergency_button_label_in_jsx():
    for src in _login_sources():
        # Visible peer CTA removed; form submit label may still say аварийный after gesture.
        assert "Аварийный вход\n" not in src
        assert ">Аварийный вход<" not in src
        assert "onClick={() => setShowLocal(true)}" not in src or "BREAK_GLASS" in src
        assert "BREAK_GLASS_CLICKS" in src
        assert "Штатный вход — через единый аккаунт SIGNAL (Keycloak)." in src


def test_crm_login_has_access_request_not_dead_owner_text():
    login = (ROOT / "web" / "app" / "crm" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8")
    portals = (ROOT / "web" / "app" / "crm" / "portals.ts").read_text(encoding="utf-8")
    assert "Обратитесь к владельцу проекта" not in login
    assert "спросите владельца" not in login.lower()
    assert "ACCESS_REQUEST" in login
    assert "crm_access" in login
    assert "Нет доступа" in login
    assert "access-denied-card" in login
    assert "Запросить доступ" in portals
    assert "cmsStaff" in portals and "PORTAL_URLS.cms" in portals
    assert "view=access" in portals


def test_ops_login_auth_l2_empty_state():
    login = (ROOT / "web" / "app" / "ops" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8")
    portals = (ROOT / "web" / "app" / "ops" / "portals.ts").read_text(encoding="utf-8")
    assert "Обратитесь к владельцу" not in login
    assert "Нет доступа" in login
    assert "access-denied-card" in login
    assert "crm_access" in login
    assert "ACCESS_REQUEST" in login
    assert "Запросить доступ" in portals
    assert "view=access" in portals


def test_crm_empty_state_not_project_wall():
    src = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "Начните с проекта" not in src
    # Linked staff keep section shell; project is optional filter/banner only.
    assert "project-optional" in src
    assert "empty-world" in src
    assert "Работа сегодня" in src
    assert "goToday" in src
    assert "Запросить доступ" in src or "ACCESS_REQUEST" in src
    assert "SalesRunbookPanel" in src
    assert "sales-runbook" in src or "/sales-runbook" in src
    # Must not gate the whole main area on create-project only.
    assert "{!pid ? (" not in src
    assert "ПРОЕКТ · фильтр" in src


def test_ops_has_work_today_cta():
    src = (ROOT / "web" / "app" / "ops" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "Работа сегодня" in src
    assert "crmTodayHref" in src
    assert "SalesRunbookPanel" in src


def test_sales_runbook_panel_is_honest_no_live_flip():
    panel = (ROOT / "web" / "app" / "crm" / "sections" / "SalesRunbookPanel.tsx").read_text(encoding="utf-8")
    portals = (ROOT / "app" / "portals.py").read_text(encoding="utf-8")
    assert "sandbox vs live" in panel
    assert "can_enable_live_from_ui" in portals or "can_enable_live_from_ui" in panel or "не включаются" in panel
    assert "ROBOKASSA_LIVE_READY" in panel
    assert "SIGNAL" in panel or "signal" in panel
    assert "LMS" in panel
    # No UI control that flips live money.
    assert "setLiveReady" not in panel
    assert "ROBOKASSA_LIVE_READY=1" not in panel or "не выставляет" in panel
    assert "def sales_runbook" in portals
    assert 'can_enable_live_from_ui": False' in portals or "can_enable_live_from_ui\": False" in portals
