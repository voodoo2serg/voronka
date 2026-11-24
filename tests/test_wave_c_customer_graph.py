"""Wave C: CRM-001/005/002/003/006/007/008, PAY-002, AUTH-004."""
from __future__ import annotations

import secrets

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, contact, product, order  # noqa: F401
from app.crm_models import (
    Agreement,
    Communication,
    Consent,
    ContactPoint,
    DeliveryEvent,
    Interaction,
    Journey,
    JourneyStep,
    MergeReview,
    OidcLink,
    OrderLine,
    PaymentAllocation,
    Person,
    Receivable,
    Refund,
)
from app.integrations import format_signature, sign_payload
from app.db import now


def test_person_and_contact_points_on_create(crm):
    person = contact(crm, name="Ирина", email="irina@example.ru", phone="+79001234567")
    assert person.get("person_id")
    r = crm.client.get(f"/api/crm/projects/p/persons/{person['person_id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    channels = {cp["channel"] for cp in body["contact_points"]}
    assert "email" in channels and "phone" in channels
    assert body["person"]["display_name"] == "Ирина"


def test_merge_apply_and_undo(crm):
    a = contact(crm, name="A", email="a@example.ru")
    b = contact(crm, name="B", email="b@example.ru")
    q = crm.client.post(
        "/api/crm/projects/p/person-merges",
        json={"source_person_id": a["person_id"], "target_person_id": b["person_id"], "reason": "same buyer"},
    )
    assert q.status_code == 200, q.text
    mid = q.json()["id"]
    assert crm.client.post(f"/api/crm/person-merges/{mid}/apply").status_code == 200
    with crm.db() as db:
        src = db.get(Person, a["person_id"])
        assert src.merged_into_id == b["person_id"]
        assert src.lifecycle_stage == "merged"
    assert crm.client.post(f"/api/crm/person-merges/{mid}/undo").status_code == 200
    with crm.db() as db:
        src = db.get(Person, a["person_id"])
        assert src.merged_into_id is None
        assert db.get(MergeReview, mid).state == "undone"


def test_consent_blocks_available_identity(crm):
    from app.db import Identity
    from app.crm_service import available_identity
    from app.crm_models import Profile

    person = contact(crm, name="OptOut")
    with crm.db.begin() as db:
        profile = db.get(Profile, person["id"])
        db.add(
            Identity(
                id="id-opt",
                contact_id=profile.contact_id,
                connection_id="tg",
                external_user_id="7001",
                chat_id="7001",
                name="OptOut",
            )
        )
        db.flush()
        # sync telegram contact point
        from app import person_graph

        person_graph.sync_profile_contact_points(db, profile)
        cps = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == profile.person_id)))
        tg = next(cp for cp in cps if cp.channel == "telegram")
        person_graph.set_consent(db, tg.id, purpose="marketing", status="revoked", evidence="unsub")
        identity, reason = available_identity(db, profile, "tg")
        assert identity is None
        assert "согласия" in reason.lower() or "consent" in reason.lower()


def test_timeline_and_interactions_dedup(crm):
    person = contact(crm)
    pid = person["person_id"]
    r1 = crm.client.post(
        f"/api/crm/projects/p/persons/{pid}/interactions",
        json={
            "kind": "site.visit",
            "direction": "inbound",
            "channel": "web",
            "source_service": "solo_pr",
            "external_event_key": "evt-1",
            "payload": {"path": "/"},
        },
    )
    assert r1.status_code == 200 and r1.json()["duplicate"] is False
    r2 = crm.client.post(
        f"/api/crm/projects/p/persons/{pid}/interactions",
        json={
            "kind": "site.visit",
            "direction": "inbound",
            "channel": "web",
            "source_service": "solo_pr",
            "external_event_key": "evt-1",
        },
    )
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert r1.json()["id"] == r2.json()["id"]
    # nine more touches + timeline
    for i in range(2, 11):
        crm.client.post(
            f"/api/crm/projects/p/persons/{pid}/interactions",
            json={
                "kind": "touch",
                "external_event_key": f"evt-{i}",
                "source_service": "voronka",
                "payload": {"n": i},
            },
        )
    o = order(crm, person=person)
    crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={"amount_minor": 10000, "external_id": "pay-touch-1", "evidence": "частичная"},
    )
    timeline = crm.client.get(f"/api/crm/projects/p/persons/{pid}/timeline?limit=50")
    assert timeline.status_code == 200, timeline.text
    body = timeline.json()
    assert "total" in body and body["total"] >= 1
    kinds = {item["kind"] for item in body["items"]}
    assert "site.visit" in kinds or "touch" in kinds
    assert any(k.startswith("deal.paid") or k.startswith("payment") for k in kinds) or any(
        item.get("category") == "technical" for item in body["items"]
    )
    legacy = crm.client.get(f"/api/crm/projects/p/persons/{pid}/timeline?limit=50&legacy=1")
    assert legacy.status_code == 200
    legacy_types = {item["type"] for item in legacy.json()["items"]}
    assert "interaction" in legacy_types
    with crm.db() as db:
        assert db.scalar(select(Interaction).where(Interaction.external_event_key == "evt-1"))
        assert db.scalars(select(Interaction).where(Interaction.person_id == pid)).unique().all().__len__() >= 10


def test_journey_ten_steps_and_opt_out(crm):
    person = contact(crm, email="warm@example.ru")
    steps = [{"step_no": i, "condition": f"day_{i}", "notes": f"touch {i}"} for i in range(1, 11)]
    r = crm.client.post(
        "/api/crm/projects/p/journeys",
        json={"person_id": person["person_id"], "title": "Warmup", "steps": steps},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()["steps"]) == 10
    jid = r.json()["id"]
    with crm.db() as db:
        cp = db.scalar(
            select(ContactPoint).where(
                ContactPoint.person_id == person["person_id"], ContactPoint.channel == "email"
            )
        )
        cpid = cp.id
    crm.client.post(
        f"/api/crm/contact-points/{cpid}/consents",
        json={"purpose": "marketing", "status": "denied", "evidence": "stop"},
    )
    with crm.db() as db:
        assert db.get(Journey, jid).status == "opted_out"
        assert len(list(db.scalars(select(JourneyStep).where(JourneyStep.journey_id == jid)))) == 10


def test_agreement_not_implied_by_won_deal(crm):
    person = contact(crm)
    deal = crm.client.post(
        "/api/crm/projects/p/deals",
        json={"profile_id": person["id"], "title": "Аудит", "stage": "won"},
    ).json()
    assert deal["stage"] == "won"
    # no agreement auto-created
    assert crm.client.get("/api/crm/projects/p/agreements").json() == []
    ag = crm.client.post(
        "/api/crm/projects/p/agreements",
        json={"person_id": person["person_id"], "title": "Договор аудита", "deal_id": deal["id"]},
    )
    assert ag.status_code == 200
    assert ag.json()["legal_status"] == "draft"
    signed = crm.client.post(
        f"/api/crm/agreements/{ag.json()['id']}/sign",
        json={"evidence": "Подписано ЭДО №1"},
    )
    assert signed.status_code == 200 and signed.json()["legal_status"] == "signed"


def test_ledger_partial_overpay_refund(crm):
    o = order(crm)
    # partial
    assert (
        crm.client.post(
            f"/api/crm/orders/{o['id']}/payments",
            json={"amount_minor": 40000, "external_id": "ledger-1", "evidence": "часть"},
        ).json()["balance_minor"]
        == 60000
    )
    # overpay remainder+extra
    crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={"amount_minor": 70000, "external_id": "ledger-2", "evidence": "переплата"},
    )
    ledger = crm.client.get(f"/api/crm/orders/{o['id']}/ledger")
    assert ledger.status_code == 200, ledger.text
    summary = ledger.json()
    assert summary["due_minor"] == 100000
    assert summary["allocated_minor"] == 100000
    assert summary["overpayment_minor"] == 10000
    assert len(summary["lines"]) == 1
    # refund part of overpay+paid
    crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={"amount_minor": 15000, "external_id": "ledger-ref", "kind": "refund", "evidence": "возврат"},
    )
    summary2 = crm.client.get(f"/api/crm/orders/{o['id']}/ledger").json()
    assert summary2["refunded_minor"] == 15000
    with crm.db() as db:
        assert db.scalar(select(OrderLine).where(OrderLine.order_id == o["id"]))
        assert list(db.scalars(select(Receivable).where(Receivable.order_id == o["id"])))
        assert list(db.scalars(select(PaymentAllocation)))
        assert list(db.scalars(select(Refund)))


def test_oidc_link_not_by_email_alone(crm):
    person = contact(crm, email="stafflike@example.ru")
    # linking requires explicit ids — email_hint alone rejected
    bad = crm.client.post(
        "/api/crm/oidc-links",
        json={"issuer": "https://auth.example/realms/x", "subject": "sub-1", "email_hint": "stafflike@example.ru"},
    )
    assert bad.status_code == 422
    ok = crm.client.post(
        "/api/crm/oidc-links",
        json={
            "issuer": "https://auth.example/realms/x",
            "subject": "sub-1",
            "person_id": person["person_id"],
            "profile_id": person["id"],
            "email_hint": "stafflike@example.ru",
        },
    )
    assert ok.status_code == 200, ok.text
    conflict = crm.client.post(
        "/api/crm/oidc-links",
        json={
            "issuer": "https://auth.example/realms/x",
            "subject": "sub-1",
            "person_id": contact(crm, name="Other")["person_id"],
        },
    )
    assert conflict.status_code == 409
    got = crm.client.get(
        "/api/crm/oidc-links",
        params={"issuer": "https://auth.example/realms/x", "subject": "sub-1"},
    )
    assert got.status_code == 200 and got.json()["person_id"] == person["person_id"]
    with crm.db() as db:
        assert db.scalar(select(OidcLink).where(OidcLink.subject == "sub-1"))


def test_m2m_person_timeline_additive(crm, monkeypatch):
    secret = "signal-m2m-test-secret"
    monkeypatch.setenv("SIGNAL_M2M_SECRET", secret)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "p")
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,asset:read,person:read,timeline:read,connection:hub:read,contact:segment:read",
    )
    person = contact(crm)

    def headers(scopes: str):
        body = b""
        ts = str(int(now().timestamp()))
        return {
            "Content-Type": "application/json",
            "X-Signal-Timestamp": ts,
            "X-Signal-Signature": format_signature(sign_payload(secret, body, ts)),
            "X-Signal-Jti": secrets.token_urlsafe(12),
            "X-Signal-Service": "signal",
            "X-Signal-Scopes": scopes,
        }

    # existing hub still works (contract stable)
    hub = crm.client.request(
        "GET",
        "/api/crm/m2m/projects/p/connections/hub",
        content=b"",
        headers=headers("connection:hub:read"),
    )
    assert hub.status_code == 200, hub.text
    pr = crm.client.request(
        "GET",
        f"/api/crm/m2m/projects/p/persons/{person['person_id']}",
        content=b"",
        headers=headers("person:read"),
    )
    assert pr.status_code == 200, pr.text
    tl = crm.client.request(
        "GET",
        f"/api/crm/m2m/projects/p/persons/{person['person_id']}/timeline",
        content=b"",
        headers=headers("timeline:read"),
    )
    assert tl.status_code == 200, tl.text


def test_send_policy_check_blocks_marketing_after_revoke(crm):
    """CRM-102: send-time check denies marketing after opt-out; transactional still ok."""
    person = contact(crm, email="policy@example.ru")
    with crm.db() as db:
        cp = db.scalar(
            select(ContactPoint).where(
                ContactPoint.person_id == person["person_id"], ContactPoint.channel == "email"
            )
        )
        assert cp is not None
        cpid = cp.id
    crm.client.post(
        f"/api/crm/contact-points/{cpid}/consents",
        json={"purpose": "marketing", "status": "revoked", "evidence": "unsub-link"},
    )
    denied = crm.client.post(
        "/api/crm/projects/p/send-policy/check",
        json={
            "person_id": person["person_id"],
            "channel": "email",
            "purpose": "marketing",
            "missing_policy": "deny",
        },
    )
    assert denied.status_code == 200, denied.text
    assert denied.json()["allowed"] is False
    assert "consent" in denied.json()["reason"]
    tx = crm.client.post(
        "/api/crm/projects/p/send-policy/check",
        json={
            "contact_point_id": cpid,
            "channel": "email",
            "purpose": "transactional",
        },
    )
    assert tx.status_code == 200 and tx.json()["allowed"] is True


def test_won_deal_does_not_equal_signed_agreement_and_consent_model(crm):
    """CRM-003 + CRM-008 smoke: consent rows and agreement statuses are explicit."""
    person = contact(crm, email="c@example.ru")
    with crm.db() as db:
        cp = db.scalar(
            select(ContactPoint).where(
                ContactPoint.person_id == person["person_id"], ContactPoint.channel == "email"
            )
        )
        assert cp is not None
        cpid = cp.id
        assert not list(db.scalars(select(Consent).where(Consent.contact_point_id == cpid)))
    crm.client.post(
        f"/api/crm/contact-points/{cpid}/consents",
        json={"purpose": "transactional", "status": "granted", "evidence": "checkout"},
    )
    with crm.db() as db:
        row = db.scalar(select(Consent).where(Consent.contact_point_id == cpid))
        assert row.status == "granted" and row.purpose == "transactional"
    ag = crm.client.post(
        "/api/crm/projects/p/agreements",
        json={"person_id": person["person_id"], "title": "Оферта", "legal_status": "accepted"},
    ).json()
    assert ag["legal_status"] == "accepted"
    assert ag["signed_at"] is None


def test_m2m_communications_ingest_idempotent_and_no_html(crm, monkeypatch):
    """CJ-108: Signal CE ingest upserts Communication/DeliveryEvent/Interaction."""
    import json

    secret = "signal-m2m-test-secret"
    monkeypatch.setenv("SIGNAL_M2M_SECRET", secret)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "p")
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,person:read,timeline:read,timeline:write,connection:hub:read",
    )
    person = contact(crm, email="cj108@example.ru")

    def post(payload: dict, scopes: str = "timeline:write"):
        raw = json.dumps(payload).encode()
        ts = str(int(now().timestamp()))
        headers = {
            "Content-Type": "application/json",
            "X-Signal-Timestamp": ts,
            "X-Signal-Signature": format_signature(sign_payload(secret, raw, ts)),
            "X-Signal-Jti": secrets.token_urlsafe(12),
            "X-Signal-Service": "signal",
            "X-Signal-Scopes": scopes,
        }
        return crm.client.request(
            "POST",
            "/api/crm/m2m/projects/p/communications/ingest",
            content=raw,
            headers=headers,
        )

    html = post(
        {
            "email": "cj108@example.ru",
            "status": "queued",
            "external_event_key": "ce:voronka:evt-1:queued",
            "body_preview": "<p>secret body</p>",
            "kind": "course.access_granted",
        }
    )
    assert html.status_code == 422, html.text

    first = post(
        {
            "email": "cj108@example.ru",
            "status": "queued",
            "external_event_key": "ce:voronka:evt-1:queued",
            "body_preview": "Access granted preview",
            "kind": "course.access_granted",
            "purpose": "transactional",
        }
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["duplicate"] is False
    assert body["person_id"] == person["person_id"]
    cid = body["communication_id"]

    # ARCH-L4: unknown email must ensure thin Person (not 404 ingest_failed).
    ensured = post(
        {
            "email": "cj108-new@example.ru",
            "status": "queued",
            "external_event_key": "ce:voronka:evt-ensure:queued",
            "body_preview": "Welcome ensure",
            "kind": "identity.email_verified",
            "purpose": "transactional",
        }
    )
    assert ensured.status_code == 200, ensured.text
    ensured_body = ensured.json()
    assert ensured_body["duplicate"] is False
    assert ensured_body["person_id"]
    assert ensured_body["person_id"] != person["person_id"]
    with crm.db() as db:
        from app.crm_models import Person, Profile

        new_person = db.get(Person, ensured_body["person_id"])
        assert new_person is not None
        profiles = list(
            db.scalars(
                select(Profile).where(
                    Profile.person_id == ensured_body["person_id"],
                    Profile.project_id == "p",
                )
            )
        )
        assert len(profiles) == 1
        assert profiles[0].source == "signal_ce"
        assert profiles[0].email == "cj108-new@example.ru"

    replay = post(
        {
            "email": "cj108@example.ru",
            "status": "queued",
            "external_event_key": "ce:voronka:evt-1:queued",
            "body_preview": "Access granted preview",
            "kind": "course.access_granted",
        }
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["duplicate"] is True
    assert replay.json()["communication_id"] == cid

    accepted = post(
        {
            "email": "cj108@example.ru",
            "status": "accepted",
            "external_event_key": "ce:voronka:evt-1:accepted",
            "kind": "course.access_granted",
            "evidence_ref": "smtp-msgid-1",
        }
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["communication_id"] == cid
    assert accepted.json()["duplicate"] is False

    with crm.db() as db:
        comm = db.get(Communication, cid)
        assert comm is not None and comm.status == "accepted"
        assert "<" not in (comm.body_preview or "")
        events = list(
            db.scalars(select(DeliveryEvent).where(DeliveryEvent.communication_id == cid))
        )
        assert {e.normalized_status for e in events} >= {"queued", "accepted"}
        ixs = list(
            db.scalars(
                select(Interaction).where(
                    Interaction.person_id == person["person_id"],
                    Interaction.source_service == "signal",
                )
            )
        )
        assert len(ixs) == 2

    # Contact drawer (crm.solo-pr.ru) must surface Person writeback without a full graph rewrite.
    detail = crm.client.get(f"/api/crm/contacts/{person['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body.get("person_id") == person["person_id"]
    keys = {ix["external_event_key"] for ix in body.get("person_interactions") or []}
    assert "ce:voronka:evt-1:queued" in keys
    assert "ce:voronka:evt-1:accepted" in keys
    assert any(c["id"] == cid for c in body.get("person_communications") or [])
    assert all(ix.get("project_id") == "p" for ix in body["person_interactions"])
    assert all(c.get("project_id") == "p" for c in body["person_communications"])
    # Communications are those referenced by returned interactions (aligned sets).
    linked = {ix.get("communication_id") for ix in body["person_interactions"] if ix.get("communication_id")}
    assert linked == {c["id"] for c in body["person_communications"]}

    # Cross-project deny via allowlist
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "other")
    denied = post(
        {
            "person_id": person["person_id"],
            "status": "queued",
            "external_event_key": "ce:voronka:evt-2:queued",
        }
    )
    assert denied.status_code == 403, denied.text


def test_contact_detail_person_history_scoped_to_project(crm):
    """CJ-108: contact drawer must not leak other-project Person interactions/comms."""
    from app import person_graph as graph
    from app.crm_models import Profile
    from app.crm_service import uid
    from app.db import Contact

    person = contact(crm, email="cj108-scope@example.ru")
    person_id = person["person_id"]

    with crm.db.begin() as db:
        # Same Person linked to a Profile in project "secret".
        secret_contact = Contact(id=uid(), person_id=person_id)
        db.add(secret_contact)
        db.flush()
        db.add(
            Profile(
                id=uid(),
                project_id="secret",
                contact_id=secret_contact.id,
                person_id=person_id,
                name="Secret twin",
                email="cj108-scope@example.ru",
                revision=1,
            )
        )
        other_comm = graph.create_communication(
            db,
            person_id=person_id,
            purpose="transactional",
            channel="email",
            project_id="secret",
            body_preview="SECRET other-project preview",
            status="accepted",
        )
        graph.record_interaction(
            db,
            person_id=person_id,
            kind="course.access_granted",
            direction="outbound",
            channel="email",
            project_id="secret",
            communication_id=other_comm.id,
            source_service="signal",
            external_event_key="ce:secret:leak:accepted",
            payload={"status": "accepted"},
        )
        # Many project-p communications; an older linked one must still resolve for preview.
        linked_comm = None
        for i in range(25):
            comm = graph.create_communication(
                db,
                person_id=person_id,
                purpose="transactional",
                channel="email",
                project_id="p",
                body_preview=f"p-preview-{i}",
                status="accepted",
            )
            if i == 0:
                linked_comm = comm
        assert linked_comm is not None
        linked_comm_id = linked_comm.id
        graph.record_interaction(
            db,
            person_id=person_id,
            kind="course.access_granted",
            direction="outbound",
            channel="email",
            project_id="p",
            communication_id=linked_comm_id,
            source_service="signal",
            external_event_key="ce:p:old-linked:accepted",
            payload={"status": "accepted"},
        )

    detail = crm.client.get(f"/api/crm/contacts/{person['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    keys = {ix["external_event_key"] for ix in body.get("person_interactions") or []}
    assert "ce:secret:leak:accepted" not in keys
    assert "ce:p:old-linked:accepted" in keys
    assert all(ix.get("project_id") == "p" for ix in body["person_interactions"])
    assert all(c.get("project_id") == "p" for c in body["person_communications"])
    assert not any("SECRET" in (c.get("body_preview") or "") for c in body["person_communications"])
    # Referenced (older) communication is included even when >20 newer unlinked comms exist.
    assert any(c["id"] == linked_comm_id and c["body_preview"] == "p-preview-0" for c in body["person_communications"])
    linked = {ix.get("communication_id") for ix in body["person_interactions"] if ix.get("communication_id")}
    assert linked <= {c["id"] for c in body["person_communications"]}
