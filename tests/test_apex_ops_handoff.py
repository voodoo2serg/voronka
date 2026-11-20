"""VR-248: apex browser page must hand off to /ops/, not only «закройте окно»."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_admin_html_browser_handoff_points_to_ops():
    html = (ROOT / "app" / "admin.html").read_text(encoding="utf-8")
    assert 'href="/ops/"' in html
    assert "Mini App" in html or "Telegram" in html
    assert "Закройте обычный браузер" not in html


def test_editor_js_shows_ops_handoff_without_telegram():
    js = (ROOT / "app" / "editor.js").read_text(encoding="utf-8")
    assert "showBrowserOpsHandoff" in js
    assert 'href="/ops/"' in js or 'a.href="/ops/"' in js
    assert "не открывайте ссылку в браузере" not in js
