"""VR-406: course-lead vertical slice — variants, wait, intent_router, auto_framed."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import json
import secrets
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base, Contact, Identity, Version, Run, Connection, Funnel, Entry, Outbox, Event
from app.graph import graph_runtime_ready, validate_graph, validate_published_graph
from app.runtime import advance, consume, pick_variant_text
import app.crm_models  # noqa: F401
from app.crm_models import LlmReplyAudit
from app.integrations import format_signature, sign_payload
from app.db import now as db_now

from test_operational import system  # noqa: F401
from test_crm import crm  # noqa: F401


SECRET = "signal-m2m-test-secret"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            Connection(
                id="tg",
                name="TG",
                platform="telegram",
                token_env="TG_MAIN_TOKEN",
                secret_env="TG_MAIN_WEBHOOK_SECRET",
                config={},
                project_id=None,
            )
        )
        session.add(Contact(id="contact", stage="Новый"))
        session.flush()
        session.add(
            Identity(
                id="identity",
                contact_id="contact",
                connection_id="tg",
                external_user_id="123",
                chat_id="123",
            )
        )
        session.flush()
        yield session


def run_for(db, graph, state=None):
    db.add(Funnel(id="funnel", name="test", connection_ids=["tg"], draft=graph))
    db.flush()
    db.add(Version(id="version", funnel_id="funnel", graph=graph))
    db.add(Entry(token="entry", funnel_id="funnel", connection_id="tg", attribution={}))
    db.flush()
    run = Run(
        id="run",
        identity_id="identity",
        version_id="version",
        entry_token="entry",
        current=graph["start"],
        status="active",
        state=state or {},
    )
    db.add(run)
    db.flush()
    return run


def course_lead_core_graph():
    """Subset of Signal course-lead: variants + wait + condition + intent_router + auto_framed."""
    return {
        "start": "q1",
        "nodes": {
            "q1": {
                "type": "text",
                "pick": "round_robin",
                "variants": [
                    {"text": "Вопрос один А"},
                    {"text": "Вопрос один Б"},
                    {"text": "Вопрос один В"},
                ],
                "next": "w1",
            },
            "w1": {
                "type": "wait",
                "seconds": 60,
                "cancel_on_reply": True,
                "on_reply": "router",
                "next": "router",
            },
            "router": {
                "type": "intent_router",
                "routes": {"купить": "onboard", "цена": "value"},
                "patterns": [{"match": "дорого", "intent": "цена"}],
                "on_miss": "value",
            },
            "onboard": {
                "type": "text",
                "text": "Онбординг",
                "next": "end",
            },
            "value": {
                "type": "text",
                "text": "Ценность",
                "next": "soft",
            },
            "soft": {
                "type": "llm_reply",
                "mode": "auto_framed",
                "rules_version": "course-lead:v1",
                "provider": "ollama",
                "model": "llama3",
                "next": "end",
                "on_out_of_frame": "end",
            },
            "end": {"type": "finish"},
        },
        "interceptors": [
            {
                "id": "free_q",
                "when": "any_inbound",
                "condition": {"type": "llm_reply_count_in_dialog", "op": "lt", "value": 2},
                "action": {"type": "llm_reply", "mode": "auto_framed"},
                "return_to": "current_node",
                "on_limit_reached": "soft_script_nudge",
            }
        ],
        "meta": {
            "construct": "personal_dialog",
            "rules_version": "course-lead:v1",
            "rules_text": "Коротко, по делу.",
            "interceptors": [],
        },
    }


def test_course_lead_graph_validates_and_is_runtime_ready():
    g = course_lead_core_graph()
    validate_published_graph(g)
    validate_graph(g)
    assert graph_runtime_ready(g) is True


def test_text_variants_round_robin(db):
    state = {}
    node = {
        "type": "text",
        "pick": "round_robin",
        "variants": [{"text": "A"}, {"text": "B"}, {"text": "C"}],
    }
    t1, i1 = pick_variant_text(node, state)
    t2, i2 = pick_variant_text(node, state)
    assert (t1, i1) == ("A", 1)
    assert (t2, i2) == ("B", 2)


def test_wait_then_intent_router_branch(db):
    graph = course_lead_core_graph()
    run = run_for(db, graph)
    advance(db, run, db.get(Identity, "identity"))
    db.flush()
    assert run.status == "delayed"
    assert run.current == "w1"
    for o in db.scalars(select(Outbox)):
        o.status = "sent"
    db.flush()
    consume(
        db,
        db.get(Connection, "tg"),
        {"user_id": "123", "chat_id": "123", "text": "хочу купить курс", "start": False, "payload": None},
    )
    db.flush()
    kinds = [e.kind for e in db.scalars(select(Event))]
    assert "wait.cancel_on_reply" in kinds
    assert "intent_router.matched" in kinds
    assert any(o.payload.get("text") == "Онбординг" for o in db.scalars(select(Outbox)))
    assert run.status == "completed"


def test_intent_router_parks_without_inbound(db):
    graph = {
        "start": "router",
        "nodes": {
            "router": {
                "type": "intent_router",
                "routes": {"да": "yes", "нет": "no"},
                "on_miss": "no",
            },
            "yes": {"type": "text", "text": "YES", "next": "end"},
            "no": {"type": "text", "text": "NO", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    run = run_for(db, graph)
    advance(db, run, db.get(Identity, "identity"))
    db.flush()
    assert run.status == "waiting"
    assert run.state.get("_await") == "intent_router"
    consume(
        db,
        db.get(Connection, "tg"),
        {"user_id": "123", "chat_id": "123", "text": "да, готов", "start": False, "payload": None},
    )
    db.flush()
    assert any(o.payload.get("text") == "YES" for o in db.scalars(select(Outbox)))
    assert run.status == "completed"


def test_auto_framed_queues_and_continues(db):
    graph = {
        "start": "soft",
        "nodes": {
            "soft": {
                "type": "llm_reply",
                "mode": "auto_framed",
                "rules_version": "course-lead:v1",
                "provider": "ollama",
                "model": "llama3",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
        "meta": {"rules_version": "course-lead:v1", "rules_text": "persona"},
    }
    run = run_for(db, graph, {"_last_inbound": "сколько стоит?"})
    with patch("app.ai_providers.complete_draft_timed", return_value=("Цена на сайте", 8)):
        advance(db, run, db.get(Identity, "identity"))
        db.flush()
    assert run.status == "completed"
    assert any(o.payload.get("text") == "Цена на сайте" for o in db.scalars(select(Outbox)))
    audit = db.scalar(select(LlmReplyAudit))
    assert audit.mode == "auto_framed"
    assert audit.response_status == "ok"


def _headers(body: bytes, *, scopes: str, jti: str | None = None):
    ts = str(int(db_now().timestamp()))
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


def test_m2m_publish_course_lead_activates(m2m):
    graph = course_lead_core_graph()
    payload = {
        "schema_version": 1,
        "construct": "personal_dialog",
        "brand_id": "brand-1",
        "scenario_id": "scenario-course",
        "signal_version_id": "sv-course",
        "rules_version": "course-lead:v1",
        "rules_text": "Роль: консультант курса",
        "connection_ids": ["tg"],
        "external_idempotency_key": "pdialog-course-lead-vr406-001",
        "funnel_name": "Приведение на курс",
        "graph": graph,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m.client.post(
        "/api/crm/m2m/projects/p/scenarios/versions",
        content=body,
        headers=_headers(body, scopes="scenario:publish"),
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["activated"] is True
    assert out["voronka_version_id"]
    with m2m.db() as db:
        version = db.get(Version, out["voronka_version_id"])
        assert version.graph["nodes"]["router"]["type"] == "intent_router"
        assert version.graph["nodes"]["q1"]["variants"]
        funnel = db.get(Funnel, out["voronka_funnel_id"])
        assert funnel.published_version_id == version.id
