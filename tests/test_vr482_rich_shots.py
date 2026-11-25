"""VR-482 rich screenshots: canvas + slide-out editor + preview (CRM-R2: meta kinds, no CMS cart)."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_rich_constructor_shots(crm):
    from playwright.sync_api import expect, sync_playwright

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
        page.screenshot(path=str(shot_dir / "overview-stable.png"), full_page=True)

        page.get_by_role("button", name="Запустить цепочку", exact=True).first.click()
        expect(page.locator("#action-constructor")).to_be_visible()
        expect(page.get_by_text("Корзина CMS", exact=False)).to_have_count(0)

        # CRM-R2: add steps from meta node_kinds via «+»
        page.locator(".constructor-plus").first.click()
        menu = page.get_by_role("menu", name="Типы шагов")
        expect(menu).to_be_visible(timeout=10000)
        menu.get_by_role("menuitem").filter(has_text="Telegram").click()
        expect(page.locator(".constructor-square.selected")).to_be_visible()
        expect(page.locator(".constructor-editor-drawer.is-open")).to_be_visible()
        expect(page.locator(".constructor-preview-frame")).to_be_visible()
        page.locator(".constructor-editor textarea").fill(
            "Здравствуйте! Это превью цепочки на холсте."
        )
        page.locator(".constructor-plus").last.click()
        page.get_by_role("menu", name="Типы шагов").get_by_role("menuitem").filter(
            has_text="Письмо"
        ).click()
        page.screenshot(path=str(shot_dir / "constructor-canvas-drawer-preview.png"), full_page=True)

        assert not errors, errors
        browser.close()
