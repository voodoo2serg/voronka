"""CRM-R6 browser: after login, home face = project cards (not WorkToday / lead list)."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_crm import crm
from test_operational import system

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_crm_r6_home_projects_board_face(crm):
    from playwright.sync_api import expect, sync_playwright

    root = Path("web/out").resolve()
    if not root.is_dir():
        pytest.fail("Run npm ci && npm run build in web first")
    shot_dir = Path(os.getenv("CRM_SCREENSHOT_DIR", "/tmp")).resolve()
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
        page.goto("http://crm.test/crm/?breakglass=1")
        page.get_by_label("Логин", exact=True).fill("owner")
        page.get_by_label("Пароль", exact=True).fill("owner-password-123")
        page.get_by_role("button", name="Аварийный вход", exact=True).click()

        expect(page.get_by_role("heading", name="Проекты", exact=True, level=1)).to_be_visible()
        board = page.locator('[data-crm-home="projects-board"]')
        expect(board).to_be_visible()
        expect(page.locator("#work-today-contour")).to_have_count(0)
        expect(board.get_by_text("Контакты", exact=True).first).to_be_visible()
        expect(board.get_by_text("Деньги", exact=True).first).to_be_visible()
        expect(board.get_by_text("Ближайшие задачи", exact=True).first).to_be_visible()
        assert page.evaluate("location.hash") in ("#home", "#projects", "")
        page.screenshot(path=str(shot_dir / "crm-r6-home-projects-board.png"), full_page=True)

        page.locator("button.project-card").filter(has_text="Соло-PR").click()
        expect(page.get_by_role("heading", name="Обзор", exact=True, level=1)).to_be_visible()
        expect(page.locator("#work-today-contour")).to_be_visible()
        page.screenshot(path=str(shot_dir / "crm-r6-overview-not-home.png"), full_page=True)

        page.get_by_role("button", name="Проекты", exact=False).first.click()
        expect(board).to_be_visible()
        expect(page.locator("#work-today-contour")).to_have_count(0)
        page.screenshot(path=str(shot_dir / "crm-r6-back-to-board.png"), full_page=True)

        assert not errors, errors
        browser.close()
