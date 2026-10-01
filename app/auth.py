import hashlib, hmac, json, os, secrets, time
from urllib.parse import parse_qsl
import redis
from fastapi import Header, HTTPException
from app.db import Session, Admin

cache = redis.Redis.from_url(os.environ["REDIS_URL"], decode_responses=True)

def validate_init_data(raw, token, current_time=None):
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate fields")
    data = dict(pairs)
    signature = data.pop("hash")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid signature")
    age = (current_time or time.time()) - int(data["auth_date"])
    if age < -30 or age > 300:
        raise ValueError("expired")
    user = json.loads(data["user"])
    if not isinstance(user.get("id"), int) or isinstance(user["id"], bool):
        raise ValueError("invalid user")
    return user["id"]

def session_key(token):
    return "session:" + hashlib.sha256(token.encode()).hexdigest()

def issue_session(admin_id):
    token = secrets.token_urlsafe(32)
    cache.setex(session_key(token), 8 * 3600, str(admin_id))
    return token

def actor(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Откройте панель из Telegram")
    value = cache.get(session_key(authorization[7:]))
    if not value:
        raise HTTPException(401, "Сессия истекла")
    with Session() as db:
        admin = db.get(Admin, int(value))
        if not admin or not admin.active:
            raise HTTPException(403, "Доступ отозван")
        return admin

def permit(admin, connections=(), edit=False, owner=False):
    if owner and admin.role != "owner":
        raise HTTPException(403, "Требуется владелец")
    if edit and admin.role not in ("owner", "editor"):
        raise HTTPException(403, "Нет права редактирования")
    if admin.role != "owner" and not set(connections).issubset(set(admin.connection_ids)):
        raise HTTPException(403, "Площадка недоступна")

def allowed(admin, connection_id):
    return admin.role == "owner" or connection_id in admin.connection_ids
