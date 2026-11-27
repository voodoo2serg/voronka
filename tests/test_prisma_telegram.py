import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

from app import prisma_telegram
from app.channels import normalize


def test_authenticated_telegram_binding_relay(monkeypatch):
    monkeypatch.setenv("PRISMA5_ENABLED", "1")
    monkeypatch.setenv("PRISMA_TELEGRAM_CONNECTION_ID", "course-bot")
    monkeypatch.setenv("PRISMA_SITE_URL", "https://solo-pr.ru")
    monkeypatch.setenv("PRISMA_CRM_SECRET", "fixture-secret")
    sent = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, content, headers):
            sent.append((url, content, headers))
            return type("Result", (), {"status_code": 200})()

    monkeypatch.setattr(prisma_telegram.httpx, "AsyncClient", Client)
    start = "p5_" + "a" * 24 + "_" + "b" * 32
    raw = {"update_id": 1, "message": {"chat": {"id": 100, "type": "private"}, "from": {"id": 100, "first_name": "Fixture"}, "text": "/start " + start}}
    message = normalize("telegram", raw)
    asyncio.run(prisma_telegram.bind_prisma_chat("other-bot", message))
    assert not sent
    asyncio.run(prisma_telegram.bind_prisma_chat("course-bot", message))
    url, content, headers = sent[0]
    assert url == "https://solo-pr.ru/api/prisma5/telegram"
    body = json.loads(content)
    assert body["chat_id"] == body["user_id"] == "100"
    assert body["connection_id"] == "course-bot" and body["token"] == start
    wire = "\n".join(["v1", headers["x-prisma-timestamp"], headers["x-prisma-nonce"], "POST", "/api/prisma5/telegram", hashlib.sha256(content.encode()).hexdigest()])
    assert headers["x-prisma-signature"] == hmac.new(b"fixture-secret", wire.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(HTTPException) as error:
        asyncio.run(prisma_telegram.bind_prisma_chat("course-bot", {**message, "user_id": "101"}))
    assert error.value.status_code == 400
    raw["message"]["chat"]["type"] = "group"
    assert normalize("telegram", raw) is None
