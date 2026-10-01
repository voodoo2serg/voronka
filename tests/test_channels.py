from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from app.channels import send, normalize, DeliveryRejected

def connection(platform):
    return SimpleNamespace(platform=platform, token_env="TEST_TOKEN", config={})

def mock_client(data, status=200):
    client = MagicMock()
    response = MagicMock(status_code=status)
    response.json.return_value = data
    client.post.return_value = response
    wrapper = MagicMock()
    wrapper.__enter__.return_value = client
    return wrapper, client

@pytest.mark.parametrize("platform,data,expected", [
    ("telegram", {"ok":True,"result":{"message_id":44}}, "44"),
    ("max", {"message":{"body":{"mid":"mid44"}}}, "mid44"),
    ("vk", {"response":44}, "44")])
def test_send_with_platform_contract(platform,data,expected,monkeypatch):
    monkeypatch.setenv("TEST_TOKEN","secret")
    wrapper, client = mock_client(data)
    with patch("app.channels.httpx.Client", return_value=wrapper):
        assert send(connection(platform),"123",{"type":"text","text":"hello"},"stable") == expected
    kwargs = client.post.call_args.kwargs
    if platform == "max":
        assert kwargs["headers"]["Authorization"]=="secret"
        assert "access_token" not in kwargs["params"]

def test_vk_stable_random_id(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN","secret")
    wrapper, client = mock_client({"response":44})
    with patch("app.channels.httpx.Client",return_value=wrapper):
        send(connection("vk"),"123",{"type":"text","text":"x"},"stable")
        first=client.post.call_args.kwargs["data"]["random_id"]
        send(connection("vk"),"123",{"type":"text","text":"x"},"stable")
        assert client.post.call_args.kwargs["data"]["random_id"]==first

def test_rejected_telegram(monkeypatch):
    monkeypatch.setenv("TEST_TOKEN","secret")
    wrapper, client=mock_client({"ok":False,"description":"rejected"})
    with patch("app.channels.httpx.Client",return_value=wrapper), pytest.raises(DeliveryRejected):
        send(connection("telegram"),"123",{"type":"text","text":"x"},"stable")

def test_max_started():
    msg=normalize("max",{"update_type":"bot_started","user":{"user_id":123},"timestamp":1000,"payload":"entry"})
    assert msg["start"] is True and msg["payload"]=="entry"

def test_vk_personal_only():
    raw={"type":"message_new","event_id":"e","object":{"message":{"from_id":123,"peer_id":123,"text":"hello","ref":"entry"}}}
    assert normalize("vk",raw)["payload"]=="entry"
    raw["object"]["message"]["peer_id"]=2000000001
    assert normalize("vk",raw) is None
