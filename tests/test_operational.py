"""HTTP -> durable inbox -> worker -> native transport -> survey -> bonus.
Run against SQLite normally and PostgreSQL in the integration CI job.
"""
import os, json, time, hashlib, hmac, subprocess
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault("DATABASE_URL","sqlite://")
os.environ.setdefault("REDIS_URL","redis://localhost:6379/0")
import pytest, fakeredis
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app import main, auth, media, worker, runtime
from app.db import Base, Admin, Connection, Identity, Contact, Inbox, Outbox, Asset, Run, Version, Event, Task

def signed(user=123):
    data={"auth_date":str(int(time.time())),"user":json.dumps({"id":user})}
    check="\n".join(f"{k}={v}" for k,v in sorted(data.items()))
    key=hmac.new(b"WebAppData",b"admin-test",hashlib.sha256).digest()
    data["hash"]=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(data)

@pytest.fixture
def system(monkeypatch,tmp_path):
    url=os.getenv("TEST_DATABASE_URL","sqlite://")
    kwargs={"poolclass":StaticPool,"connect_args":{"check_same_thread":False}} if url=="sqlite://" else {}
    engine=create_engine(url,**kwargs)
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine,expire_on_commit=False)
    with factory.begin() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.add(Admin(telegram_id=123,role="owner",active=True,connection_ids=[]))
        db.add(Admin(telegram_id=456,role="editor",active=True,connection_ids=["tg"]))
        db.add(Connection(id="tg",name="Telegram",platform="telegram",token_env="TG_MAIN_TOKEN",
            secret_env="TG_MAIN_WEBHOOK_SECRET",config={"username":"real_bot"}))
        db.add(Connection(id="other",name="Private",platform="telegram",token_env="TG_OTHER_TOKEN",
            secret_env="TG_OTHER_WEBHOOK_SECRET",config={}))
    cache=fakeredis.FakeRedis(decode_responses=True)
    for module in [main,auth,media,worker]:
        monkeypatch.setattr(module,"Session",factory)
    for module in [main,auth,worker]:
        monkeypatch.setattr(module,"cache",cache)
    monkeypatch.setattr(media,"ROOT",tmp_path)
    monkeypatch.setenv("ADMIN_BOT_TOKEN","admin-test")
    monkeypatch.setenv("OWNER_TELEGRAM_ID","123")
    monkeypatch.setenv("TG_MAIN_TOKEN","bot-test")
    monkeypatch.setenv("TG_MAIN_WEBHOOK_SECRET","hook-test")
    client=TestClient(main.app)
    response=client.post("/auth",json={"init_data":signed()})
    assert response.status_code==200,response.text
    client.headers["Authorization"]="Bearer "+response.json()["token"]
    yield SimpleNamespace(client=client,db=factory,cache=cache,root=tmp_path)
    client.close();engine.dispose()

def hook(system,index,text=None,callback=None,media_data=None,user=900):
    msg={"message_id":index,"from":{"id":user,"first_name":"Test"},
         "chat":{"id":user,"type":"private"}}
    if text is not None:msg["text"]=text
    if media_data:msg.update(media_data)
    raw={"update_id":index,"message":msg}
    if callback:
        raw={"update_id":index,"callback_query":{"id":"cb"+str(index),"from":{"id":user},
            "message":msg,"data":callback}}
    return system.client.post("/hooks/tg",json=raw,
        headers={"X-Telegram-Bot-Api-Secret-Token":"hook-test"})

def drain(system):
    with patch("app.worker.send",return_value="sent-id"):
        for _ in range(100):
            [system.cache.delete(key) for key in system.cache.scan_iter("send-rate:*")]
            if not worker.outgoing():
                break

def make_funnel(system,graph):
    r=system.client.post("/api/funnels",json={"name":"Course","connection_ids":["tg"],"graph":graph})
    assert r.status_code==200,r.text
    fid=r.json()["id"]
    r=system.client.post("/api/funnels/"+fid+"/publish")
    assert r.status_code==200,r.text
    r=system.client.post("/api/entries",json={"funnel_id":fid,"connection_id":"tg","attribution":{"source":"test"}})
    assert r.status_code==200,r.text
    return fid,r.json()["payload"]

def graph():
    return {"start":"video","nodes":{
        "video":{"type":"video","text":"Lesson","media":"https://cdn.example.net/video.mp4","next":"q"},
        "q":{"type":"question","key":"ack","text":"Ready?","options":["Continue"],"next":"survey"},
        "survey":{"type":"survey","key":"done","questions":[{"key":"need","text":"Your need?"},{"key":"role","text":"Your role?","options":["PR","Business"]}],"next":"grant"},
        "grant":{"type":"grant","product":"book","requires":["done"],"next":"pdf"},
        "pdf":{"type":"document","text":"Your book","media":"https://cdn.example.net/book.pdf","next":"goal"},
        "goal":{"type":"goal","text":"book_received","next":"task"},
        "task":{"type":"task","text":"Contact participant","next":"end"},"end":{"type":"finish"}}}

def test_full_http_funnel_and_duplicate_webhook(system):
    fid,entry=make_funnel(system,graph())
    assert hook(system,1,"/start "+entry).status_code==200
    assert hook(system,1,"/start "+entry).status_code==200
    assert worker.incoming()
    with system.db() as db:
        assert len(db.scalars(select(Run)).all())==1
        run=db.scalar(select(Run))
        first_nonce=run.state["_prompt"]
        assert db.scalar(select(Task)) is None
    # An early answer stays in inbox instead of disappearing.
    assert hook(system,2,callback="a:"+first_nonce+":0").status_code==200
    assert worker.incoming()
    with system.db() as db:
        assert db.scalar(select(Inbox).where(Inbox.external_event_id=="2")).status=="pending"
    drain(system)
    with system.db.begin() as db:
        row=db.scalar(select(Inbox).where(Inbox.external_event_id=="2"))
        row.available_at=runtime.now()-timedelta(seconds=1)
    assert worker.incoming();drain(system)
    # Same button from previous step must not answer the next question.
    assert hook(system,3,callback="a:"+first_nonce+":0").status_code==200
    assert worker.incoming()
    with system.db() as db:
        assert db.scalar(select(Run)).state["_survey_index"]==0
    assert hook(system,4,"Automate work").status_code==200
    assert worker.incoming();drain(system)
    with system.db() as db:
        nonce=db.scalar(select(Run)).state["_prompt"]
        assert db.scalar(select(Task)) is None
    assert hook(system,5,callback="a:"+nonce+":0").status_code==200
    assert worker.incoming();drain(system)
    with system.db() as db:
        run=db.scalar(select(Run))
        assert run.status=="completed"
        assert db.scalar(select(Task)).title=="Contact participant"
        assert db.scalar(select(Event).where(Event.kind=="goal.reached"))
    # Publishing an edit cannot mutate this participant's version.
    version_id=run.version_id
    g=graph();g["nodes"]["q"]["text"]="New question"
    response=system.client.put("/api/funnels/"+fid+"/draft",json={"revision":1,"graph":g,"name":"Renamed"})
    assert response.status_code==200
    assert system.client.post("/api/funnels/"+fid+"/publish").status_code==200
    with system.db() as db:
        assert db.get(Version,version_id).graph["nodes"]["q"]["text"]=="Ready?"

def test_roles_are_checked_on_every_request(system):
    login=system.client.post("/auth",json={"init_data":signed(456)})
    assert login.status_code==200
    system.client.headers["Authorization"]="Bearer "+login.json()["token"]
    response=system.client.post("/api/funnels",json={"name":"Denied","connection_ids":["other"],"graph":{"start":"end","nodes":{"end":{"type":"finish"}}}})
    assert response.status_code==403
    with system.db.begin() as db:db.get(Admin,456).active=False
    assert system.client.get("/api/connections").status_code==403

def test_webhook_requires_secret(system):
    assert system.client.post("/hooks/tg",json={"update_id":1}).status_code==403

def test_photo_and_pdf_bank_and_scope(system):
    r=system.client.post("/api/assets",data={"name":"Bonus","kind":"document","connection_ids":'["tg"]'},
        files={"file":("../../../escape.pdf",b"%PDF-1.4\nfake", "application/pdf")})
    assert r.status_code==200,r.text
    aid=r.json()["id"]
    assert len(list(system.root.iterdir()))==1
    assert system.client.get("/api/assets/"+aid+"/file").status_code==200
    assert system.client.get("/api/assets").json()[0]["id"]==aid
    r=system.client.post("/api/assets",data={"name":"bad","kind":"document","connection_ids":'["tg"]'},
        files={"file":("bad.pdf",b"<html>fake</html>","application/pdf")})
    assert r.status_code==422
    assert len(list(system.root.iterdir()))==1
    assert system.client.post("/api/assets/test-send",json={"asset_id":aid,"connection_id":"other","chat_id":"123"}).status_code==403

def test_pending_asset_cannot_be_deleted(system):
    r=system.client.post("/api/assets",data={"name":"Bonus","kind":"document","connection_ids":'["tg"]'},
        files={"file":("bonus.pdf",b"%PDF-1.4\n","application/pdf")})
    aid=r.json()["id"]
    assert system.client.post("/api/assets/test-send",json={"asset_id":aid,"connection_id":"tg","chat_id":"123"}).status_code==200
    assert system.client.delete("/api/assets/"+aid).status_code==409

def test_imported_media_requires_admin(system):
    hook(system,1,media_data={"video":{"file_id":"tg-file","duration":12}},user=900)
    worker.incoming()
    with system.db() as db:assert db.scalar(select(Asset)) is None
    hook(system,2,"My lesson",media_data={"video":{"file_id":"tg-file","duration":12}},user=456)
    worker.incoming()
    with system.db() as db:
        asset=db.scalar(select(Asset))
        assert asset.telegram_refs=={"tg":"tg-file"}
        assert asset.name=="My lesson"

def test_delay_starts_after_actual_sends(system):
    g={"start":"text","nodes":{"text":{"type":"text","text":"First","next":"wait"},
       "wait":{"type":"wait","seconds":120,"next":"end"},"end":{"type":"finish"}}}
    fid,entry=make_funnel(system,g)
    hook(system,1,"/start "+entry);worker.incoming()
    assert worker.timers() is False
    drain(system)
    assert worker.timers()
    with system.db() as db:
        r=db.scalar(select(Run))
        assert r.status=="delayed" and r.due_at is not None

def test_pause_resume_preserves_queued_prompts(system):
    fid,entry=make_funnel(system,graph());hook(system,1,"/start "+entry);worker.incoming()
    with system.db() as db:rid=db.scalar(select(Run)).id
    assert system.client.post("/api/runs/"+rid+"/action",json={"action":"pause"}).status_code==200
    with system.db() as db:assert {o.status for o in db.scalars(select(Outbox).where(Outbox.run_id==rid))}=={"held"}
    assert system.client.post("/api/runs/"+rid+"/action",json={"action":"resume"}).status_code==200
    with system.db() as db:assert {o.status for o in db.scalars(select(Outbox).where(Outbox.run_id==rid))}=={"pending"}

def test_failed_send_blocks_later_messages_and_429_is_scheduled(system):
    from app.channels import RateLimited
    fid,entry=make_funnel(system,graph());hook(system,1,"/start "+entry);worker.incoming()
    with patch("app.worker.send",side_effect=RateLimited(120)):
        assert worker.outgoing()
    with system.db() as db:
        messages=db.scalars(select(Outbox).order_by(Outbox.position)).all()
        assert messages[0].status=="pending" and messages[0].attempts==1
        assert messages[0].due_at is not None
    [system.cache.delete(key) for key in system.cache.scan_iter("send-rate:*")]
    with patch("app.worker.send") as send:
        assert worker.outgoing() is False
        send.assert_not_called()

def test_mp4_probe_and_upload(system,tmp_path):
    sample=tmp_path/"sample.mp4"
    subprocess.run(["ffmpeg","-v","error","-f","lavfi","-i","color=c=blue:s=128x128:d=1",
        "-c:v","libx264","-pix_fmt","yuv420p","-y",str(sample)],check=True)
    with sample.open("rb") as stream:
        r=system.client.post("/api/assets",data={"name":"Real MP4","kind":"video","connection_ids":'["tg"]'},
            files={"file":("lesson.mp4",stream,"video/mp4")})
    assert r.status_code==200,r.text
    assert r.json()["metadata"]["duration"]>0
