"""VR-256: cutover paint is Keycloak-only — no peer «Аварийный вход» CTA."""
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


def test_crm_empty_state_not_project_only_for_managers():
    src = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    assert "Аккаунт привязан" in src
    assert "Начните с проекта" not in src
    assert "empty-world" in src
