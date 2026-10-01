import os
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
import hashlib, hmac, json
from urllib.parse import urlencode
import pytest
from fastapi import HTTPException
from app.auth import validate_init_data, permit
from app.db import Admin

def signed(auth_date=1000):
    data = {"auth_date": str(auth_date), "user": json.dumps({"id": 123})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    key = hmac.new(b"WebAppData", b"test-token", hashlib.sha256).digest()
    data["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)

def test_real_signature_and_age():
    assert validate_init_data(signed(), "test-token", 1100) == 123

def test_tampered_data():
    with pytest.raises(ValueError):
        validate_init_data(signed().replace("1000", "1001"), "test-token", 1100)

def test_old_data():
    with pytest.raises(ValueError):
        validate_init_data(signed(), "test-token", 1500)

def test_scope_denies_other_connection():
    a = Admin(telegram_id=1, role="editor", active=True, connection_ids=["tg"])
    with pytest.raises(HTTPException):
        permit(a, ["max"], edit=True)

def test_viewer_cannot_edit():
    a = Admin(telegram_id=1, role="viewer", active=True, connection_ids=["tg"])
    with pytest.raises(HTTPException):
        permit(a, ["tg"], edit=True)

def test_duplicate_signed_fields_rejected():
    with pytest.raises(ValueError):
        validate_init_data(signed() + "&auth_date=1000", "test-token", 1100)
