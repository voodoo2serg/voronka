import os
import httpx

class DeliveryRejected(Exception):
    pass

def credential(name):
    if not name or not os.environ.get(name):
        raise DeliveryRejected("credential_missing")
    return os.environ[name]

def send(connection, chat_id, payload, message_key):
    token = credential(connection.token_env)
    kind = payload.get("type", "text")
    text = payload.get("text", "")
    # Direct Bot API calls keep platform contracts isolated.
    with httpx.Client(timeout=20) as client:
        if connection.platform == "telegram":
            methods = {"text": ("sendMessage", None), "photo": ("sendPhoto", "photo"),
                       "video": ("sendVideo", "video"), "voice": ("sendVoice", "voice"),
                       "document": ("sendDocument", "document")}
            method, field = methods[kind]
            body = {"chat_id": chat_id}
            if field:
                body[field] = payload["media"]
                body["caption"] = text
            else:
                body["text"] = text
            if payload.get("buttons"):
                body["reply_markup"] = {"inline_keyboard": [
                    [{"text": b["text"], "url": b["url"]}] for b in payload["buttons"]]}
            response = client.post(f"https://api.telegram.org/bot{token}/{method}", json=body)
            data = response.json()
            if not data.get("ok"):
                raise DeliveryRejected("telegram_rejected")
            return str(data["result"]["message_id"])
        if connection.platform == "max":
            # Baseline has an explicit media fallback: text + URL.
            if payload.get("media"):
                text += "\n" + payload["media"]
            body = {"text": text}
            if payload.get("buttons"):
                body["attachments"] = [{"type": "inline_keyboard", "payload": {
                    "buttons": [[{"type": "link", "text": b["text"], "url": b["url"]}]
                                for b in payload["buttons"]]}}]
            target = connection.config.get("target_type", "user_id")
            if target not in ("user_id", "chat_id"):
                raise DeliveryRejected("max_target_invalid")
            response = client.post("https://platform-api2.max.ru/messages",
                params={target: chat_id}, headers={"Authorization": token}, json=body)
            if response.status_code >= 400:
                raise DeliveryRejected("max_rejected")
            return str(response.json()["message"]["body"]["mid"])
        if connection.platform == "vk":
            if payload.get("media"):
                text += "\n" + payload["media"]
            if payload.get("buttons"):
                text += "\n" + "\n".join(b["text"] + ": " + b["url"] for b in payload["buttons"])
            # Stable random_id protects retried VK sends within VK's deduplication semantics.
            import hashlib
            random_id = int(hashlib.sha256(message_key.encode()).hexdigest()[:7], 16)
            response = client.post("https://api.vk.com/method/messages.send", data={
                "access_token": token, "v": os.getenv("VK_API_VERSION", "5.199"),
                "peer_id": chat_id, "message": text, "random_id": random_id})
            data = response.json()
            if "error" in data:
                raise DeliveryRejected("vk_rejected")
            return str(data["response"])
        raise DeliveryRejected("platform_not_supported")

def normalize(platform, raw):
    if platform == "telegram":
        msg = raw.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            return None
        user = msg.get("from", {})
        if user.get("is_bot"):
            return None
        text = msg.get("text", "")
        parts = text.split(maxsplit=1)
        return {"event_id": str(raw["update_id"]), "user_id": str(user["id"]),
                "chat_id": str(msg["chat"]["id"]), "text": text,
                "start": parts[0] == "/start" if parts else False,
                "payload": parts[1] if len(parts) == 2 and parts[0] == "/start" else None}
    if platform == "max":
        kind = raw.get("update_type")
        if kind == "bot_started":
            return {"event_id": f"start:{raw['user']['user_id']}:{raw['timestamp']}",
                    "user_id": str(raw["user"]["user_id"]), "chat_id": str(raw["user"]["user_id"]),
                    "text": "", "start": True, "payload": raw.get("payload")}
        msg = raw.get("message", {})
        if kind != "message_created" or msg.get("recipient", {}).get("chat_type") != "dialog":
            return None
        sender = msg.get("sender", {})
        if sender.get("is_bot"):
            return None
        return {"event_id": str(msg["body"]["mid"]), "user_id": str(sender["user_id"]),
                "chat_id": str(sender["user_id"]), "text": msg["body"].get("text", ""),
                "start": False, "payload": None}
    if platform == "vk":
        if raw.get("type") != "message_new":
            return None
        msg = raw["object"]["message"]
        if msg["from_id"] <= 0 or msg["peer_id"] != msg["from_id"]:
            return None
        return {"event_id": str(raw["event_id"]), "user_id": str(msg["from_id"]),
                "chat_id": str(msg["peer_id"]), "text": msg.get("text", ""),
                "start": bool(msg.get("ref")), "payload": msg.get("ref")}
    return None
