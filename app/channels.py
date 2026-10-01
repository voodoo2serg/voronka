import os, json, hashlib
from pathlib import Path
import httpx

class DeliveryRejected(Exception):
    pass

class RateLimited(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, min(int(seconds), 3600))

class DeliveryReceipt(str):
    def __new__(cls, message_id, file_id=None):
        value = super().__new__(cls, str(message_id))
        value.file_id = file_id
        return value

def credential(name):
    if not name or not os.environ.get(name):
        raise DeliveryRejected("credential_missing")
    return os.environ[name]

def result(response, platform):
    if response.status_code == 429:
        try:
            delay = response.json().get("parameters", {}).get("retry_after", 30)
        except ValueError:
            delay = 30
        raise RateLimited(delay)
    if response.status_code >= 500:
        raise RuntimeError("platform_result_unknown")
    data = response.json()
    if platform == "telegram" and not data.get("ok"):
        if data.get("error_code") == 429:
            raise RateLimited(data.get("parameters", {}).get("retry_after", 30))
        raise DeliveryRejected("telegram_" + str(data.get("error_code", "rejected")))
    if response.status_code >= 400:
        raise DeliveryRejected(platform + "_rejected")
    return data

def send(connection, chat_id, payload, message_key):
    token = credential(connection.token_env)
    kind, text = payload.get("type", "text"), payload.get("text", "")
    with httpx.Client(timeout=httpx.Timeout(180, connect=10)) as client:
        if connection.platform == "telegram":
            if kind == "ack":
                data = result(client.post(f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                    json={"callback_query_id": payload["callback_id"]}), "telegram")
                return DeliveryReceipt(payload["callback_id"])
            methods = {"text": ("sendMessage", None), "photo": ("sendPhoto", "photo"),
                "video": ("sendVideo", "video"), "video_note": ("sendVideoNote", "video_note"),
                "voice": ("sendVoice", "voice"), "document": ("sendDocument", "document")}
            method, field = methods[kind]
            body = {"chat_id": chat_id}
            if field and kind != "video_note":
                body["caption"] = text
            elif not field:
                body["text"] = text
            if kind == "video":
                body["supports_streaming"] = True
            rows = [[{"text": b["text"], "url": b["url"]}] for b in payload.get("buttons", [])]
            rows += [[{"text": b["text"], "callback_data": b["data"]}] for b in payload.get("choices", [])]
            if rows:
                body["reply_markup"] = {"inline_keyboard": rows}
            url = f"https://api.telegram.org/bot{token}/{method}"
            if field and payload.get("_local_path") and not payload.get("_file_id"):
                path = Path(payload["_local_path"])
                with path.open("rb") as source:
                    form = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, bool)) else str(v)
                            for k, v in body.items()}
                    response = client.post(url, data=form,
                        files={field: (path.name, source, payload.get("_mime", "application/octet-stream"))})
            else:
                if field:
                    body[field] = payload.get("_file_id") or payload.get("media")
                response = client.post(url, json=body)
            data = result(response, "telegram")["result"]
            media = data.get(field) if field else None
            if isinstance(media, list):
                media = media[-1] if media else None
            file_id = media.get("file_id") if isinstance(media, dict) else None
            return DeliveryReceipt(data["message_id"], file_id)
        if connection.platform == "max":
            if kind == "ack":
                result(client.post("https://platform-api2.max.ru/answers",
                    params={"callback_id":payload["callback_id"]}, headers={"Authorization":token}, json={}), "max")
                return DeliveryReceipt(payload["callback_id"])
            if payload.get("media"):
                text += "\n" + payload["media"]
            body = {"text": text}
            rows = [[{"type": "link", "text": b["text"], "url": b["url"]}]
                    for b in payload.get("buttons", [])]
            rows += [[{"type": "callback", "text": b["text"], "payload": b["data"]}]
                     for b in payload.get("choices", [])]
            if rows:
                body["attachments"] = [{"type": "inline_keyboard", "payload": {"buttons": rows}}]
            target = connection.config.get("target_type", "user_id")
            if target not in ("user_id", "chat_id"):
                raise DeliveryRejected("max_target_invalid")
            response = client.post("https://platform-api2.max.ru/messages",
                params={target: chat_id}, headers={"Authorization": token}, json=body)
            data = result(response, "max")
            return DeliveryReceipt(data["message"]["body"]["mid"])
        if connection.platform == "vk":
            if payload.get("media"):
                text += "\n" + payload["media"]
            if payload.get("buttons"):
                text += "\n" + "\n".join(b["text"] + ": " + b["url"] for b in payload["buttons"])
            random_id = int(hashlib.sha256(message_key.encode()).hexdigest()[:7], 16)
            form = {"access_token": token, "v": os.getenv("VK_API_VERSION", "5.199"),
                    "peer_id": chat_id, "message": text, "random_id": random_id}
            choices = payload.get("choices", [])
            if choices:
                form["keyboard"] = json.dumps({"inline": True, "buttons": [
                    [{"action": {"type": "text", "label": b["text"],
                      "payload": json.dumps({"callback": b["data"]})}}] for b in choices[:10]]}, ensure_ascii=False)
            response = client.post("https://api.vk.com/method/messages.send", data=form)
            data = result(response, "vk")
            if "error" in data:
                if data["error"].get("error_code") in {6, 9}:
                    raise RateLimited(30)
                raise DeliveryRejected("vk_" + str(data["error"].get("error_code", "rejected")))
            return DeliveryReceipt(data["response"])
        raise DeliveryRejected("platform_not_supported")

def normalize(platform, raw):
    if platform == "telegram":
        query = raw.get("callback_query")
        msg = query.get("message") if query else raw.get("message")
        if not msg or msg.get("chat", {}).get("type") != "private":
            return None
        user = query["from"] if query else msg.get("from", {})
        if user.get("is_bot"):
            return None
        text = "" if query else msg.get("text", msg.get("caption", ""))
        parts = text.split(maxsplit=1)
        event = {"event_id": str(raw["update_id"]), "user_id": str(user["id"]),
            "chat_id": str(msg["chat"]["id"]), "text": text,
            "name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")]))[:200],
            "start": parts[0].split("@")[0] == "/start" if parts else False,
            "payload": parts[1] if len(parts) == 2 and parts[0].split("@")[0] == "/start" else None}
        if query:
            event.update(callback=query.get("data", ""), callback_id=query["id"])
        else:
            for kind in ("video", "video_note", "voice", "document", "photo"):
                media = msg.get(kind)
                if not media:
                    continue
                if isinstance(media, list):
                    media = media[-1]
                event["media"] = {"kind": kind, "file_id": media["file_id"],
                    "mime": media.get("mime_type", ""), "meta": {k:media[k] for k in
                        ("duration","width","height","file_size") if k in media}}
                break
        return event
    if platform == "max":
        kind = raw.get("update_type")
        if kind == "bot_started":
            return {"event_id": f"start:{raw['user']['user_id']}:{raw['timestamp']}",
                "user_id": str(raw["user"]["user_id"]), "chat_id": str(raw["user"]["user_id"]),
                "text": "", "start": True, "payload": raw.get("payload")}
        if kind == "message_callback":
            callback = raw["callback"]
            user_id = str(callback["user"]["user_id"])
            return {"event_id": "callback:" + callback["callback_id"], "user_id": user_id,
                "chat_id": user_id, "text": "", "start": False, "payload": None,
                "callback": callback.get("payload", ""), "callback_id": callback["callback_id"]}
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
        callback = ""
        try:
            callback = json.loads(msg.get("payload", "{}")).get("callback", "")
        except (ValueError, TypeError):
            pass
        return {"event_id": str(raw["event_id"]), "user_id": str(msg["from_id"]),
            "chat_id": str(msg["peer_id"]), "text": msg.get("text", ""),
            "start": bool(msg.get("ref")), "payload": msg.get("ref"), "callback": callback}
    return None
