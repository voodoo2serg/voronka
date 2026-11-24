"""Fernet sealed box for CRM Connection Vault secrets.

Never log or return plaintext. Key from CRM_SECRETS_FERNET_KEY (url-safe
base64 32-byte key) or auto-persisted file at CRM_SECRETS_FERNET_KEY_PATH.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_fernet: Fernet | None = None


def _key_path() -> Path:
    raw = (os.getenv("CRM_SECRETS_FERNET_KEY_PATH") or "").strip()
    if raw:
        return Path(raw)
    return Path(os.getenv("CRM_SECRETS_FERNET_KEY_DEFAULT", ".voronka-fernet.key"))


def _load_or_create_key() -> bytes:
    explicit = (os.getenv("CRM_SECRETS_FERNET_KEY") or "").strip()
    if explicit:
        return explicit.encode("ascii")
    path = _key_path()
    if path.is_file():
        return path.read_text(encoding="utf-8").strip().encode("ascii")
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.decode("ascii") + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def reset_fernet_cache() -> None:
    """Test helper — drop cached Fernet after env changes."""
    global _fernet
    _fernet = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_or_create_key())
    return _fernet


def last4(plain: str | None) -> str | None:
    if plain is None:
        return None
    text = str(plain)
    if not text:
        return None
    return text[-4:] if len(text) >= 4 else text


def mask_display(plain: str | None) -> str | None:
    """UI mask like ••••xYz9 — never the full secret."""
    tip = last4(plain)
    if tip is None:
        return None
    return f"••••{tip}"


def encrypt_mapping(secrets: dict[str, str]) -> str:
    payload = json.dumps({k: str(v) for k, v in secrets.items() if v is not None and str(v) != ""}, ensure_ascii=False)
    return get_fernet().encrypt(payload.encode("utf-8")).decode("ascii")


def decrypt_mapping(blob: str | None) -> dict[str, str]:
    if not blob:
        return {}
    try:
        raw = get_fernet().decrypt(blob.encode("ascii"))
    except (InvalidToken, ValueError, TypeError):
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v is not None and str(v) != ""}


def masks_from_secrets(secrets: dict[str, str]) -> dict[str, str]:
    return {k: mask_display(v) for k, v in secrets.items() if mask_display(v)}


def fingerprint(plain: str) -> str:
    """Stable non-reversible short fingerprint for dual-read dedupe (not a secret)."""
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()[:16]


def derive_dev_key_from_seed(seed: str) -> str:
    """Deterministic Fernet key for tests (not for production)."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")
