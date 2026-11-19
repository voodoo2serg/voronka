"""PDialog P0: M2M scenario publish / pause / status + llm_reply descriptors."""
from __future__ import annotations

import json
import secrets

import pytest

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app.crm_service import uid
from app.db import Contact, Entry, Funnel, Identity, Run, Version, now
from app.graph import (
    FORWARD_COMPAT_TYPES,
    graph_runtime_ready,
    validate_graph,
    validate_published_graph,
)
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


def _llm_payload(**overrides):
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


def _classic_payload(**overrides):
    graph = {
        "start": "greet",
        "nodes": {
            "greet": {"type": "text", "text": "Привет", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    body = {
        "schema_version": 1,
        "construct": "personal_dialog",
        "brand_id": "brand-1",
        "scenario_id": "scenario-classic",
        "signal_version_id": "sv-classic",
        "rules_version": "rules-1:v1",
        "rules_text": "ok",
        "connection_ids": ["tg"],
        "external_idempotency_key": "pdialog-classic-idem-001",
        "funnel_name": "Classic dialog",
        "graph": graph,
    }
    body.update(overrides)
    return body


def test_scenario_star_expands():
    assert "scenario:publish" in expand_scopes({"scenario:*"})
    assert "send_template" in FORWARD_COMPAT_TYPES
    assert "llm_reply" not in FORWARD_COMPAT_TYPES


def test_validate_published_graph_reuses_classic_invariants():
    g = _llm_payload()["graph"]
    validate_published_graph(g)
    # VR-124 on main: llm_reply is runtime TYPES → ready to activate
    assert graph_runtime_ready(g) is True

    # question without key — must fail (classic invariant preserved)
    bad = {
        "start": "q",
        "nodes": {
            "q": {"type": "question", "text": "Как дела?", "options": ["ок"], "next": "end"},
            "end": {"type": "finish"},
        },
    }
    with pytest.raises(ValueError):
        validate_published_graph(bad)
    with pytest.raises(ValueError):
        validate_graph(bad)

    bad_llm = json.loads(json.dumps(g))
    bad_llm["nodes"]["llm"]["auto_send"] = True
    with pytest.raises(ValueError):
        validate_published_graph(bad_llm)


def test_llm_graph_activates_with_vr124_types(m2m):
    """VR-124 on main: llm_reply is in TYPES + runtime → activate after bindings."""
    payload = _llm_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["idempotent_replay"] is False
    assert first["activated"] is True
    assert first["voronka_version_id"]

    with m2m.db() as db:
        version = db.get(Version, first["voronka_version_id"])
        assert version.graph["nodes"]["llm"]["type"] == "llm_reply"
        assert version.graph["meta"]["activation"] == "active"
        funnel = db.get(Funnel, first["voronka_funnel_id"])
        assert funnel.published_version_id == version.id

    body2 = json.dumps(payload, separators=(",", ":")).encode()
    r2 = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body2,
        headers=_headers(body2, scopes="scenario:publish"),
    )
    assert r2.status_code == 200
    assert r2.json()["idempotent_replay"] is True
    assert r2.json()["activated"] is True

    empty = b""
    r3 = m2m.client.request(
        "GET",
        f"/api/crm/m2m/projects/p/scenarios/versions/{first['voronka_version_id']}/status",
        content=empty,
        headers=_headers(empty, scopes="scenario:status"),
    )
    assert r3.status_code == 200
    assert r3.json()["is_published_head"] is True


def test_forward_compat_graph_stored_inactive(m2m):
    """send_template remains forward-compat → stored_inactive, not published head."""
    graph = {
        "start": "greet",
        "nodes": {
            "greet": {"type": "text", "text": "Привет", "next": "tpl"},
            "tpl": {
                "type": "send_template",
                "document_revision_id": "rev-1",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
    }
    payload = _classic_payload(
        scenario_id="scenario-fwd",
        signal_version_id="sv-fwd",
        external_idempotency_key="pdialog-fwd-idem-001",
        funnel_name="Forward compat dialog",
        graph=graph,
    )
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["activated"] is False
    with m2m.db() as db:
        funnel = db.get(Funnel, out["voronka_funnel_id"])
        assert funnel.published_version_id != out["voronka_version_id"]


def test_classic_graph_activates_with_bindings(m2m):
    payload = _classic_payload()
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["activated"] is True
    with m2m.db() as db:
        funnel = db.get(Funnel, out["voronka_funnel_id"])
        assert funnel.published_version_id == out["voronka_version_id"]


def test_activate_requires_validate_bindings(m2m):
    # Classic graph with handoff to unknown connection → bindings fail before activate
    graph = {
        "start": "greet",
        "nodes": {
            "greet": {"type": "text", "text": "hi", "next": "sw"},
            "sw": {"type": "handoff", "connection_id": "missing-bot", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    payload = _classic_payload(
        scenario_id="scenario-handoff-bad",
        signal_version_id="sv-handoff",
        external_idempotency_key="pdialog-handoff-bad-001",
        graph=graph,
        connection_ids=["tg"],
    )
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 422


def test_reject_auto_send(m2m):
    payload = _llm_payload()
    payload["auto_send"] = True
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 422


def test_pause_execution(m2m):
    # Activate a classic version so Entry/Run are meaningful
    classic = _classic_payload(external_idempotency_key="pdialog-pause-classic-001")
    body = json.dumps(classic, separators=(",", ":")).encode()
    pub = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    ).json()
    assert pub["activated"] is True

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
