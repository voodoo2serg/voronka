import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from app.channels import send, RateLimited, DeliveryRejected, normalize

def test_native_multipart_and_file_id(tmp_path,monkeypatch):
    monkeypatch.setenv("TG_TOKEN","secret")
    path=tmp_path/"lesson.mp4";path.write_bytes(b"fake video")
    connection=SimpleNamespace(platform="telegram",token_env="TG_TOKEN",config={})
    client=MagicMock()
    response=MagicMock(status_code=200)
    response.json.return_value={"ok":True,"result":{"message_id":1,"video":{"file_id":"cached"}}}
    client.post.return_value=response
    wrapper=MagicMock();wrapper.__enter__.return_value=client
    with patch("app.channels.httpx.Client",return_value=wrapper):
        receipt=send(connection,"123",{"type":"video","text":"Lesson","_local_path":str(path),"_mime":"video/mp4"},"key")
        assert receipt.file_id=="cached"
        assert "video" in client.post.call_args.kwargs["files"]
        send(connection,"123",{"type":"video","text":"Lesson","_file_id":"cached"},"key2")
        assert client.post.call_args.kwargs["json"]["video"]=="cached"

def test_callback_normalization():
    raw={"update_id":1,"callback_query":{"id":"callback","from":{"id":123},
        "message":{"message_id":2,"chat":{"id":123,"type":"private"}},"data":"a:nonce:0"}}
    event=normalize("telegram",raw)
    assert event["user_id"]=="123" and event["callback"]=="a:nonce:0"

def test_circle_has_no_caption(monkeypatch):
    monkeypatch.setenv("TG_TOKEN","secret")
    connection=SimpleNamespace(platform="telegram",token_env="TG_TOKEN",config={})
    client=MagicMock();response=MagicMock(status_code=200)
    response.json.return_value={"ok":True,"result":{"message_id":1,"video_note":{"file_id":"cached"}}}
    client.post.return_value=response
    wrapper=MagicMock();wrapper.__enter__.return_value=client
    with patch("app.channels.httpx.Client",return_value=wrapper):
        send(connection,"123",{"type":"video_note","text":"Explanation","_file_id":"circle"},"key")
        assert "caption" not in client.post.call_args.kwargs["json"]
        assert client.post.call_args.kwargs["json"]["video_note"]=="circle"

def test_rate_limit_is_explicit(monkeypatch):
    monkeypatch.setenv("TG_TOKEN","secret")
    connection=SimpleNamespace(platform="telegram",token_env="TG_TOKEN",config={})
    client=MagicMock();response=MagicMock(status_code=429)
    response.json.return_value={"ok":False,"parameters":{"retry_after":42}}
    client.post.return_value=response
    wrapper=MagicMock();wrapper.__enter__.return_value=client
    with patch("app.channels.httpx.Client",return_value=wrapper),pytest.raises(RateLimited) as error:
        send(connection,"123",{"type":"text","text":"Hello"},"key")
    assert error.value.seconds==42
