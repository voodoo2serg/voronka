"""PDialog P0: M2M scenario publish / pause / status + llm_reply descriptors."""
from __future__ import annotations

import json
import secrets

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app.crm_service import uid
from app.db import Contact, Entry, Funnel, Identity, Run, Version, now
from app.graph import FORWARD_COMPAT_TYPES, validate_published_graph
from app.integrations import format_signature, sign_payload
from app.m2m_signal import expand_scopes


SECRET = "signal-m2m-test-secret"


def _headers(body: bytes, *, scopes: str, jti: str | None = None):
    ts = str(int(now().timestamp()))
    digest = sign_payload(SECRET, body, ts)
    return {
        "Content-Type": "application/json",
        "X-Signal-Timestamp": ts,
        "X-Signal-Signature": format_signature(digest),
        "X-Signal-Jti": jti or secrets.token_urlsafe(12),
        "X-Signal-Service": "signal",
        "X-Signal-Scopes": scopes,
    }


@pytest.fixture
def m2m(crm, monkeypatch):
    monkeypatch.setenv("SIGNAL_M2M_SECRET", SECRET)
    monkeypatch.setenv("SIGNAL_M2M_SERVICE_ID", "signal")
    monkeypatch.setenv("SIGNAL_M2M_PROJECT_ALLOWLIST", "p")
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,scenario:*,asset:read,connection:hub:read",
    )
    return crm


def _payload(**overrides):
    graph = {
        "start": "greet",
        "nodes": {
            "greet": {"type": "text", "text": "Привет", "next": "llm"},
            "llm": {
                "type": "llm_reply",
                "id": "llm",
                "mode": "draft_only",
                "rules_version": "rules-1:v1",
                "provider": "ollama",
                "model": "llama3.2",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
        "meta": {"construct": "personal_dialog"},
    }
    body = {
        "schema_version": 1,
        "construct": "personal_dialog",
        "brand_id": "brand-1",
        "scenario_id": "scenario-1",
        "signal_version_id": "sv-1",
        "rules_version": "rules-1:v1",
        "rules_text": "Роль: помощник",
        "connection_ids": ["tg"],
        "external_idempotency_key": "pdialog-voronka-idem-001",
        "funnel_name": "Inquiry dialog",
        "graph": graph,
    }
    body.update(overrides)
    return body


def test_scenario_star_expands():
    assert "scenario:publish" in expand_scopes({"scenario:*"})
    assert "llm_reply" in FORWARD_COMPAT_TYPES


def test_validate_published_graph_llm_reply():
    g = _payload()["graph"]
    validate_published_graph(g)
    bad = json.loads(json.dumps(g))
    bad["nodes"]["llm"]["auto_send"] = True
    with pytest.raises(ValueError):
        validate_published_graph(bad)


def test_publish_idempotent_and_status(m2m):
    payload = _payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["idempotent_replay"] is False
    assert first["voronka_version_id"]
    assert first["voronka_funnel_id"]
    assert first["rules_version"] == "rules-1:v1"

    with m2m.db() as db:
        version = db.get(Version, first["voronka_version_id"])
        assert version is not None
        assert version.graph["nodes"]["llm"]["type"] == "llm_reply"
        assert version.graph["meta"]["rules_version"] == "rules-1:v1"
        funnel = db.get(Funnel, first["voronka_funnel_id"])
        assert funnel.published_version_id == version.id

    body2 = json.dumps(payload, separators=(",", ":")).encode()
    r2 = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body2,
        headers=_headers(body2, scopes="scenario:publish"),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["voronka_version_id"] == first["voronka_version_id"]

    empty = b""
    r3 = m2m.client.request(
        "GET",
        f"/api/crm/m2m/projects/p/scenarios/versions/{first['voronka_version_id']}/status",
        content=empty,
        headers=_headers(empty, scopes="scenario:status"),
    )
    assert r3.status_code == 200, r3.text
    st = r3.json()
    assert st["rules_version"] == "rules-1:v1"
    assert st["is_published_head"] is True
    assert st["run_total"] == 0


def test_reject_auto_send(m2m):
    payload = _payload()
    payload["auto_send"] = True
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 422


def test_pause_execution(m2m):
    payload = _payload(external_idempotency_key="pdialog-pause-idem-001")
    body = json.dumps(payload, separators=(",", ":")).encode()
    pub = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    ).json()

    with m2m.db.begin() as db:
        token = secrets.token_urlsafe(8)
        db.add(
            Entry(
                token=token,
                funnel_id=pub["voronka_funnel_id"],
                connection_id="tg",
                attribution={},
                active=True,
            )
        )
        contact = Contact(id=uid(), stage="Новый", score=0)
        db.add(contact)
        db.flush()
        ident = Identity(
            id=uid(),
            contact_id=contact.id,
            connection_id="tg",
            external_user_id="u-pause-1",
            chat_id="c-pause-1",
        )
        db.add(ident)
        db.flush()
        run = Run(
            id=uid(),
            identity_id=ident.id,
            version_id=pub["voronka_version_id"],
            entry_token=token,
            current="greet",
            status="active",
            state={},
        )
        db.add(run)
        db.flush()
        run_id = run.id

    empty = b""
    r = m2m.client.post(
        f"/api/crm/m2m/projects/p/executions/{run_id}/pause",
        content=empty,
        headers=_headers(empty, scopes="scenario:pause"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"
    with m2m.db() as db:
        assert db.get(Run, run_id).status == "paused"
