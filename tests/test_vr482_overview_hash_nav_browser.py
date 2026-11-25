"""VR-482: #overview hash must not trap nav; constructor has preview + editor drawer."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_overview_hash_nav_and_constructor_preview(crm, monkeypatch, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from app import secret_box as sb

    key = sb.derive_dev_key_from_seed("vr482-shot")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    sb.reset_fernet_cache()

    root = Path("web/out").resolve()
    if not root.is_dir():
        pytest.fail("Run npm ci && npm run build:crm in web first")

    shot_dir = Path(os.getenv("CRM_SCREENSHOT_DIR", "/opt/cursor/artifacts/vr482"))
    shot_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    crm.client.headers.pop("Authorization", None)

    def serve(route):
        req = route.request
        path = urlparse(req.url).path
        if path.startswith("/api/"):
            headers = {
                k: v
                for k, v in req.headers.items()
                if k.lower() in {"authorization", "content-type", "cookie"}
            }
            response = crm.client.request(
                req.method,
                req.url.split("crm.test", 1)[1],
                headers=headers,
                content=req.post_data_buffer,
            )
            route.fulfill(
                status=response.status_code,
                body=response.content,
                headers={"Content-Type": response.headers.get("content-type", "application/json")},
            )
            return
        relative = path.removeprefix("/crm/").lstrip("/")
        file = root / relative
        if file.is_dir():
            file = file / "index.html"
        if not file.is_file():
            route.fulfill(status=404, body="Not found")
            return
        route.fulfill(
            body=file.read_bytes(),
            content_type=mimetypes.guess_type(str(file))[0] or "application/octet-stream",
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.getenv("PLAYWRIGHT_EXECUTABLE_PATH") or None,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route("http://crm.test/**", serve)
        page.goto("http://crm.test/crm/")
        page.get_by_label("Логин", exact=True).fill("owner")
        page.get_by_label("Пароль", exact=True).fill("owner-password-123")
        page.get_by_role("button", name="Войти", exact=True).click()
        expect(page.get_by_role("heading", name="Проекты", exact=True, level=1)).to_be_visible()
        page.locator("button.project-card").filter(has_text="Соло-PR").click()
        expect(page.get_by_role("heading", name="Обзор", exact=True, level=1)).to_be_visible()
        assert page.evaluate("location.hash") == "#overview"
        page.screenshot(path=str(shot_dir / "overview-before-nav.png"), full_page=True)

        # Stuck #overview must not block leaving Обзор — nav syncs hash.
        page.locator("nav[aria-label='Разделы CRM'] button.nav").filter(has_text="Клиенты").click()
        expect(page.get_by_role("heading", name="Клиенты", exact=True, level=1)).to_be_visible()
        assert page.evaluate("location.hash") == "#contacts"

        page.locator("nav[aria-label='Разделы CRM'] button.nav").filter(has_text="Обзор").click()
        expect(page.get_by_role("heading", name="Обзор", exact=True, level=1)).to_be_visible()
        assert page.evaluate("location.hash") == "#overview"
        page.screenshot(path=str(shot_dir / "overview-after-nav.png"), full_page=True)

        # Deep-link hashchange → constructor
        page.evaluate("location.hash = '#today'")
        expect(page.locator("#action-constructor")).to_be_visible(timeout=10000)
        expect(page.locator(".constructor-preview")).to_be_visible()
        expect(page.locator(".constructor-flow-squares")).to_be_visible()
        expect(page.get_by_text("Клиент получает", exact=True)).to_be_visible()
        page.screenshot(path=str(shot_dir / "constructor-canvas-preview.png"), full_page=True)

        assert not errors, errors
        browser.close()
