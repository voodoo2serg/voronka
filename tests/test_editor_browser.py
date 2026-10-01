"""Real Chromium editor smoke test with mocked platform/API responses."""
import os, json
from pathlib import Path
from urllib.parse import urlparse
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_BROWSER") != "1", reason="Browser job only")

@pytest.mark.parametrize("width", [1180, 390])
def test_build_five_lesson_funnel_without_json(width):
    from playwright.sync_api import sync_playwright
    templates = json.loads(Path("app/templates.json").read_text())
    assets = [{"id": "video"+str(i), "name": "Видео "+str(i), "kind": "video", "metadata": {},
               "connection_ids": ["tg"], "preview_available": False} for i in range(1,6)]
    assets.append({"id":"book", "name":"Книга", "kind":"document", "metadata":{},
                   "connection_ids":["tg"], "preview_available":False})
    state = {"funnels": [], "saved": None, "published": False}
    errors = []
    def handle(route):
        path = urlparse(route.request.url).path
        if path == "/js/telegram-web-app.js":
            route.fulfill(content_type="application/javascript", body='window.Telegram={WebApp:{initData:"test",initDataUnsafe:{user:{id:123}},ready(){}}};')
            return
        if path == "/":
            route.fulfill(content_type="text/html", body=Path("app/admin.html").read_text())
            return
        if path == "/editor.js":
            route.fulfill(content_type="application/javascript", body=Path("app/editor.js").read_text())
            return
        data = []
        if path == "/auth": data = {"token":"token","role":"owner"}
        elif path == "/api/connections": data = [{"id":"tg","name":"Мой бот","platform":"telegram"}]
        elif path == "/api/templates": data = templates
        elif path == "/api/assets": data = assets
        elif path == "/api/funnels" and route.request.method == "POST":
            body = route.request.post_data_json
            from app.graph import validate_graph
            validate_graph(body["graph"])
            state["saved"] = body
            state["funnels"] = [{"id":"f1","name":body["name"],"graph":body["graph"],
                                 "connection_ids":body["connection_ids"],"revision":1,"version_id":None}]
            data = {"id":"f1","revision":1}
        elif path == "/api/funnels": data = state["funnels"]
        elif path.endswith("/draft"): data = {"revision":2}
        elif path.endswith("/publish"):
            state["published"] = True
            data = {"version_id":"v1"}
        route.fulfill(content_type="application/json", body=json.dumps(data))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width":width,"height":900})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        page.route("**/*", handle)
        page.goto("https://voronka.test/")
        page.get_by_role("button", name="Новая воронка", exact=True).click()
        page.get_by_label("Название", exact=True).first.fill("Боевой мини-курс")
        page.get_by_role("button", name="Применить шаблон", exact=True).click()
        for i in range(1,6):
            page.get_by_role("button", name="Урок "+str(i), exact=True).click()
            page.get_by_label("Материал из медиабанка", exact=True).select_option("video"+str(i))
        page.get_by_role("button", name="Книга", exact=True).click()
        page.get_by_label("Материал из медиабанка", exact=True).select_option("book")
        page.get_by_role("button", name="Опубликовать", exact=True).click()
        page.wait_for_function("document.getElementById('status').textContent.includes('Воронка опубликована')")
        assert state["published"] and state["saved"]["name"] == "Боевой мини-курс"
        assert len([n for n in state["saved"]["graph"]["nodes"].values() if n["type"] == "video"]) == 5
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
        browser.close()
