"""Square constructor IA (CRM-R2/R3): start «+» → node_kinds menu, no CMS cart hop.

Covers the owner lock vs live tip 564f0b3 (cart-first <a href=cms/#/admin/cart>).
"""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_start_plus_opens_kind_menu_not_cms(crm, tmp_path):
    from playwright.sync_api import expect, sync_playwright

    root = Path("web/out").resolve()
    if not root.is_dir():
        pytest.fail("Run npm ci && npm run build:crm in web first")

    shot_dir = Path(os.getenv("CRM_SCREENSHOT_DIR") or tmp_path / "square-ia")
    shot_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    navigated_cms: list[str] = []
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
        page.on(
            "framenavigated",
            lambda frame: navigated_cms.append(frame.url)
            if frame == page.main_frame and ("cms.solo-pr" in frame.url or "/admin/cart" in frame.url)
            else None,
        )
        page.route("http://crm.test/**", serve)
        page.goto("http://crm.test/crm/?breakglass=1")
        page.get_by_label("Логин", exact=True).fill("owner")
        page.get_by_label("Пароль", exact=True).fill("owner-password-123")
        page.get_by_role("button", name="Аварийный вход", exact=True).click()
        expect(page.get_by_role("heading", name="Проекты", exact=True, level=1)).to_be_visible()
        page.locator("button.project-card").filter(has_text="Соло-PR").click()
        expect(page.get_by_role("heading", name="Обзор", exact=True, level=1)).to_be_visible()

        page.get_by_role("button", name="Запустить цепочку", exact=True).first.click()
        expect(page.locator("#action-constructor")).to_be_visible()
        expect(page.get_by_text("Клиент получает", exact=True)).to_be_visible()
        expect(page.locator(".constructor-flow-squares")).to_be_visible()

        # No CMS cart staff surface
        expect(page.get_by_text("Корзина CMS", exact=False)).to_have_count(0)
        expect(page.get_by_role("link", name="Набрать корзину в CMS")).to_have_count(0)
        expect(page.locator('a[href*="admin/cart"]')).to_have_count(0)
        expect(page.locator('a.constructor-plus')).to_have_count(0)

        # Start-square «+» opens kind menu (does NOT navigate away)
        before = page.url
        page.get_by_test_id("constructor-plus-start").click()
        menu = page.get_by_role("menu", name="Типы шагов")
        expect(menu).to_be_visible()
        for label in ("Контакт", "Опрос", "Письмо", "Telegram", "Оплата"):
            expect(menu.get_by_role("menuitem").filter(has_text=label)).to_be_visible()
        assert page.url == before
        assert not navigated_cms, navigated_cms
        page.screenshot(path=str(shot_dir / "square-ia-kind-menu.png"), full_page=True)

        # Add square from kinds
        menu.get_by_role("menuitem").filter(has_text="Telegram").click()
        expect(page.locator(".constructor-square.selected")).to_be_visible()
        expect(page.locator(".constructor-square-sender")).to_be_visible()

        # Top «+» → Медиатека overlay (constructor stays mounted)
        page.locator(".constructor-plus-top .constructor-plus").click()
        top = page.get_by_role("menu", name="Содержимое шага")
        expect(top.get_by_text("Текст / детали")).to_be_visible()
        top.get_by_test_id("square-media-library").click()
        expect(page.locator(".media-library")).to_be_visible(timeout=10000)
        expect(page.locator("#action-constructor")).to_be_visible()
        page.screenshot(path=str(shot_dir / "square-ia-top-media.png"), full_page=True)
        page.get_by_role("button", name="← К конструктору").click()
        expect(page.locator("#action-constructor")).to_be_visible()

        # Save / Проверить chrome present
        save = page.get_by_role("button", name="Сохранить", exact=True)
        expect(save).to_be_visible()
        expect(page.get_by_role("button", name="Проверить", exact=True)).to_be_visible()
        expect(page.get_by_text("Сохранить сделает эту цепочку базой выбранного проекта/ветки", exact=True)).to_be_visible()
        save.click()
        expect(page.get_by_text("Сейчас базовая цепочка проекта", exact=True)).to_be_visible()

        assert not navigated_cms, navigated_cms
        assert not errors, errors
        browser.close()
