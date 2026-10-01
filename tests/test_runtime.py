import os
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.db import Base, Contact, Identity, Version, Run, Entitlement, Connection, Funnel, Entry, Outbox
from app.runtime import advance, consume
from app.channels import normalize

@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Connection(id="tg", name="TG", platform="telegram",token_env="TG_MAIN_TOKEN",secret_env="TG_MAIN_WEBHOOK_SECRET",config={}))
        session.add(Contact(id="contact",stage="Новый"))
        session.flush()
        session.add(Identity(id="identity",contact_id="contact",connection_id="tg",external_user_id="123",chat_id="123"))
        session.flush()
        yield session

def run_for(db, graph, state=None):
    db.add(Funnel(id="funnel",name="test",connection_ids=["tg"],draft=graph))
    db.flush()
    db.add(Version(id="version",funnel_id="funnel",graph=graph))
    db.add(Entry(token="entry",funnel_id="funnel",connection_id="tg",attribution={}))
    db.flush()
    run=Run(id="run",identity_id="identity",version_id="version",entry_token="entry",
        current=graph["start"],status="active",state=state or {})
    db.add(run); db.flush()
    return run

def test_grant_blocked_without_completed_survey(db):
    graph={"start":"grant","nodes":{"grant":{"type":"grant","requires":["done"],"product":"book","next":"end"},"end":{"type":"finish"}}}
    run=run_for(db,graph)
    advance(db,run,db.get(Identity,"identity"));db.flush()
    assert run.status=="blocked"
    assert db.scalar(select(Entitlement)) is None

def test_grant_after_gate(db):
    graph={"start":"grant","nodes":{"grant":{"type":"grant","requires":["done"],"product":"book","next":"end"},"end":{"type":"finish"}}}
    run=run_for(db,graph,{"done":True})
    advance(db,run,db.get(Identity,"identity"));db.flush()
    assert run.status=="completed"
    assert db.scalar(select(Entitlement)).product_key=="book"

def test_survey_requires_all_answers(db):
    graph={"start":"survey","nodes":{
        "survey":{"type":"survey","key":"done","questions":[{"key":"a","text":"A?"},{"key":"b","text":"B?"}],"next":"grant"},
        "grant":{"type":"grant","requires":["done"],"product":"book","next":"end"},"end":{"type":"finish"}}}
    run=run_for(db,graph)
    advance(db,run,db.get(Identity,"identity"));db.flush()
    for o in db.scalars(select(Outbox)):o.status="sent"
    db.flush()
    message={"user_id":"123","chat_id":"123","text":"answer one","start":False,"payload":None}
    consume(db,db.get(Connection,"tg"),message);db.flush()
    assert run.status=="waiting"
    assert db.scalar(select(Entitlement)) is None
    for o in db.scalars(select(Outbox)):o.status="sent"
    db.flush()
    consume(db,db.get(Connection,"tg"),{**message,"text":"answer two"});db.flush()
    assert run.status=="completed"
    assert db.scalar(select(Entitlement)) is not None

def test_telegram_deep_link_and_group_exclusion():
    msg={"update_id":7,"message":{"from":{"id":123},"chat":{"id":123,"type":"private"},"text":"/start abc"}}
    assert normalize("telegram",msg)["payload"]=="abc"
    msg["message"]["chat"]["type"]="supergroup"
    assert normalize("telegram",msg) is None


def test_stop_cancels_pending_automation(db):
    graph={"start":"q","nodes":{"q":{"type":"question","text":"Question?","key":"answer","next":"end"},"end":{"type":"finish"}}}
    run=run_for(db,graph)
    advance(db,run,db.get(Identity,"identity"));db.flush()
    assert db.scalar(select(Outbox)).status=="pending"
    consume(db,db.get(Connection,"tg"),{"user_id":"123","chat_id":"123","text":"/stop","start":False,"payload":None})
    db.flush()
    assert run.status=="stopped"
    assert db.scalar(select(Outbox)).status=="cancelled"
