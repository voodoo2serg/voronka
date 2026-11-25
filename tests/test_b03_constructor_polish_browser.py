"""B-03: Constructor CRM chrome polish + media overlay + vault Activate screenshots."""
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_b03_constructor_media_overlay_and_activate(crm, monkeypatch, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from app import secret_box as sb

    key = sb.derive_dev_key_from_seed("b03-shot")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    sb.reset_fernet_cache()

    root = Path("web/out").resolve()
    if not root.is_dir():
        pytest.fail("Run npm ci && npm run build:crm in web first")

    shot_dir = Path(os.getenv("CRM_SCREENSHOT_DIR", "/opt/cursor/artifacts/b03"))
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

        page.get_by_role("button", name="Запустить цепочку", exact=True).first.click()
        expect(page.locator("#action-constructor")).to_be_visible()
        expect(page.get_by_text("Клиент получает", exact=True)).to_be_visible()
        expect(page.get_by_text("Корзина CMS", exact=False)).to_have_count(0)
        # CRM-R3: square IA + preview pane
        expect(page.get_by_role("region", name="Превью шага").or_(page.locator(".constructor-preview"))).to_be_visible()
        expect(page.locator(".constructor-flow-squares")).to_be_visible()
        page.screenshot(path=str(shot_dir / "b03-constructor.png"), full_page=True)

        # CRM-R3: add a square, then top «+» → Медиатека overlay (constructor stays mounted).
        page.locator(".constructor-plus").first.click()
        page.get_by_role("menu", name="Типы шагов").get_by_role("menuitem").filter(
            has_text="Telegram"
        ).click()
        expect(page.locator(".constructor-square.selected")).to_be_visible()
        page.locator(".constructor-plus-top .constructor-plus").click()
        page.get_by_role("menu", name="Содержимое шага").get_by_role(
            "menuitem", name="Медиатека Файл из Signal (SSO)"
        ).click()
        expect(page.locator(".media-library")).to_be_visible(timeout=10000)
        expect(page.locator("#action-constructor")).to_be_visible()
        expect(
            page.get_by_role("dialog", name="Медиатека CRM").get_by_role("link", name="Открыть в Signal")
        ).to_be_visible()
        page.screenshot(path=str(shot_dir / "b03-media-overlay.png"), full_page=True)
        page.get_by_role("button", name="← К конструктору").click()
        expect(page.locator(".media-overlay-layer")).to_have_count(0)
        expect(page.locator("#action-constructor")).to_be_visible()

        login = crm.client.post(
            "/api/crm/login", json={"login": "owner", "password": "owner-password-123"}
        )
        assert login.status_code == 200, login.text
        crm.client.headers["Authorization"] = "Bearer " + login.json()["token"]
        me = crm.client.get("/api/crm/me").json()
        projects = me.get("projects") or []
        solo = next((p for p in projects if "Соло" in str(p.get("name") or "")), None)
        pid = (solo or projects[0])["id"] if projects else "p"
        created = crm.client.post(
            f"/api/crm/projects/{pid}/connection-vault",
            json={
                "type": "telegram_bot",
                "label": "B03 Activate bot",
                "meta": {"bot_username": "b03_bot"},
                "secrets": {"bot_token": "123456:BBBB-secret-token-ACT1"},
            },
        )
        assert created.status_code == 200, created.text

        page.locator("nav[aria-label='Разделы CRM'] button.nav").filter(
            has=page.get_by_text("Подключения", exact=True)
        ).click()
        expect(page.get_by_role("heading", name="Подключения", exact=True, level=1)).to_be_visible()
        expect(page.get_by_text("B03 Activate bot")).to_be_visible(timeout=15000)
        expect(page.get_by_role("link", name="Активировать webhook")).to_be_visible()
        page.screenshot(path=str(shot_dir / "b03-connections-activate.png"), full_page=True)

        assert not errors, errors
        browser.close()
