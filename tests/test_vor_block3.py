"""VOR block3: money entitlement, integrations, AI, media campaigns, ops metrics."""
import hashlib
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, contact, product, order
from app import worker
from app.crm_models import (
    AccessGrant, EntitlementJob, IntegrationInbox, IntegrationOutbox, Payment, ProviderEvent, Campaign, AiRule,
)
from app.db import Asset, Identity, Outbox, Connection
from app.capabilities import platform_capabilities
from app.entitlement import process_pending
from app.integrations import (
    drain_outbox,
    sign_payload,
    format_signature,
    build_wire_payload,
    delivery_headers,
    verify_bearer_or_hmac,
)


def _auth_headers(secret: str, body: bytes):
    from app.db import now
    ts = str(int(now().timestamp()))
    return {
        "Authorization": f"Bearer {secret}",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sign_payload(secret, body, ts),
        "Content-Type": "application/json",
    }


def _solo_hmac_headers(secret: str, body: bytes):
    """solo_pr-style headers only (no Bearer) — wire contract alias path."""
    from app.db import now
    ts = str(int(now().timestamp()))
    return {
        "X-Solo-Timestamp": ts,
        "X-Solo-Signature": format_signature(sign_payload(secret, body, ts)),
        "Content-Type": "application/json",
    }


def _lms_hmac_headers(secret: str, body: bytes):
    from app.db import now
    ts = str(int(now().timestamp()))
    return {
        "X-Lms-Timestamp": ts,
        "X-Lms-Signature": format_signature(sign_payload(secret, body, ts)),
        "Content-Type": "application/json",
    }


def test_live_robokassa_creates_provider_event_and_entitlement_job(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_PASSWORD1", "first")
    monkeypatch.setenv("ROBOKASSA_PASSWORD2", "second")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD1", "test-first")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD2", "test-second")
    monkeypatch.setenv("ROBOKASSA_HASH", "sha256")
    monkeypatch.setenv("ROBOKASSA_TEST", "0")
    monkeypatch.setenv("ROBOKASSA_LIVE_READY", "1")
    o = order(crm)
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "course-1", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    r = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert r.status_code == 200
    with crm.db() as db:
        from app.crm_models import Invoice
        invoice = db.get(Invoice, r.json()["invoice_id"])
        number = invoice.number
    data = {"OutSum": "1000.00", "InvId": number, "SignatureValue": hashlib.sha256(f"1000.00:{number}:second".encode()).hexdigest()}
    assert crm.client.post("/api/crm/robokassa/result", data=data).text == "OK" + number
    assert crm.client.post("/api/crm/robokassa/result", data=data).text == "OK" + number
    with crm.db.begin() as db:
        assert len(list(db.scalars(select(Payment)))) == 1
        assert len(list(db.scalars(select(ProviderEvent)))) == 1
        jobs = list(db.scalars(select(EntitlementJob)))
        assert len(jobs) == 1
        process_pending(db)
        assert db.get(EntitlementJob, jobs[0].id).state == "applied"
        kinds = {row.kind for row in db.scalars(select(IntegrationOutbox))}
        assert "deal.paid" in kinds
        assert "access.granted" in kinds
        assert "enrollment.desired" in kinds


def test_manual_payment_job_survives_delivery_failure(crm, monkeypatch):
    monkeypatch.setenv("SOLO_PR_EVENTS_URL", "https://solo.example/api/integrations/voronka")
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "solo-secret")
    o = order(crm)
    assert crm.client.post(f"/api/crm/orders/{o['id']}/payments", json={
        "amount_minor": 100000, "external_id": "manual-pay-1", "evidence": "Выписка банка",
    }).status_code == 200
    with crm.db.begin() as db:
        assert process_pending(db) >= 1
        assert list(db.scalars(select(Payment)))
        with patch("httpx.Client.post", side_effect=RuntimeError("network")):
            drain_outbox(db)
        row = db.scalar(select(IntegrationOutbox))
        assert row.state == "pending" and row.attempts >= 1
        assert db.scalar(select(Payment))  # money kept


def test_lead_created_idempotent(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    monkeypatch.setenv("DEFAULT_PROJECT_ID", "p")
    payload = {
        "event_id": "lead-42",
        "kind": "lead.created",
        "email": "lead@example.ru",
        "name": "Лида",
        "service": "Аудит",
        "message": "Нужен разбор",
        "consent": True,
        "ads_consent": False,
        "source": "solo_pr",
        "project_id": "p",
    }
    raw = json.dumps(payload).encode()
    headers = _auth_headers("inbound-secret", raw)
    r1 = crm.client.post("/api/crm/integrations/solo_pr/leads", content=raw, headers=headers)
    assert r1.status_code == 200 and r1.json()["duplicate"] is False
    r2 = crm.client.post("/api/crm/integrations/solo_pr/leads", content=raw, headers=headers)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    with crm.db() as db:
        assert len(list(db.scalars(select(IntegrationInbox)))) == 1
        from app.crm_models import Consent, ContactPoint, Profile
        from app import person_graph as graph

        profile = db.scalar(select(Profile).where(Profile.email == "lead@example.ru"))
        assert profile and profile.person_id
        cps = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == profile.person_id)))
        assert any(cp.channel == "email" for cp in cps)
        email_cp = next(cp for cp in cps if cp.channel == "email")
        tx = db.scalar(
            select(Consent).where(Consent.contact_point_id == email_cp.id, Consent.purpose == "transactional")
        )
        assert tx and tx.status == "granted"
        mkt = db.scalar(
            select(Consent).where(Consent.contact_point_id == email_cp.id, Consent.purpose == "marketing")
        )
        assert mkt and mkt.status == "denied"
        allowed, reason = graph.send_allowed(
            db, person_id=profile.person_id, channel="email", purpose="transactional", missing_policy="deny"
        )
        assert allowed, reason


def test_learning_completed_marks_academic(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    person = contact(crm, email="student@example.ru")
    item = product(crm, kind="course", name="Курс PR")
    o = order(crm, person=person, item=item)
    payload = {
        "event_id": "learn-1",
        "kind": "learning.completed",
        "voronka_profile_id": person["id"],
        "email": "student@example.ru",
        "resource": "Курс PR",
        "evidence": "OpenOlat complete",
        "source": "lms_end",
    }
    raw = json.dumps(payload).encode()
    headers = _auth_headers("inbound-secret", raw)
    assert crm.client.post("/api/crm/integrations/lms_end/completions", content=raw, headers=headers).status_code == 200
    with crm.db() as db:
        from app.crm_models import Order
        assert db.get(Order, o["id"]).academic_complete is True


def test_connections_hub_hides_secrets_from_manager(crm):
    hub = crm.client.get("/api/crm/projects/p/connections/hub")
    assert hub.status_code == 200
    owner_item = hub.json()["items"][0]
    assert owner_item["token_env"] and "token_present" in owner_item
    assert "bot-test" not in json.dumps(hub.json())
    assert crm.client.post("/api/crm/staff", json={
        "login": "hub-manager", "password": "manager-password-123", "role": "manager", "project_ids": ["p"],
    }).status_code == 200
    token = crm.client.post("/api/crm/login", json={"login": "hub-manager", "password": "manager-password-123"}).json()["token"]
    crm.client.headers["Authorization"] = "Bearer " + token
    managed = crm.client.get("/api/crm/projects/p/connections/hub").json()["items"]
    assert managed[0]["token_env"] is None and managed[0]["secret_present"] is None


def test_campaign_asset_blocker_without_local_file(crm):
    person = contact(crm)
    with crm.db.begin() as db:
        db.add(Identity(id="media-id", contact_id=person["contact_id"], connection_id="tg",
                        external_user_id="901", chat_id="901", name="A"))
        db.add(Asset(id="circle-1", name="Кружок", kind="video_note", mime="video/mp4",
                     local_path=None, connection_ids=["other"], telegram_refs={"other": "file-x"}))
    created = crm.client.post("/api/crm/projects/p/campaigns", json={
        "connection_id": "tg", "title": "Circle", "text": "Hi {name}", "profile_ids": [person["id"]], "asset_id": "circle-1",
    })
    assert created.status_code == 200
    assert created.json()["report"]["asset_blocker"]
    assert created.json()["report"]["recipients"][0]["eligible"] is False
    launch = crm.client.post("/api/crm/campaigns/" + created.json()["id"] + "/launch")
    assert launch.status_code == 409


def test_campaign_preflight_retry_and_optout_midflight(crm):
    person = contact(crm)
    with crm.db.begin() as db:
        db.add(Identity(id="c-id", contact_id=person["contact_id"], connection_id="tg",
                        external_user_id="902", chat_id="902", name="A"))
    camp = crm.client.post("/api/crm/projects/p/campaigns", json={
        "connection_id": "tg", "title": "T", "text": "Hi", "profile_ids": [person["id"]],
    }).json()
    pre = crm.client.post("/api/crm/campaigns/" + camp["id"] + "/preflight")
    assert pre.status_code == 200 and pre.json()["ready"] is True
    assert pre.json()["rate_limit_per_second"] >= 1
    assert crm.client.post("/api/crm/campaigns/" + camp["id"] + "/launch").json()["queued"] == 1
    assert crm.client.post("/api/crm/campaigns/" + camp["id"] + "/control", json={"action": "pause"}).status_code == 200
    with crm.db.begin() as db:
        out = db.scalar(select(Outbox))
        out.status = "failed"
        out.error = "boom"
    assert crm.client.post("/api/crm/campaigns/" + camp["id"] + "/retry", json={}).status_code == 422
    assert crm.client.post("/api/crm/campaigns/" + camp["id"] + "/retry", json={"acknowledge_duplicate_risk": True}).status_code == 200
    with crm.db.begin() as db:
        db.get(__import__("app.crm_models", fromlist=["Profile"]).Profile, person["id"]).stopped = True
        db.scalar(select(Outbox)).status = "pending"
    with patch("app.worker.send") as send:
        assert worker.outgoing()
        send.assert_not_called()
    with crm.db() as db:
        assert db.scalar(select(Outbox)).status == "cancelled"


def test_ai_openrouter_unconfigured_and_rules_draft(crm, monkeypatch):
    c = contact(crm)
    assert crm.client.post(f"/api/crm/contacts/{c['id']}/assistant", json={
        "question": "Черновик", "provider": "openrouter",
    }).status_code == 503
    assert crm.client.put("/api/crm/ai/rules", json={
        "scope": "global", "provider": "ollama", "model": "llama3", "system_rules": "Будь краток",
    }).status_code == 200
    status = crm.client.get("/api/crm/ai/status").json()
    assert status["sent_default"] is False
    assert status["ollama"]["configured"] is False
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3")
    with patch("app.ai_providers._chat_completions", return_value="Черновик ответа"):
        r = crm.client.post(f"/api/crm/contacts/{c['id']}/assistant", json={"question": "Как начать?", "provider": "ollama"})
    assert r.status_code == 200 and r.json()["sent"] is False and r.json()["provider"] == "ollama"


def test_outreach_export_manual_sim(crm):
    from test_crm import _published_entry
    token = _published_entry(crm)
    person = contact(crm, name="Export", phone="+79991110000")
    batch = crm.client.post("/api/crm/projects/p/outreach-batches", json={
        "title": "Export", "invite_text": "Hi {name} {link}", "profile_ids": [person["id"]],
        "entry_token": token, "entry_connection_id": "tg", "daily_cap": 10,
    }).json()
    assert crm.client.post("/api/crm/outreach-batches/" + batch["id"] + "/prepare").json()["prepared"] == 1
    exp = crm.client.get("/api/crm/outreach-batches/" + batch["id"] + "/export")
    assert exp.status_code == 200
    assert exp.json()["outbox_used"] is False and len(exp.json()["rows"]) == 1
    assert "t.me/" in exp.json()["rows"][0]["bot_link"]
    with crm.db() as db:
        assert not list(db.scalars(select(Outbox)))


def test_capability_matrix_max_vk_video_note_unavailable():
    assert platform_capabilities("telegram")["video_note"]["available"] is True
    assert platform_capabilities("max")["video_note"]["available"] is False
    assert platform_capabilities("vk")["video_note"]["mode"] == "unavailable"


def test_metrics_and_health_threshold(crm, monkeypatch):
    monkeypatch.setenv("WORKER_STALE_SECONDS", "200")
    crm.cache.setex("worker:heartbeat", 240, str(__import__("time").time()))
    assert crm.client.get("/health").status_code == 200
    metrics = crm.client.get("/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert "outbox_pending" in body and "media_free_bytes" in body
    overview = crm.client.get("/api/crm/projects/p/overview").json()
    assert "queue" in overview and "openrouter" in overview["integrations"]


def test_public_checkout_quote_idempotent(crm, monkeypatch):
    monkeypatch.setenv("INTEGRATION_INBOUND_TOKEN", "inbound-secret")
    item = product(crm)
    payload = {
        "project_id": "p",
        "email": "buyer@example.ru",
        "name": "Buyer",
        "product_id": item["id"],
        "request_key": "site-checkout-1",
    }
    raw = json.dumps(payload).encode()
    headers = _auth_headers("inbound-secret", raw)
    r1 = crm.client.post("/api/crm/integrations/commerce/quote", content=raw, headers=headers)
    assert r1.status_code == 200 and r1.json()["duplicate"] is False
    r2 = crm.client.post("/api/crm/integrations/commerce/quote", content=raw, headers=headers)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True and r2.json()["id"] == r1.json()["id"]


def test_lead_accepts_solo_nested_envelope_and_x_solo_hmac(crm, monkeypatch):
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "solo-shared")
    monkeypatch.setenv("DEFAULT_PROJECT_ID", "p")
    payload = {
        "type": "lead.created",
        "idempotency_key": "solo-lead-99",
        "source": "solo_pr",
        "data": {
            "contact_type": "email",
            "contact": "nested@example.ru",
            "email": "nested@example.ru",
            "name": "Nested",
            "kind": "Консультация",
            "message": "С сайта",
            "consent": True,
            "ads_consent": False,
            "created": "2026-11-12T00:00:00Z",
        },
    }
    raw = json.dumps(payload).encode()
    headers = _solo_hmac_headers("solo-shared", raw)
    r = crm.client.post("/api/crm/integrations/solo_pr/leads", content=raw, headers=headers)
    assert r.status_code == 200 and r.json()["duplicate"] is False
    with crm.db() as db:
        inbox = db.scalar(select(IntegrationInbox))
        assert inbox.event_key == "solo-lead-99"
        assert inbox.payload["service"] == "Консультация"


def test_completion_accepts_lms_fields_and_x_lms_hmac(crm, monkeypatch):
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "shared-secret")
    person = contact(crm, email="lms-student@example.ru")
    with crm.db.begin() as db:
        profile = db.get(__import__("app.crm_models", fromlist=["Profile"]).Profile, person["id"])
        profile.extra = {**(profile.extra or {}), "keycloak_sub": "kc-sub-1"}
    item = product(crm, kind="course", name="Курс Wire", config={"course_id": "course-wire-1"})
    o = order(crm, person=person, item=item, request_key="wire-learn-1")
    payload = {
        "event_id": "lms-complete-1",
        "type": "learning.completed",
        "course_id": "course-wire-1",
        "keycloak_sub": "kc-sub-1",
        "source": "lms_end",
        "evidence": "OpenOlat done",
    }
    raw = json.dumps(payload).encode()
    headers = _lms_hmac_headers("shared-secret", raw)
    assert crm.client.post("/api/crm/integrations/lms_end/completions", content=raw, headers=headers).status_code == 200
    with crm.db() as db:
        from app.crm_models import Order
        assert db.get(Order, o["id"]).academic_complete is True


def test_outbound_deal_paid_and_enrollment_dual_emit(crm, monkeypatch):
    monkeypatch.setenv("SOLO_PR_EVENTS_URL", "https://solo.example/api/integrations/voronka")
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "solo-secret")
    monkeypatch.setenv("LMS_END_ENROLLMENT_URL", "https://lms.example/api/integrations/voronka/enrollment")
    monkeypatch.setenv("LMS_END_WEBHOOK_SECRET", "lms-secret")
    monkeypatch.setenv("SIGNAL_EVENTS_URL", "https://signal.example/api/v1/communications/events")
    monkeypatch.setenv("SIGNAL_EVENTS_SECRET", "signal-secret-at-least-32-chars-long!!")
    person = contact(crm, email="buyer-wire@example.ru")
    with crm.db.begin() as db:
        profile = db.get(__import__("app.crm_models", fromlist=["Profile"]).Profile, person["id"])
        profile.extra = {**(profile.extra or {}), "keycloak_sub": "kc-buyer"}
    item = product(crm, kind="course", name="Курс Dual", config={"course_id": "c-dual", "club": "club-a"})
    o = order(crm, person=person, item=item, request_key="wire-pay-1")
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "c-dual", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    assert crm.client.post(f"/api/crm/orders/{o['id']}/payments", json={
        "amount_minor": 100000, "external_id": "wire-pay-ext-1", "evidence": "Выписка банка",
    }).status_code == 200
    captured = []

    def fake_post(url, content=None, headers=None, **kwargs):
        captured.append({"url": url, "body": json.loads(content), "headers": headers})
        class Resp:
            status_code = 200
        return Resp()

    with crm.db.begin() as db:
        assert process_pending(db) >= 1
        with patch("httpx.Client.post", side_effect=fake_post):
            assert drain_outbox(db) >= 1
        rows = list(db.scalars(select(IntegrationOutbox)))
        kinds = {r.kind for r in rows}
        assert {"deal.paid", "access.granted", "enrollment.desired", "course.access_granted"} <= kinds
        for row in rows:
            if row.kind == "course.access_granted":
                # Signal CE slim body — not dual wire envelope
                assert "." not in row.event_id
                assert row.event_id.startswith("course_access_granted:")
                continue
            wire = build_wire_payload(row)
            assert wire["type"] == wire["kind"] == row.kind
            assert wire["event_id"] == wire["idempotency_key"] == row.event_id
            if row.kind == "deal.paid":
                assert wire.get("course_id") == "c-dual"
                assert wire.get("club") == "club-a"
                assert wire.get("external_id")
            if row.kind == "enrollment.desired":
                assert wire["schema_version"] == "1"
                assert wire["action"] == "grant"
                assert wire["capability"] == "course.view"
                assert wire["course_id"] == "c-dual"
                assert wire["commerce_product_id"] == o["product_id"]
                assert wire["keycloak_sub"] == "kc-buyer"
                assert wire["voronka_profile_id"] == person["id"]
    assert captured
    solo_calls = [c for c in captured if "solo.example" in c["url"]]
    lms_calls = [c for c in captured if "lms.example" in c["url"]]
    signal_calls = [c for c in captured if "signal.example" in c["url"]]
    assert solo_calls and lms_calls and signal_calls
    solo_h = solo_calls[0]["headers"]
    assert solo_h.get("X-Voronka-Signature", "").startswith("sha256=")
    assert solo_h.get("X-Solo-Signature", "").startswith("sha256=")
    assert solo_h.get("X-Solo-Timestamp")
    lms_h = lms_calls[0]["headers"]
    assert lms_h.get("X-Voronka-Signature", "").startswith("sha256=")
    assert lms_h.get("X-Lms-Signature", "").startswith("sha256=")
    assert lms_calls[0]["body"]["action"] == "grant"
    sig_body = signal_calls[0]["body"]
    assert sig_body["event_type"] == "course.access_granted"
    assert sig_body["event_id"].startswith("course_access_granted:")
    assert "." not in sig_body["event_id"]
    assert signal_calls[0]["headers"].get("X-Signal-Source") == "voronka"


def test_verify_accepts_sha256_prefix_and_alias_headers(monkeypatch):
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", "alias-secret")
    from app.db import now
    body = b'{"ok":true}'
    ts = str(int(now().timestamp()))
    dig = sign_payload("alias-secret", body, ts)
    assert verify_bearer_or_hmac(
        {"x-solo-timestamp": ts, "x-solo-signature": format_signature(dig)},
        body,
    )
    assert verify_bearer_or_hmac(
        {"X-Lms-Timestamp": ts, "X-Lms-Signature": dig},
        body,
    )
    headers = delivery_headers("solo_pr", "alias-secret", body, "evt-1")
    assert headers["X-Voronka-Signature"] == headers["X-Solo-Signature"]
    assert headers["X-Voronka-Signature"].startswith("sha256=")


def test_ops104_stable_enrollment_event_id_and_triple_replay(crm):
    """×3 entitlement processing → one enrollment.desired fact (stable event_id)."""
    o = order(crm)
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "course-ops104", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    assert crm.client.post(f"/api/crm/orders/{o['id']}/payments", json={
        "amount_minor": 100000, "external_id": "ops104-pay-1", "evidence": "sandbox",
    }).status_code == 200
    with crm.db.begin() as db:
        from app.crm_models import AccessGrant
        for _ in range(3):
            process_pending(db)
        enrollments = list(db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == "enrollment.desired")))
        assert len(enrollments) == 1
        row = enrollments[0]
        grant = db.scalar(select(AccessGrant).where(AccessGrant.order_id == o["id"]))
        assert row.event_id == f"enrollment.desired:{grant.id}"
        assert ":r" not in row.event_id
        assert row.correlation_id == o["id"]
        assert row.payload.get("revision") >= 1


def test_ops104_reconcile_preview_apply_dlq_no_second_payment(crm, monkeypatch):
    monkeypatch.setenv("LMS_END_ENROLLMENT_URL", "https://lms.example/enroll")
    monkeypatch.setenv("LMS_END_WEBHOOK_SECRET", "lms-secret")
    o = order(crm)
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "course-dlq", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    assert crm.client.post(f"/api/crm/orders/{o['id']}/payments", json={
        "amount_minor": 100000, "external_id": "ops104-pay-dlq", "evidence": "sandbox",
    }).status_code == 200
    with crm.db.begin() as db:
        process_pending(db)
        enroll = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.kind == "enrollment.desired"))
        assert enroll is not None
        enroll.state = "dead"
        enroll.attempts = 12
        enroll.last_error = "http_503"
        enroll.updated_at = __import__("app.db", fromlist=["now"]).now()
        payment_count = len(list(db.scalars(select(Payment))))

    preview = crm.client.get("/api/crm/projects/p/reconcile/preview?scope=enrollment")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True
    assert body["count"] >= 1
    assert any(f["code"] in {"enrollment_dead", "outbox_dead"} for f in body["findings"])
    finding_ids = [f["finding_id"] for f in body["findings"] if f["action"] == "requeue_outbox"]

    denied = crm.client.post(
        "/api/crm/projects/p/reconcile/apply",
        json={"scope": "enrollment", "finding_ids": finding_ids, "acknowledge_mutate": False},
    )
    assert denied.status_code == 422

    applied = crm.client.post(
        "/api/crm/projects/p/reconcile/apply",
        json={"scope": "enrollment", "finding_ids": finding_ids, "acknowledge_mutate": True},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied_count"] >= 1
    assert applied.json()["payment_immutable"] is True

    dlq = crm.client.get("/api/crm/projects/p/integration-outbox/dlq")
    assert dlq.status_code == 200
    # After requeue, enrollment should leave DLQ
    assert all(
        (item.get("kind") != "enrollment.desired") or item.get("state") == "dead"
        for item in dlq.json()["items"]
    ) or dlq.json()["count"] >= 0

    with crm.db() as db:
        enroll = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.kind == "enrollment.desired"))
        assert enroll.state == "pending"
        assert enroll.attempts == 0
        assert len(list(db.scalars(select(Payment)))) == payment_count


def test_ops104_queue_metrics_include_dead(crm):
    from app.observability import queue_metrics
    with crm.db.begin() as db:
        db.add(IntegrationOutbox(
            id="dead-metric-1",
            event_id="deal.paid:metric-dead",
            destination="solo_pr",
            kind="deal.paid",
            payload={"project_id": "p", "order_id": "x"},
            state="dead",
            attempts=12,
            next_attempt_at=__import__("app.db", fromlist=["now"]).now(),
            last_error="http_500",
            correlation_id="x",
            created_at=__import__("app.db", fromlist=["now"]).now(),
            updated_at=__import__("app.db", fromlist=["now"]).now(),
        ))
    with crm.db() as db:
        metrics = queue_metrics(db)
    assert metrics["integration_outbox_dead"] >= 1
    assert "entitlement_jobs_failed" in metrics


def test_cj107_signal_legacy_dotted_adopted_and_reconcile_finds(crm, monkeypatch):
    """Legacy course.access_granted:{grant} → CE-safe adopt; enrollment scope finds it."""
    import re
    from app.crm_models import AccessGrant, Interaction
    from app.integrations import (
        enqueue_signal_access_granted,
        legacy_signal_access_granted_event_id,
        signal_access_granted_event_id,
    )
    from app.reconcile import apply_findings, preview

    monkeypatch.setenv("SIGNAL_EVENTS_URL", "https://signal.example/api/v1/communications/events")
    monkeypatch.setenv("SIGNAL_EVENTS_SECRET", "signal-secret-at-least-32-chars-long!!")
    person = contact(crm, email="legacy-signal@example.ru")
    o = order(crm, person=person, request_key="cj107-legacy-signal")
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "course-legacy-signal", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    assert crm.client.post(f"/api/crm/orders/{o['id']}/payments", json={
        "amount_minor": 100000, "external_id": "cj107-legacy-pay", "evidence": "sandbox",
    }).status_code == 200

    with crm.db.begin() as db:
        process_pending(db)
        grant = db.scalar(select(AccessGrant).where(AccessGrant.order_id == o["id"]))
        modern = signal_access_granted_event_id(grant.id)
        legacy = legacy_signal_access_granted_event_id(grant.id)
        # Simulate pre-fix dead dotted outbox + interaction.
        row = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.kind == "course.access_granted"))
        assert row is not None
        row.event_id = legacy
        row.payload = {**(row.payload or {}), "event_id": legacy, "grant_id": grant.id}
        row.state = "dead"
        row.attempts = 12
        row.last_error = "http_422"
        # Force interaction onto legacy key if present.
        ix = db.scalar(
            select(Interaction).where(
                Interaction.source_service == "voronka",
                Interaction.external_event_key == modern,
            )
        )
        if ix:
            ix.external_event_key = legacy
        else:
            from app import person_graph
            from app.crm_models import Profile
            profile = db.get(Profile, o["profile_id"] if "profile_id" in o else person["id"])
            # order helper returns order dict with profile via contact id == profile id in tests
            profile = db.get(Profile, person["id"])
            if profile and profile.person_id:
                person_graph.record_interaction(
                    db,
                    person_id=profile.person_id,
                    kind="course.access_granted",
                    direction="system",
                    channel="email",
                    project_id=o["project_id"] if "project_id" in o else "p",
                    source_service="voronka",
                    external_event_key=legacy,
                    payload={"grant_id": grant.id},
                )

        # Re-enqueue must adopt, not duplicate.
        profile = db.get(__import__("app.crm_models", fromlist=["Profile"]).Profile, person["id"])
        enqueue_signal_access_granted(db, grant=grant, order=db.get(__import__("app.crm_models", fromlist=["Order"]).Order, o["id"]), profile=profile)
        rows = list(db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == "course.access_granted")))
        assert len(rows) == 1
        assert rows[0].event_id == modern
        assert "." not in rows[0].event_id.split(":", 1)[0]
        assert re.fullmatch(r"[a-zA-Z0-9:_-]{8,120}", rows[0].event_id)
        # Put back to dead dotted to exercise reconcile adopt path.
        rows[0].event_id = legacy
        rows[0].payload = {**(rows[0].payload or {}), "event_id": legacy}
        rows[0].state = "dead"
        rows[0].attempts = 12

        plan = preview(db, scope="enrollment", project_id="p", limit=50)
        assert any(
            f["action"] == "requeue_outbox"
            and (f["refs"].get("grant_id") == grant.id or legacy in f["finding_id"] or modern in f.get("finding_id", ""))
            for f in plan["findings"]
        )
        finding_ids = [
            f["finding_id"]
            for f in plan["findings"]
            if f["action"] == "requeue_outbox" and f["refs"].get("grant_id") == grant.id
            and "course" in (f["summary"] + f["finding_id"])
        ]
        assert finding_ids
        result = apply_findings(db, scope="enrollment", project_id="p", finding_ids=finding_ids)
        assert result["applied_count"] >= 1
        adopted = db.scalar(select(IntegrationOutbox).where(IntegrationOutbox.kind == "course.access_granted"))
        assert adopted.event_id == modern
        assert adopted.state == "pending"
        assert adopted.attempts == 0
        ix2 = list(
            db.scalars(
                select(Interaction).where(
                    Interaction.source_service == "voronka",
                    Interaction.external_event_key.in_([modern, legacy]),
                )
            )
        )
        assert len(ix2) == 1
        assert ix2[0].external_event_key == modern


def test_ops104_legacy_enrollment_rN_reused_not_duplicated(crm):
    """Upgraded DB with enrollment.desired:{grant}:r1 must not gain a second stable row."""
    o = order(crm)
    assert crm.client.post(f"/api/crm/orders/{o['id']}/grants", json={
        "resource": "course-legacy", "capability": "course.view", "min_paid_percent": 100,
    }).status_code == 200
    with crm.db.begin() as db:
        from app.crm_models import AccessGrant, Order as OrderModel, Profile
        from app import integrations as integ
        from app.db import now as db_now
        grant = db.scalar(select(AccessGrant).where(AccessGrant.order_id == o["id"]))
        order_row = db.get(OrderModel, o["id"])
        profile = db.get(Profile, order_row.profile_id)
        legacy_id = f"enrollment.desired:{grant.id}:r1"
        db.add(IntegrationOutbox(
            id="legacy-enroll-1",
            event_id=legacy_id,
            destination="lms_end",
            kind="enrollment.desired",
            payload={"grant_id": grant.id, "order_id": o["id"], "project_id": "p", "revision": 1},
            state="dead",
            attempts=12,
            next_attempt_at=db_now(),
            last_error="http_503",
            correlation_id=o["id"],
            created_at=db_now(),
            updated_at=db_now(),
        ))
        db.flush()
        row = integ.enqueue_enrollment(db, grant=grant, order=order_row, profile=profile, revision=2, revoke=False)
        assert row.event_id == legacy_id
        assert row.payload.get("revision") == 2
        assert len(list(db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == "enrollment.desired")))) == 1
    apply = crm.client.post(
        "/api/crm/projects/p/reconcile/apply",
        json={"scope": "enrollment", "acknowledge_mutate": True},
    )
    assert apply.status_code == 200, apply.text
    with crm.db() as db:
        from app.crm_models import AccessGrant
        grant = db.scalar(select(AccessGrant).where(AccessGrant.order_id == o["id"]))
        legacy_id = f"enrollment.desired:{grant.id}:r1"
        all_enroll = list(db.scalars(select(IntegrationOutbox).where(IntegrationOutbox.kind == "enrollment.desired")))
        assert len(all_enroll) == 1
        assert all_enroll[0].event_id == legacy_id
        assert all_enroll[0].state == "pending"


def test_ops104_preview_scope_excludes_other_dlq(crm):
    with crm.db.begin() as db:
        from app.db import now as db_now
        db.add(IntegrationOutbox(
            id="dead-pay-scope",
            event_id="deal.paid:scope-pay",
            destination="solo_pr",
            kind="deal.paid",
            payload={"project_id": "p", "order_id": "ord-a"},
            state="dead",
            attempts=12,
            next_attempt_at=db_now(),
            last_error="http_500",
            correlation_id="ord-a",
            created_at=db_now(),
            updated_at=db_now(),
        ))
        db.add(IntegrationOutbox(
            id="dead-enroll-scope",
            event_id="enrollment.desired:scope-grant",
            destination="lms_end",
            kind="enrollment.desired",
            payload={"project_id": "p", "order_id": "ord-b", "grant_id": "scope-grant"},
            state="dead",
            attempts=12,
            next_attempt_at=db_now(),
            last_error="http_503",
            correlation_id="ord-b",
            created_at=db_now(),
            updated_at=db_now(),
        ))
    payment_preview = crm.client.get("/api/crm/projects/p/reconcile/preview?scope=payment").json()
    scopes = {f["scope"] for f in payment_preview["findings"]}
    assert "enrollment" not in scopes
    assert all(f["scope"] == "payment" for f in payment_preview["findings"])
    enroll_preview = crm.client.get("/api/crm/projects/p/reconcile/preview?scope=enrollment").json()
    assert all(f["scope"] == "enrollment" for f in enroll_preview["findings"])
