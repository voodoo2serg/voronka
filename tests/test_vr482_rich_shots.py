"""VR-482 rich screenshots: canvas + slide-out editor + preview with cart steps."""
import mimetypes
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

import pytest
from test_crm import crm  # noqa: F401
from test_operational import system  # noqa: F401

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def test_rich_constructor_shots(crm, monkeypatch, tmp_path):
    from playwright.sync_api import expect, sync_playwright
    from app import secret_box as sb

    key = sb.derive_dev_key_from_seed("vr482-rich")
    monkeypatch.setenv("CRM_SECRETS_FERNET_KEY", key)
    monkeypatch.setenv("CRM_CONNECTION_SECRETS_PATH", str(tmp_path / "conn.env"))
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "cart-secret-test")
    monkeypatch.setenv("SOLO_PR_CMS_URL", "https://cms.test")
    sb.reset_fernet_cache()

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "cart": {
                    "id": "c-uuid",
                    "cart_id": "c-uuid",
                    "project_id": "p",
                    "items": [
                        {
                            "asset_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                            "kind": "tg_post",
                            "title": "Приветствие ТГ",
                        },
                        {
                            "asset_id": "ffffffff-1111-2222-3333-444444444444",
                            "kind": "email_body",
                            "title": "Письмо welcome",
                        },
                    ],
                    "kinds": ["tg_post", "email_body"],
                }
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            return _Resp()

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

    with patch("app.chain_cart.httpx.Client", _Client):
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
            cart_btn = page.locator(".constructor-cart-items button").first
            expect(cart_btn).to_be_visible(timeout=10000)
            cart_btn.click()
            expect(page.locator(".constructor-square.selected")).to_be_visible()
            expect(page.locator(".constructor-editor-drawer.is-open")).to_be_visible()
            expect(page.locator(".constructor-preview-frame")).to_be_visible()
            page.locator(".constructor-editor textarea").fill(
                "Здравствуйте! Это превью цепочки на холсте."
            )
            page.locator(".constructor-cart-items button").nth(1).click()
            page.screenshot(path=str(shot_dir / "constructor-canvas-drawer-preview.png"), full_page=True)

            assert not errors, errors
            browser.close()
