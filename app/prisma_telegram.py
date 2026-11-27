"""Link a private Telegram chat only after the normal webhook secret check."""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

import httpx
from fastapi import HTTPException


async def bind_prisma_chat(connection_id: str, message: dict):
    if os.getenv("PRISMA5_ENABLED") != "1" or connection_id != os.getenv("PRISMA_TELEGRAM_CONNECTION_ID"):
        return
    token = message.get("payload", "")
    if not re.fullmatch(r"p5_[0-9a-f]{24}_[0-9a-f]{32}", token) or message.get("chat_id") != message.get("user_id"):
        raise HTTPException(400, "Invalid private chat challenge")
    secret = os.getenv("PRISMA_CRM_SECRET", "")
    base = os.getenv("PRISMA_SITE_URL", "").rstrip("/")
    if not secret or not base.startswith("https://"):
        raise HTTPException(503, "Diagnostic site is not configured")
    body = json.dumps({"token": token, "chat_id": message["chat_id"], "user_id": message["user_id"], "connection_id": connection_id}, separators=(",", ":"))
    ts, nonce, path = str(int(time.time())), secrets.token_hex(24), "/api/prisma5/telegram"
    wire = "\n".join(["v1", ts, nonce, "POST", path, hashlib.sha256(body.encode()).hexdigest()])
    headers = {"content-type": "application/json", "x-prisma-timestamp": ts, "x-prisma-nonce": nonce, "x-prisma-signature": hmac.new(secret.encode(), wire.encode(), hashlib.sha256).hexdigest()}
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            result = await client.post(base + path, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Diagnostic binding temporarily unavailable") from exc
    if result.status_code in {400, 404, 409, 410, 422}:
        return  # An expired user link must not make Telegram retry forever.
    if result.status_code != 200:
        raise HTTPException(503, "Diagnostic binding retry required")
