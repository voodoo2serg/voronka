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


# --- API approve/reject hardening (Codex P1/P2 on #21) ---

from test_operational import system  # noqa: E402, F401 — crm fixture dependency
from test_crm import crm, contact  # noqa: E402
from app.crm_models import Profile  # noqa: E402
from app.db import now as db_now  # noqa: E402
from app.runtime import queue as runtime_queue  # noqa: E402
from uuid import uuid4


def _seed_llm_draft(crm, *, owner_id=None, draft_text="Короткий черновик", status="pending", run_status="waiting"):
    """Create Identity/Run/LlmReplyAudit tied to a CRM contact on project p."""
    person = contact(crm, name="Диалог LLM")
    with crm.db.begin() as db:
        profile = db.get(Profile, person["id"])
        if owner_id is not None:
            profile.owner_id = owner_id
        contact_id = profile.contact_id
        identity = db.scalar(select(Identity).where(Identity.contact_id == contact_id))
        if not identity:
            identity = Identity(
                id=str(uuid4()),
                contact_id=contact_id,
                connection_id="tg",
                external_user_id="u-" + person["id"][:8],
                chat_id="u-" + person["id"][:8],
                name=person["name"],
            )
            db.add(identity)
            db.flush()
        graph = {
            "start": "ai",
            "nodes": {
                "ai": {
                    "type": "llm_reply",
                    "rules_version": "local-v1",
                    "mode": "draft_only",
                    "next": "end",
                },
                "end": {"type": "finish"},
            },
        }
        funnel = Funnel(id=str(uuid4()), name="llm", connection_ids=["tg"], draft=graph, project_id="p")
        db.add(funnel)
        db.flush()
        version = Version(id=str(uuid4()), funnel_id=funnel.id, graph=graph)
        db.add(version)
        entry = Entry(token="e-" + funnel.id[:8], funnel_id=funnel.id, connection_id="tg", attribution={})
        db.add(entry)
        db.flush()
        run = Run(
            id=str(uuid4()),
            identity_id=identity.id,
            version_id=version.id,
            entry_token=entry.token,
            current="ai",
            status=run_status,
            state={"_await": "llm_approval"},
        )
        db.add(run)
        db.flush()
        # Predecessor outbox so approve must get position > 1
        prior = runtime_queue(db, identity, {"type": "text", "text": "Earlier step"}, run=run)
        prior.status = "pending"
        audit = LlmReplyAudit(
            id=str(uuid4()),
            execution_id=run.id,
            node_id="ai",
            run_id=run.id,
            identity_id=identity.id,
            contact_id=contact_id,
            connection_id="tg",
            project_id="p",
            provider="ollama",
            model="llama3",
            rules_version="local-v1",
            mode="draft_only",
            response_status=status,
            draft_text=draft_text,
            created_at=db_now(),
            updated_at=db_now(),
        )
        run.state = {**run.state, "_llm_draft_id": audit.id}
        db.add(audit)
        db.flush()
        return {
            "profile_id": person["id"],
            "draft_id": audit.id,
            "run_id": run.id,
            "identity_id": identity.id,
            "prior_outbox_id": prior.id,
            "prior_position": prior.position,
        }


def test_approve_uses_next_outbox_position(crm):
    seeded = _seed_llm_draft(crm)
    r = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert r.status_code == 200, r.text
    with crm.db() as db:
        prior = db.get(Outbox, seeded["prior_outbox_id"])
        approved = db.scalar(select(Outbox).where(Outbox.id == r.json()["message_id"]))
        assert prior.position >= 1
        assert approved.position == prior.position + 1
        assert approved.payload["text"] == "Короткий черновик"


def test_approve_does_not_resume_manual_pause(crm):
    seeded = _seed_llm_draft(crm, run_status="paused")
    with crm.db.begin() as db:
        run = db.get(Run, seeded["run_id"])
        run.status = "paused"
        run.state = {
            **dict(run.state),
            "_before_pause": "waiting",
            "_pause_reason": "manual",
            "_await": "llm_approval",
            "_llm_draft_id": seeded["draft_id"],
        }
    r = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert r.status_code == 200, r.text
    with crm.db() as db:
        run = db.get(Run, seeded["run_id"])
        assert run.status == "paused"
        assert run.state.get("_llm_draft_id") is None
        assert run.state.get("_await") is None


def test_approve_advances_waiting_llm_approval(crm):
    seeded = _seed_llm_draft(crm, run_status="waiting")
    r = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert r.status_code == 200, r.text
    with crm.db() as db:
        run = db.get(Run, seeded["run_id"])
        assert run.status == "completed"
        assert run.state.get("_await") is None


def test_manager_cannot_access_unassigned_draft(crm):
    seeded = _seed_llm_draft(crm, owner_id=123)
    assert crm.client.post(
        "/api/crm/staff",
        json={"login": "llm-mgr", "password": "manager-password-123", "role": "manager", "project_ids": ["p"]},
    ).status_code == 200
    staff = crm.client.get("/api/crm/staff").json()
    mid = next(s["id"] for s in staff if s["login"] == "llm-mgr")
    # Draft stays owned by owner (123), not this manager.
    login = crm.client.post("/api/crm/login", json={"login": "llm-mgr", "password": "manager-password-123"}).json()
    crm.client.headers["Authorization"] = "Bearer " + login["token"]
    listed = crm.client.get("/api/crm/projects/p/llm-drafts").json()
    assert all(i["id"] != seeded["draft_id"] for i in listed["items"])
    assert crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={}).status_code == 404
    assert crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/reject", json={}).status_code == 404
    # Assign contact to manager → can approve
    with crm.db.begin() as db:
        db.get(Profile, seeded["profile_id"]).owner_id = mid
    assert crm.client.get("/api/crm/projects/p/llm-drafts").json()["items"]
    ok = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert ok.status_code == 200, ok.text


def test_double_approve_second_is_conflict(crm):
    seeded = _seed_llm_draft(crm)
    assert crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={}).status_code == 200
    again = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert again.status_code == 409


def test_approve_rejects_oversized_draft_without_truncate(crm):
    huge = "x" * 4001
    seeded = _seed_llm_draft(crm, draft_text=huge)
    r = crm.client.post("/api/crm/llm-drafts/" + seeded["draft_id"] + "/approve", json={})
    assert r.status_code == 422
    assert "4000" in r.text
    with crm.db() as db:
        audit = db.get(LlmReplyAudit, seeded["draft_id"])
        assert audit.response_status == "pending"
        assert audit.message_id is None
        # No new outbox beyond the prior seeded one
        outs = list(db.scalars(select(Outbox).where(Outbox.run_id == seeded["run_id"])))
        assert len(outs) == 1
