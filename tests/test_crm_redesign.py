"""CRM redesign: Project/Branch/Старт, home board, ActionChain, payments vault, A15 media."""
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.crm_models import ActionChain, Branch, ProjectCourse, StaffAlertRule
from app.db import Project
from app.projects_domain import START_SYSTEM_KEY, chain_to_funnel_graph, validate_action_nodes
from app.graph import validate_graph
from test_crm import contact, product, order

ROOT = Path(__file__).resolve().parents[1]

# crm fixture needs system from test_operational
pytest_plugins = ("test_operational", "test_crm")


def test_start_project_seeded_on_me(crm):
    me = crm.client.get("/api/crm/me").json()
    assert me["start_project_id"]
    with crm.db() as db:
        start = db.scalar(select(Project).where(Project.system_key == START_SYSTEM_KEY))
        assert start is not None
        assert start.name == "Старт"
        assert bool(start.is_system) is True
        assert start.id == me["start_project_id"]


def test_home_projects_board_metrics(crm):
    c = contact(crm)
    item = product(crm)
    o = order(crm, c, item, request_key="board-pay-rk")
    pay = crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={"external_id": "board-pay-01", "amount_minor": 50000, "evidence": "Выписка теста"},
    )
    assert pay.status_code == 200, pay.text
    board = crm.client.get("/api/crm/home/projects").json()
    assert board["start_project_id"]
    ids = {x["id"] for x in board["items"]}
    assert "p" in ids
    row = next(x for x in board["items"] if x["id"] == "p")
    assert row["contacts_count"] >= 1
    assert row["received_minor"] >= 50000
    assert "next_tasks" in row


def test_branch_and_course_m2m(crm):
    br = crm.client.post(
        "/api/crm/projects/p/branches",
        json={"name": "Ежемесячный мит", "description": "ветка"},
    ).json()
    assert br["name"] == "Ежемесячный мит"
    bind = crm.client.post(
        "/api/crm/projects/p/courses",
        json={"course_id": "olat-course-1", "label": "Курс", "branch_id": br["id"]},
    ).json()
    assert bind["course_id"] == "olat-course-1"
    courses = crm.client.get("/api/crm/projects/p/courses").json()
    assert any(x["course_id"] == "olat-course-1" for x in courses["items"])
    c = contact(crm, name="Ветка")
    placed = crm.client.patch(
        f"/api/crm/contacts/{c['id']}/placement",
        json={"branch_id": br["id"], "person_role": "client"},
    ).json()
    assert placed["branch_id"] == br["id"]
    assert placed["person_role"] == "client"
    page = crm.client.get("/api/crm/projects/p/contacts?limit=10&branch_id=" + br["id"]).json()
    assert page["total"] >= 1
    assert page["limit"] == 10


def test_contacts_page_size_all(crm):
    contact(crm, name="A")
    contact(crm, name="B")
    all_page = crm.client.get("/api/crm/projects/p/contacts?limit=0").json()
    assert all_page["total"] >= 2
    assert all_page["limit"] == 5000


def test_action_chain_save_publish_dry_run(crm, monkeypatch, tmp_path):
    monkeypatch.setenv("SMTP_HOST", "")
    meta = crm.client.get("/api/crm/constructor/meta").json()
    assert meta["media_library_url"].endswith("view=media") or "view=media" in meta["media_library_url"]
    assert meta["client_receives_label"] == "Клиент получает"
    created = crm.client.post(
        "/api/crm/projects/p/action-chains",
        json={
            "name": "Онбординг",
            "nodes": [
                {"id": "n1", "kind": "email", "title": "Письмо", "config": {"body": "Привет"}, "from_address": "serg@vodopetov.ru"},
                {"id": "n2", "kind": "payment", "title": "Оплата", "config": {"provider": "robokassa", "tariff_mode": "fixed"}},
                {"id": "n3", "kind": "finish", "title": "Финиш", "config": {}},
            ],
        },
    ).json()
    assert created["state"] == "draft"
    dry = crm.client.post(
        f"/api/crm/action-chains/{created['id']}/dry-run",
        json={"email": "tester@example.ru", "telegram": ""},
    ).json()
    assert dry["report"]["steps"]
    pub = crm.client.post(f"/api/crm/action-chains/{created['id']}/publish").json()
    assert pub["state"] == "published"
    assert pub["funnel_id"]
    with crm.db() as db:
        row = db.get(ActionChain, created["id"])
        assert row is not None
        assert row.funnel_id


def test_chain_to_funnel_graph_validates():
    nodes = validate_action_nodes(
        [
            {"kind": "contact", "title": "Касание", "config": {"body": "Здравствуйте"}},
            {"kind": "survey", "title": "Опрос", "config": {"question": "Готовы?", "options": ["Да", "Нет"]}},
            {"kind": "finish", "title": "Готово", "config": {}},
        ]
    )
    graph = chain_to_funnel_graph(nodes)
    validate_graph(graph)


def test_payment_vault_and_alert_rules(crm, monkeypatch, tmp_path):
    path = tmp_path / "pay.env"
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(path))
    from app import payment_vault

    payment_vault.apply_updates(
        {
            "ROBOKASSA_LOGIN": "shop",
            "ROBOKASSA_TEST": "1",
            "ROBOKASSA_TEST_PASSWORD1": "a" * 12,
            "ROBOKASSA_TEST_PASSWORD2": "b" * 12,
            "YOOKASSA_SHOP_ID": "yk-shop",
            "YOOKASSA_SECRET_KEY": "yk-secret",
        }
    )
    status = crm.client.get("/api/crm/payment-providers/credentials").json()
    assert status["robokassa"]["fields"]["login"] is True
    assert status["robokassa"]["status"]["secrets_exposed"] is False
    assert status["robokassa"]["secrets_exposed"] is False
    saved = crm.client.put(
        "/api/crm/payment-providers/credentials",
        json={"provider": "yookassa", "fields": {"shop_id": "yk2", "secret_key": "sec2"}},
    ).json()
    assert saved["ok"] is True
    assert "sec2" not in str(saved)
    rule = crm.client.post(
        "/api/crm/staff-alert-rules",
        json={"kind": "paid", "channel": "telegram", "amount_minor": 0},
    ).json()
    assert rule["kind"] == "paid"
    ping = crm.client.post("/api/crm/payment-providers/robokassa/test-ping").json()
    assert ping["ok"] is True


def test_nav_and_media_constants_in_frontend():
    constants = (ROOT / "web" / "app" / "crm" / "constants.ts").read_text(encoding="utf-8")
    assert "['home', 'Проекты']" in constants
    assert "['chains', 'Конструктор']" in constants
    assert "Прогрев" not in constants or "['outreach'" not in constants
    portals = (ROOT / "web" / "app" / "crm" / "portals.ts").read_text(encoding="utf-8")
    assert "SIGNAL_MEDIA_LIBRARY_URL" in portals
    assert "/?view=media" in portals
    ctor = (ROOT / "web" / "app" / "crm" / "sections" / "ActionConstructorSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "Создать файл в Signal" in ctor
    assert "Набрать корзину в CMS" in ctor
    assert "Клиент получает" in ctor
    assert "SIGNAL_MEDIA_LIBRARY_URL" in ctor
    assert "cmsChainCartHref" in ctor or "/#/admin/cart" in ctor
    assert "Настроить в Подключениях" in ctor
    # CART-04: «+» from CMS cart items; no paste asset_id
    assert "constructor/cart" in ctor
    assert "addFromCartItem" in ctor
    assert "вставьте asset_id" not in ctor.lower()
