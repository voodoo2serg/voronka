"""CRM-R7 browser: project sales dashboard, Старт inbox, clients 10/50/Все."""
import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest
from app.integrations import sign_payload
from app.projects_domain import ensure_start_project
from test_crm import crm
from test_operational import system

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")


def _auth_headers(secret: str, body: bytes):
    from app.db import now

    ts = str(int(now().timestamp()))
    return {
        "Authorization": f"Bearer {secret}",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sign_payload(secret, body, ts),
        "Content-Type": "application/json",
    }


def test_crm_r7_start_and_project_dashboard(crm, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    root = Path("web/out").resolve()
    if not root.is_dir():
        pytest.fail("Run npm ci && npm run build in web first")

    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.delenv("DEFAULT_PROJECT_ID", raising=False)
    with crm.db.begin() as db:
        start = ensure_start_project(db)
        start_id = start.id
        start_name = start.name

    raw = json.dumps(
        {
            "event_id": "r7-browser-lead-1",
            "kind": "lead.created",
            "email": "r7.browser@example.ru",
            "name": "R7 Browser Lead",
            "consent": True,
            "ads_consent": False,
            "source": "solo_pr",
        }
    ).encode()
    lead = crm.client.post(
        "/api/crm/integrations/leads",
        content=raw,
        headers=_auth_headers("inbound-secret", raw),
    )
    assert lead.status_code == 200, lead.text

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

        expect(page.locator('[data-crm-home="projects-board"]')).to_be_visible()
        page.locator("button.project-card").filter(has_text=start_name).click()
        expect(page.get_by_role("heading", name="Обзор", exact=True, level=1)).to_be_visible()
        dash = page.locator('[data-crm-r7="project-dashboard"]')
        expect(dash).to_be_visible()
        expect(page.get_by_text("Старт · непривязанный интерес", exact=True)).to_be_visible()
        expect(dash.get_by_text("R7 Browser Lead", exact=True).first).to_be_visible()
        page.screenshot(path=str(shot_dir / "crm-r7-start-dashboard.png"), full_page=True)

        dash.get_by_role("button", name="Клиенты", exact=True).first.click()
        expect(page.get_by_role("heading", name="Клиенты", exact=True, level=1)).to_be_visible()
        for label in ("10", "50", "Все"):
            expect(page.get_by_role("group", name="Ветка и размер страницы").get_by_role("button", name=label, exact=True)).to_be_visible()
        page.screenshot(path=str(shot_dir / "crm-r7-clients-pagination.png"), full_page=True)

        page.get_by_role("navigation").get_by_role("button", name="Сделки", exact=True).click()
        expect(page.get_by_role("heading", name="Сделки", exact=True, level=1)).to_be_visible()
        for label in ("10", "50", "Все"):
            expect(page.get_by_role("group", name="Размер страницы сделок").get_by_role("button", name=label, exact=True)).to_be_visible()
        page.screenshot(path=str(shot_dir / "crm-r7-deals-pagination.png"), full_page=True)

        assert start_id
        assert not errors, errors
        browser.close()
