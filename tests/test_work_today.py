"""FUNC-L1/L3: CRM work-today contour + Signal deep-links (no UUID paste)."""
from pathlib import Path

from test_operational import system  # noqa: F401
from test_crm import crm, contact, product, order  # noqa: F401
from app import portals
from app.crm_models import Communication, Interaction, Profile
from app.crm_service import uid


ROOT = Path(__file__).resolve().parents[1]


def test_signal_href_embeds_person_without_form_paste():
    url = portals.signal_href(view="sets", person="person-abc")
    assert "view=sets" in url
    assert "person=person-abc" in url
    assert url.startswith(portals.signal_public_base())
    ce = portals.signal_href(view="campaigns", person="person-abc")
    assert "view=campaigns" in ce
    assert "person=person-abc" in ce


def test_work_today_contour_stages_and_links(crm):
    person = contact(crm, name="Контур Анна")
    item = product(crm)
    deal = crm.client.post(
        "/api/crm/projects/p/deals",
        json={"profile_id": person["id"], "title": "Курс", "stage": "qualified", "next_action": "Созвон"},
    ).json()
    assert deal["id"]

    assert (
        crm.client.post(
            "/api/crm/contacts/" + person["id"] + "/activities",
            json={"body": "Созвон", "outcome": "Интерес", "channel": "phone"},
        ).status_code
        == 200
    )

    o = order(crm, person, item, request_key="work-today-order-1")
    base = "/api/crm/orders/" + o["id"]
    assert (
        crm.client.post(
            base + "/grants",
            json={"resource": "course-demo", "capability": "course.view", "min_paid_percent": 100},
        ).status_code
        == 200
    )
    pay = crm.client.post(
        base + "/payments",
        json={
            "amount_minor": o["total_minor"],
            "external_id": "work-today-pay-001",
            "evidence": "Sandbox выписка",
        },
    )
    assert pay.status_code == 200, pay.text
    assert crm.client.post(base + "/academic", json={"complete": True, "evidence": "Модуль сдан"}).status_code == 200

    detail = crm.client.get("/api/crm/contacts/" + person["id"]).json()
    profile_person = detail.get("person_id")
    assert profile_person, "deal create should ensure Person on profile"

    with crm.db.begin() as db:
        # Keep Person on profile (deal path already linked); add Signal mail writeback.
        row = db.get(Profile, person["id"])
        assert row and row.person_id == profile_person
        from app.crm_models import Order

        ord_row = db.get(Order, o["id"])
        if ord_row and not ord_row.person_id:
            ord_row.person_id = profile_person
        comm = Communication(
            id=uid(),
            person_id=profile_person,
            project_id="p",
            direction="outbound",
            purpose="transactional",
            channel="email",
            status="delivered",
            body_preview="Доступ к курсу",
        )
        db.add(comm)
        db.flush()
        db.add(
            Interaction(
                id=uid(),
                person_id=profile_person,
                project_id="p",
                communication_id=comm.id,
                kind="email.sent",
                direction="outbound",
                channel="email",
                source_service="signal",
                external_event_key="wt-mail-1",
                payload={"status": "delivered"},
            )
        )

    r = crm.client.get("/api/crm/projects/p/work-today")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["contour"] == ["lead", "touch", "pay", "access", "course", "mail"]
    assert body["honesty"]["can_enable_live_from_ui"] is False
    assert "Q6" in body["honesty"]["policy"] or "sandbox" in body["honesty"]["policy"].lower()
    assert body["links"]["signal_sets"]
    assert "view=sets" in body["links"]["signal_sets"]
    assert body["secrets_exposed"] is False

    row = next((x for x in body["items"] if x["profile_id"] == person["id"]), None)
    assert row, body["items"]
    assert row["statuses"]["lead"]["done"] is True
    assert row["statuses"]["touch"]["done"] is True
    assert row["statuses"]["pay"]["done"] is True
    assert row["statuses"]["access"]["done"] is True
    assert row["statuses"]["course"]["done"] is True
    assert row["statuses"]["mail"]["done"] is True
    assert row["person_id"]
    assert row["links"]["signal_ce"] and "person=" in row["links"]["signal_ce"]
    assert row["links"]["signal_sets"] and "view=sets" in row["links"]["signal_sets"]
    assert row["links"]["lms"]
    assert row["links"]["lms"].endswith("/open")
    assert body["links"]["lms"].endswith("/open")


def test_work_today_paint_and_signal_actions():
    ws = (ROOT / "web" / "app" / "crm" / "Workspace.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "web" / "app" / "crm" / "sections" / "WorkTodayPanel.tsx").read_text(encoding="utf-8")
    actions = (ROOT / "web" / "app" / "crm" / "sections" / "SignalPersonActions.tsx").read_text(encoding="utf-8")
    portals_ts = (ROOT / "web" / "app" / "crm" / "portals.ts").read_text(encoding="utf-8")
    ctor = (ROOT / "web" / "app" / "crm" / "sections" / "ActionConstructorSection.tsx").read_text(
        encoding="utf-8"
    )
    assert "WorkTodayPanel" in ws  # secondary on overview; not #today
    assert "SignalPersonActions" in ws
    assert "work-today-contour" in panel
    assert "lead" in panel and "mail" in panel
    assert "SIGNAL CE" in panel or "SIGNAL CE" in actions
    assert "signalHref" in portals_ts
    assert "person" in portals_ts
    assert "копир" in actions
    # CART-01: #today / goToday → constructor, not overview contour scroll
    assert "function goToday()" in ws
    assert "setSection('chains')" in ws
    assert "action-constructor" in ctor
    assert "getElementById('work-today-contour')" not in ws
    assert "getElementById('action-constructor')" in ws
    assert "ROBOKASSA_LIVE_READY" in panel
    assert "Открыть курс" in panel
    assert "lmsOpenCourseHref" in portals_ts or "/open" in portals_ts
    assert "Из медиатеки" in ctor
    assert "Медиатека CRM" in ctor
    assert "Открыть в Signal" in ctor
    assert "onOpenMediaLibrary" in ctor
    assert "Набрать корзину в CMS" not in ctor
    assert "Корзина CMS" not in ctor
    assert "addNodeKind" in ctor
    assert "Настроить в Подключениях" in ctor
    assert "smtp_password" not in ctor
    assert "bot_token" not in ctor
