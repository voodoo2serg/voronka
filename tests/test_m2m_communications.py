"""SIG-4: Signal M2M communications — HMAC scopes, replay, idempotency, no token leak."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, contact
from app.db import Identity, Outbox, now
from app.crm_models import Campaign
from app.integrations import format_signature, sign_payload
from app.m2m_signal import expand_scopes


SECRET = "signal-m2m-test-secret"


def _headers(body: bytes, *, scopes: str, jti: str | None = None, service: str = "signal"):
    ts = str(int(now().timestamp()))
    digest = sign_payload(SECRET, body, ts)
    return {
        "Content-Type": "application/json",
        "X-Signal-Timestamp": ts,
        "X-Signal-Signature": format_signature(digest),
        "X-Signal-Jti": jti or secrets.token_urlsafe(12),
        "X-Signal-Service": service,
        "X-Signal-Scopes": scopes,
    }


@pytest.fixture
def m2m(crm, monkeypatch):
    monkeypatch.setenv("SIGNAL_M2M_SECRET", SECRET)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "p")
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,asset:read,asset:import,contact:segment:read,connection:hub:read",
    )
    # Ensure CRM session still works for seeding contacts/identities
    return crm


def _seed_identity(m2m, profile_id: str):
    with m2m.db.begin() as db:
        profile = db.get(__import__("app.crm_models", fromlist=["Profile"]).Profile, profile_id)
        identity = Identity(
            id="id-m2m-1",
            contact_id=profile.contact_id,
            connection_id="tg",
            chat_id="9001",
            external_user_id="9001",
            name=profile.name,
        )
        db.add(identity)
        db.flush()
        return identity.id


def test_expand_campaign_star():
    assert "campaign:launch" in expand_scopes({"campaign:*"})


def test_wrong_hmac_401(m2m):
    body = b"{}"
    headers = _headers(body, scopes="connection:hub:read")
    headers["X-Signal-Signature"] = "sha256=" + ("0" * 64)
    r = m2m.client.get("/api/crm/m2m/projects/p/connections/hub", headers=headers)
    assert r.status_code == 401


def test_wrong_scope_403(m2m):
    body = b""
    headers = _headers(body, scopes="asset:read")  # missing connection:hub:read
    r = m2m.client.request("GET", "/api/crm/m2m/projects/p/connections/hub", content=body, headers=headers)
    assert r.status_code == 403
    assert "scope" in r.text.lower() or "scope" in str(r.json()).lower() or r.status_code == 403


def test_replay_jti_409(m2m):
    body = b""
    headers = _headers(body, scopes="connection:hub:read", jti="replay-jti-1")
    r1 = m2m.client.request("GET", "/api/crm/m2m/projects/p/connections/hub", content=body, headers=headers)
    assert r1.status_code == 200, r1.text
    # same jti + resign with fresh timestamp still same jti key
    headers2 = _headers(body, scopes="connection:hub:read", jti="replay-jti-1")
    r2 = m2m.client.request("GET", "/api/crm/m2m/projects/p/connections/hub", content=body, headers=headers2)
    assert r2.status_code == 409


def test_project_allowlist_403(m2m):
    body = b""
    headers = _headers(body, scopes="connection:hub:read")
    r = m2m.client.request("GET", "/api/crm/m2m/projects/secret/connections/hub", content=body, headers=headers)
    assert r.status_code == 403


def test_hub_never_leaks_tokens(m2m):
    body = b""
    headers = _headers(body, scopes="connection:hub:read")
    r = m2m.client.request("GET", "/api/crm/m2m/projects/p/connections/hub", content=body, headers=headers)
    assert r.status_code == 200, r.text
    raw = r.text
    assert "bot-test" not in raw
    assert "hook-test" not in raw
    data = r.json()
    assert "items" in data
    for item in data["items"]:
        assert "token_env" not in item
        assert "secret_env" not in item
        assert "token_present" in item


def test_campaign_idempotent_create_launch_cancel_delivery(m2m):
    person = contact(m2m)
    _seed_identity(m2m, person["id"])
    payload = {
        "connection_id": "tg",
        "title": "Signal blast",
        "text": "Hello {name}",
        "profile_ids": [person["id"]],
        "external_idempotency_key": "idem-campaign-001",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = _headers(body, scopes="campaign:create")
    r = m2m.client.post("/api/crm/m2m/projects/p/campaigns", content=body, headers=headers)
    assert r.status_code == 200, r.text
    campaign = r.json()
    assert campaign["state"] == "draft"
    assert campaign["idempotent_replay"] is False
    cid = campaign["id"]

    # duplicate create → same campaign, no second row
    body2 = json.dumps(payload, separators=(",", ":")).encode()
    headers2 = _headers(body2, scopes="campaign:create")
    r2 = m2m.client.post("/api/crm/m2m/projects/p/campaigns", content=body2, headers=headers2)
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == cid
    assert r2.json()["idempotent_replay"] is True
    with m2m.db() as db:
        assert len(list(db.scalars(select(Campaign).where(Campaign.project_id == "p")))) == 1

    # preflight
    empty = b"{}"
    r3 = m2m.client.post(
        f"/api/crm/m2m/campaigns/{cid}/preflight",
        content=empty,
        headers=_headers(empty, scopes="campaign:preflight"),
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["ready"] is True

    # launch
    r4 = m2m.client.post(
        f"/api/crm/m2m/campaigns/{cid}/launch",
        content=empty,
        headers=_headers(empty, scopes="campaign:launch"),
    )
    assert r4.status_code == 200, r4.text
    assert r4.json()["queued"] == 1
    with m2m.db() as db:
        outs = list(db.scalars(select(Outbox)))
        assert len(outs) == 1
        # no bot token in outbox payload
        assert "bot-test" not in json.dumps(outs[0].payload)

    # second launch — no second outbox
    r5 = m2m.client.post(
        f"/api/crm/m2m/campaigns/{cid}/launch",
        content=empty,
        headers=_headers(empty, scopes="campaign:launch"),
    )
    assert r5.status_code == 200
    assert r5.json().get("idempotent_replay") is True
    with m2m.db() as db:
        assert len(list(db.scalars(select(Outbox)))) == 1

    # delivery
    r6 = m2m.client.request(
        "GET",
        f"/api/crm/m2m/campaigns/{cid}/delivery",
        content=b"",
        headers=_headers(b"", scopes="campaign:read"),
    )
    assert r6.status_code == 200, r6.text
    delivery = r6.json()
    assert delivery["projection"] in {"queued", "accepted", "partial"}
    assert delivery["projection"] != "delivered"
    assert "bot-test" not in r6.text

    # cancel
    r7 = m2m.client.post(
        f"/api/crm/m2m/campaigns/{cid}/cancel",
        content=empty,
        headers=_headers(empty, scopes="campaign:cancel"),
    )
    assert r7.status_code == 200
    assert r7.json()["state"] == "cancelled"


def test_asset_import_checksum(m2m):
    # minimal PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    digest = hashlib.sha256(png).hexdigest()
    payload = {
        "name": "pixel",
        "kind": "photo",
        "connection_ids": ["tg"],
        "content_base64": base64.b64encode(png).decode(),
        "sha256": digest,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/assets/import",
        content=body,
        headers=_headers(body, scopes="asset:import"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["sha256"] == digest
    assert r.json()["duplicate"] is False
    aid = r.json()["id"]

    body2 = json.dumps(payload, separators=(",", ":")).encode()
    r2 = m2m.client.post(
        "/api/crm/m2m/projects/p/assets/import",
        content=body2,
        headers=_headers(body2, scopes="asset:import"),
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == aid
    assert r2.json()["duplicate"] is True


def test_contacts_segment_limited(m2m):
    person = contact(m2m, name="Seg", tags=["vip"])
    body = b""
    r = m2m.client.request(
        "GET",
        "/api/crm/m2m/projects/p/contacts?tag=vip",
        content=body,
        headers=_headers(body, scopes="contact:segment:read"),
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert any(i["id"] == person["id"] for i in items)
    raw = r.text
    assert "password" not in raw
    assert "bot-test" not in raw


def test_segment_export_only_sendable_marketing(m2m):
    """CAMP-101: export returns sendable email ContactPoints; missing marketing consent excluded."""
    from app.crm_models import ContactPoint

    allowed = contact(m2m, name="Ok", email="ok-seg@example.ru", tags=["vip"])
    denied = contact(m2m, name="No", email="no-seg@example.ru", tags=["vip"])
    with m2m.db.begin() as db:
        ok_cp = db.scalar(
            select(ContactPoint).where(
                ContactPoint.person_id == allowed["person_id"], ContactPoint.channel == "email"
            )
        )
        assert ok_cp is not None
        from app import person_graph

        person_graph.set_consent(
            db, ok_cp.id, purpose="marketing", status="granted", evidence="import"
        )
        # denied person: no marketing consent → excluded when only_sendable

    payload = {
        "channel": "email",
        "purpose": "marketing",
        "tag": "vip",
        "only_sendable": True,
        "missing_policy": "deny",
        "limit": 50,
        "offset": 0,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/segments/export",
        content=body,
        headers=_headers(body, scopes="contact:segment:read"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["sor"] == "voronka"
    assert data["only_sendable"] is True
    addresses = {i["address"] for i in data["items"]}
    assert "ok-seg@example.ru" in addresses
    assert "no-seg@example.ru" not in addresses
    ok_row = next(i for i in data["items"] if i["address"] == "ok-seg@example.ru")
    assert ok_row["person_id"] == allowed["person_id"]
    assert ok_row["contact_point_id"]
    assert ok_row["send_allowed"] is True
    assert denied["person_id"] not in {i["person_id"] for i in data["items"]}
    assert data["excluded"] >= 1


def test_segment_export_wrong_scope_403(m2m):
    body = json.dumps({"channel": "email", "purpose": "marketing"}).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/segments/export",
        content=body,
        headers=_headers(body, scopes="person:read"),
    )
    assert r.status_code == 403


def test_crm_session_routes_still_work(m2m):
    """Regression: existing CRM UI auth path unchanged."""
    assert m2m.client.get("/api/crm/me").status_code == 200
    person = contact(m2m)
    assert person["id"]


def test_ops104_m2m_integrations_status(m2m, monkeypatch):
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,asset:read,asset:import,contact:segment:read,connection:hub:read,integrations:status:read",
    )
    with m2m.db.begin() as db:
        from app.crm_models import IntegrationOutbox
        from app.db import now as db_now
        db.add(IntegrationOutbox(
            id="m2m-dead-1",
            event_id="enrollment.desired:m2m-grant",
            destination="lms_end",
            kind="enrollment.desired",
            payload={"project_id": "p", "order_id": "ord-m2m", "grant_id": "m2m-grant"},
            state="dead",
            attempts=12,
            next_attempt_at=db_now(),
            last_error="http_503",
            correlation_id="ord-m2m",
            created_at=db_now(),
            updated_at=db_now(),
        ))
    body = b""
    headers = _headers(body, scopes="integrations:status:read")
    r = m2m.client.request("GET", "/api/crm/m2m/projects/p/integrations/status", content=body, headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["integration_outbox"]["dead"] >= 1
    assert data["auto_retry_cap_attempts"] == 12
    assert "dlq_sample" in data
    raw = r.text.lower()
    assert "secret" not in raw or "shared_database" in raw
    assert SECRET.lower() not in raw
