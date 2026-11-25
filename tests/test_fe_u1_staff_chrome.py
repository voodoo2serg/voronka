"""FE-U1: CRM shared staff chrome markers (Signal portable pattern). Guest login stays FE-U3."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_staff_chrome_css_shipped():
    css = (ROOT / "web" / "app" / "styles" / "staff-chrome.css").read_text(encoding="utf-8")
    assert "--staff-chrome-accent-2" in css
    assert "#c45c26" in css
    assert ".staff-chrome__mark" in css
    assert ".staff-chrome__launcher" in css
    assert ".staff-chrome__logout" in css
    # Codex #46 follow-up: right-anchored popover; chrome stays under drawer-layer (40).
    assert "left: auto" in css
    assert "z-index: 30" in css


def test_crm_workspace_authed_chrome_slots():
    src = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert 'data-staff-chrome="crm"' in src
    assert "staff-chrome__mark" in src
    assert 'staff-chrome__product">CRM' in src or "staff-chrome__product\">CRM" in src
    assert "staff-chrome__platform" in src
    assert "staff-chrome__logout" in src
    assert "Выйти" in src
    assert "SignalLauncher" in src
    assert "staff-chrome__strip" in src
    assert "Запустить цепочку" in src
    # Chrome only after authed `me` gate — LoginScreen is the guest path.
    assert "if (!me)" in src
    assert "<LoginScreen" in src


def test_launcher_uses_staff_chrome_classes():
    src = (ROOT / "web" / "app" / "components" / "SignalLauncher.tsx").read_text(encoding="utf-8")
    assert "staff-chrome__launcher" in src
    assert "staff-chrome__popover" in src
    assert "▦" in src
    assert 'aria-label="Сервисы"' in src
    # Drop legacy left:0 popover class so staff-chrome right:0 wins (Codex P1).
    assert 'className="staff-chrome__popover"' in src
    assert "signal-service-popover" not in src
    assert "signal-service-switcher" not in src


def test_guest_login_fe_u3_untouched():
    """CRM-R1: no guest CTA island; still no staff-chrome on login gate."""
    login = (ROOT / "web" / "app" / "crm" / "components" / "LoginScreen.tsx").read_text(encoding="utf-8")
    assert "staff-chrome" not in login
    assert "SignalLauncher" not in login
    assert "Войти" in login
    assert "location.replace" in login
    assert "Переход к единому входу" not in login
    assert 'className="auth-ctas"' not in login
    assert "Регистрация" not in login


def test_layout_imports_staff_chrome():
    layout = (ROOT / "web" / "app" / "layout.tsx").read_text(encoding="utf-8")
    assert "staff-chrome.css" in layout
