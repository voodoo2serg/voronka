"""VR-452 Person Timeline: filters, projection, staff note, LMS stub."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import select

from app.crm_models import ContactPoint
from test_crm import contact, product, order

pytest_plugins = ("test_operational", "test_crm")


def test_timeline_filters_correspondence_vs_technical(crm):
    person = contact(crm, email="tl-filter@example.ru", name="История")
    pid = person["person_id"]

    crm.client.post(
        f"/api/crm/projects/p/persons/{pid}/interactions",
        json={
            "kind": "message.in",
            "direction": "inbound",
            "channel": "telegram",
            "external_event_key": "tl-msg-1",
            "payload": {"text": "привет"},
        },
    )
    o = order(crm, person=person, item=product(crm), request_key="tl-pay-rk")
    pay = crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={"amount_minor": 15000, "external_id": "tl-pay-1", "evidence": "sandbox тест"},
    )
    assert pay.status_code == 200, pay.text

    # Staff note via activity
    assert (
        crm.client.post(
            f"/api/crm/contacts/{person['id']}/activities",
            json={"body": "Созвон по mentorship", "outcome": "Договорились", "channel": "phone"},
        ).status_code
        == 200
    )

    all_rows = crm.client.get(f"/api/crm/projects/p/persons/{pid}/timeline?category=all&limit=50")
    assert all_rows.status_code == 200, all_rows.text
    body = all_rows.json()
    assert body["total"] >= 2
    assert all("category" in i and "title" in i for i in body["items"])

    corr = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=correspondence&limit=50"
    ).json()
    tech = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=technical&limit=50"
    ).json()
    assert all(i["category"] == "correspondence" for i in corr["items"])
    assert all(i["category"] == "technical" for i in tech["items"])
    assert corr["total"] >= 1
    assert tech["total"] >= 1
    assert any(i["kind"] in {"message.in", "staff.note"} or "message" in i["kind"] for i in corr["items"])
    assert any(
        i["kind"].startswith("deal.paid") or i["kind"].startswith("payment") or i["kind"] == "access.granted"
        for i in tech["items"]
    )


def test_consent_and_timeline_note_and_lms_stub(crm):
    person = contact(crm, email="tl-consent@example.ru")
    pid = person["person_id"]
    with crm.db() as db:
        cp = db.scalar(
            select(ContactPoint).where(ContactPoint.person_id == pid, ContactPoint.channel == "email")
        )
        assert cp is not None
        cpid = cp.id

    r = crm.client.post(
        f"/api/crm/contact-points/{cpid}/consents",
        json={"purpose": "marketing", "status": "granted", "evidence": "form"},
    )
    assert r.status_code == 200, r.text

    note = crm.client.post(
        f"/api/crm/projects/p/persons/{pid}/timeline/notes",
        json={"summary": "Клиент ждёт программу", "title": "Заметка"},
    )
    assert note.status_code == 200, note.text
    assert note.json()["category"] == "correspondence"

    lms = crm.client.post(
        f"/api/crm/projects/p/lms/timeline-events",
        json={
            "person_id": pid,
            "kind": "module.open",
            "course_id": "olat-1",
            "module_id": "m-2",
            "summary": "Модуль 2",
        },
    )
    assert lms.status_code == 200, lms.text
    assert lms.json()["kind"] == "module.open"
    assert lms.json()["gap"]

    tech = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=technical&limit=50"
    ).json()
    kinds = {i["kind"] for i in tech["items"]}
    assert "consent.changed" in kinds
    assert "module.open" in kinds

    corr = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=correspondence&limit=50"
    ).json()
    assert any(i["kind"] == "staff.note" for i in corr["items"])


def test_timeline_pagination(crm):
    person = contact(crm, email="tl-page@example.ru")
    pid = person["person_id"]
    for i in range(25):
        crm.client.post(
            f"/api/crm/projects/p/persons/{pid}/interactions",
            json={
                "kind": "touch",
                "channel": "web",
                "external_event_key": f"tl-page-{i}",
                "payload": {"n": i},
            },
        )
    page1 = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=all&offset=0&limit=10"
    ).json()
    page2 = crm.client.get(
        f"/api/crm/projects/p/persons/{pid}/timeline?category=all&offset=10&limit=10"
    ).json()
    assert page1["total"] >= 25
    assert len(page1["items"]) == 10
    assert len(page2["items"]) == 10
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not ids1 & ids2


def test_channel_invite_projects_to_timeline(crm):
    """Invite Event → Person timeline correspondence row (projection)."""
    from app.db import Event
    from app.crm_service import uid
    from app.db import now as db_now

    person = contact(crm, email="tl-invite@example.ru")
    with crm.db.begin() as db:
        from app.crm_models import Profile

        profile = db.get(Profile, person["id"])
        assert profile is not None
        ev = Event(
            id=uid(),
            kind="lead.channel_invite.pending",
            contact_id=profile.contact_id,
            payload={"lead_event_id": "lead-tl-1", "status": "pending"},
            created_at=db_now(),
        )
        db.add(ev)

    rows = crm.client.get(
        f"/api/crm/projects/p/persons/{person['person_id']}/timeline?category=correspondence&limit=50"
    ).json()
    assert any(
        i["kind"] == "lead.channel_invite.pending" or "channel_invite" in i["kind"] for i in rows["items"]
    ), rows
