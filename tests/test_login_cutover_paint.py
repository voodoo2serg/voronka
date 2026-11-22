"""VR-256 / VR-273 / AUTH-M2: cutover paint + CRM guest declutter (login/register/forgot)."""
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


def test_auth_m2_guest_card_only_login_register_forgot():
    """AUTH-M2 DoD: guest card = prefs RU, ≤3 CTA, no platform essay / Keycloak essay."""
    for src in _login_sources():
        assert ">Войти<" in src or ">Войти\n" in src or "Войти" in src
        assert "Регистрация" in src
        assert "Забыли пароль?" in src
        assert "register=1" in src or "registerPath" in src
        # No platform essay / IdP promo clutter on guest card (comments OK).
        assert "Клиенты, сделки и платежи" not in src
        assert "Боты · Сценарии" not in src
        assert "Штатный вход — через единый аккаунт SIGNAL" not in src
        assert "Восстановление только через единый аккаунт SIGNAL" not in src
        assert "Войти через единый аккаунт SIGNAL" not in src
        # Access-request block belongs on no-access empty-state, not default guest clutter.
        assert 'className="access-request' not in src


def test_fe_u3_crm_guest_cta_hierarchy():
    """FE-U3: Войти is filled primary; Регистрация outline secondary (not the reverse)."""
    login = (ROOT / "web" / "app" / "crm" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8")
    css = (ROOT / "web" / "app" / "globals.css").read_text(encoding="utf-8")
    auth = login.split('className="auth-ctas"', 1)[1].split("</div>", 1)[0]
    voiti = auth.find("Войти")
    reg = auth.find("Регистрация")
    assert 0 <= voiti < reg, "Войти must appear before Регистрация in auth-ctas"
    assert 'className="primary full"' in auth
    assert auth.index('className="primary full"') < auth.index("auth-secondary")
    assert "a.primary" in css
    assert "a.auth-secondary" in css
    # Login gate uses Solo-PR terracotta — not orphan grey primary paint for SSO link.
    assert ".login-screen" in css and "--accent:#a0463c" in css
    assert "button.primary" in css  # local form submit still primary


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
    assert "WorkTodayPanel" in src
    assert "work-today" in src or "/work-today" in src
    assert "SignalPersonActions" in src
    assert "Запросить доступ" in src or "ACCESS_REQUEST" in src
    assert "SalesRunbookPanel" in src
    assert "sales-runbook" in src or "/sales-runbook" in src
    assert "HistoryTimeline" in src
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
    assert "mail live" in panel.lower() or "Q11" in panel or "production_mail" in panel
    assert "can_enable_live_from_ui" in portals or "can_enable_live_from_ui" in panel or "не включаются" in panel
    assert "ROBOKASSA_LIVE_READY" in panel
    assert "SIGNAL" in panel or "signal" in panel
    assert "LMS" in panel
    # No UI control that flips live money.
    assert "setLiveReady" not in panel
    assert "ROBOKASSA_LIVE_READY=1" not in panel or "не выставляет" in panel
    assert "def sales_runbook" in portals
    assert 'can_enable_live_from_ui": False' in portals or "can_enable_live_from_ui\": False" in portals
    assert "production_mail" in portals
    assert "Q11" in portals


def test_history_timeline_paints_pay_and_mail_stages():
    hist = (ROOT / "web" / "app" / "crm" / "components" / "HistoryTimeline.tsx").read_text(encoding="utf-8")
    assert "Оплата" in hist
    assert "Письмо" in hist
    assert "sandbox" in hist.lower()
    assert "deal.paid" in hist
    assert "stage-${item.stage}" in hist
    assert "'pay'" in hist and "'mail'" in hist
    assert "history-stage" in hist
