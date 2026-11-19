"""PDialog P1: llm_reply validation, draft path, wait.cancel_on_reply."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base, Contact, Identity, Version, Run, Connection, Funnel, Entry, Outbox, Event, Task
from app.graph import validate_graph
from app.runtime import advance, consume
import app.crm_models  # noqa: F401 — register LlmReplyAudit on Base.metadata
from app.crm_models import LlmReplyAudit


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


def test_llm_reply_requires_rules_version():
    graph = {
        "start": "ai",
        "nodes": {
            "ai": {"type": "llm_reply", "mode": "draft_only", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    with pytest.raises(ValueError, match="rules_version"):
        validate_graph(graph)


def test_llm_reply_rejects_auto_send_publish_field():
    graph = {
        "start": "ai",
        "nodes": {
            "ai": {
                "type": "llm_reply",
                "rules_version": "v1",
                "mode": "draft_only",
                "auto_send": False,
                "next": "end",
            },
            "end": {"type": "finish"},
        },
    }
    with pytest.raises(ValueError, match="auto_send"):
        validate_graph(graph)


def test_llm_reply_valid_descriptor():
    graph = {
        "start": "ai",
        "nodes": {
            "ai": {
                "type": "llm_reply",
                "id": "n_llm_1",
                "provider": "ollama",
                "model": "llama3",
                "temperature": 0.2,
                "max_tokens": 256,
                "rules_version": "solo-pr-v1",
                "mode": "human_approval",
                "fallback": "Менеджер ответит лично.",
                "prompt_ref": "inquiry",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
    }
    assert validate_graph(graph)["nodes"]["ai"]["rules_version"] == "solo-pr-v1"


def test_wait_cancel_on_reply_validates_on_reply_edge():
    graph = {
        "start": "wait",
        "nodes": {
            "wait": {
                "type": "wait",
                "seconds": 60,
                "cancel_on_reply": True,
                "on_reply": "replied",
                "next": "timeout",
            },
            "replied": {"type": "text", "text": "Got reply", "next": "end"},
            "timeout": {"type": "text", "text": "Timed out", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    validate_graph(graph)


def test_escalate_is_not_handoff():
    graph = {
        "start": "esc",
        "nodes": {
            "esc": {"type": "escalate", "text": "Нужен менеджер", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    validate_graph(graph)
    with pytest.raises(ValueError):
        validate_graph(
            {
                "start": "h",
                "nodes": {
                    "h": {"type": "handoff", "next": "end"},
                    "end": {"type": "finish"},
                },
            }
        )


def test_llm_reply_draft_path_parks_without_send(db):
    graph = {
        "start": "ai",
        "nodes": {
            "ai": {
                "type": "llm_reply",
                "rules_version": "local-v1",
                "mode": "draft_only",
                "provider": "ollama",
                "model": "llama3",
                "fallback": "safe",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
    }
    run = run_for(db, graph, {"_last_inbound": "Сколько стоит?"})
    with patch("app.ai_providers.complete_draft_timed", return_value=("Черновик цены нет", 12)):
        advance(db, run, db.get(Identity, "identity"))
        db.flush()
    assert run.status == "waiting"
    assert run.state.get("_await") == "llm_approval"
    assert db.scalar(select(Outbox)) is None
    audit = db.scalar(select(LlmReplyAudit))
    assert audit is not None
    assert audit.draft_text == "Черновик цены нет"
    assert audit.rules_version == "local-v1"
    assert audit.execution_id == run.id
    assert audit.response_status == "pending"
    assert audit.mode == "draft_only"


def test_llm_reply_error_uses_fallback_template(db):
    graph = {
        "start": "ai",
        "nodes": {
            "ai": {
                "type": "llm_reply",
                "rules_version": "local-v1",
                "mode": "draft_only",
                "provider": "ollama",
                "model": "llama3",
                "fallback": "Безопасный шаблон",
                "next": "end",
            },
            "end": {"type": "finish"},
        },
    }
    run = run_for(db, graph)
    with patch("app.ai_providers.complete_draft_timed", side_effect=TimeoutError("timeout")):
        advance(db, run, db.get(Identity, "identity"))
        db.flush()
    audit = db.scalar(select(LlmReplyAudit))
    assert audit.draft_text == "Безопасный шаблон"
    assert audit.response_status == "fallback"
    assert audit.error_class == "timeout"
    assert run.status == "waiting"


def test_escalate_pauses_like_manual_reply(db):
    graph = {
        "start": "esc",
        "nodes": {
            "esc": {"type": "escalate", "text": "Зовём оператора", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    run = run_for(db, graph)
    advance(db, run, db.get(Identity, "identity"))
    db.flush()
    assert run.status == "paused"
    assert run.state.get("_pause_reason") == "escalate"
    assert db.scalar(select(Task)).title == "Зовём оператора"


def test_cancel_on_reply_cancels_delayed_and_branches(db):
    graph = {
        "start": "hello",
        "nodes": {
            "hello": {"type": "text", "text": "Hi", "next": "wait"},
            "wait": {
                "type": "wait",
                "seconds": 120,
                "cancel_on_reply": True,
                "on_reply": "replied",
                "next": "timeout",
            },
            "replied": {"type": "text", "text": "Thanks for reply", "next": "end"},
            "timeout": {"type": "text", "text": "Timed out", "next": "end"},
            "end": {"type": "finish"},
        },
    }
    run = run_for(db, graph)
    advance(db, run, db.get(Identity, "identity"))
    db.flush()
    assert run.status == "delayed"
    assert run.current == "wait"
    # Pretend hello was delivered so delayed wait is live.
    for o in db.scalars(select(Outbox)):
        o.status = "sent"
    db.flush()
    consume(
        db,
        db.get(Connection, "tg"),
        {"user_id": "123", "chat_id": "123", "text": "я тут", "start": False, "payload": None},
    )
    db.flush()
    kinds = [e.kind for e in db.scalars(select(Event))]
    assert "wait.cancel_on_reply" in kinds
    assert run.due_at is None
    assert run.status == "completed"
    assert any(o.payload.get("text") == "Thanks for reply" for o in db.scalars(select(Outbox)))
