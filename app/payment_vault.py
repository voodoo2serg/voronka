"""Owner-facing payment credential vault (env-file pattern). Never commit secrets.

Writes KEY=VALUE lines to CRM_PAYMENT_SECRETS_PATH and applies them to os.environ
for the running API process. Live money stays gated by ROBOKASSA_LIVE_READY /
YOOKASSA sandbox policy — this module never flips live gates.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ALLOWED_KEYS = {
    "ROBOKASSA_LOGIN",
    "ROBOKASSA_TEST",
    "ROBOKASSA_TEST_PASSWORD1",
    "ROBOKASSA_TEST_PASSWORD2",
    "ROBOKASSA_PASSWORD1",
    "ROBOKASSA_PASSWORD2",
    "ROBOKASSA_HASH",
    "ROBOKASSA_RECEIPT_TAX",
    "ROBOKASSA_RECEIPT_SNO",
    "YOOKASSA_ENABLED",
    "YOOKASSA_SHOP_ID",
    "YOOKASSA_SECRET_KEY",
    "YOOKASSA_WEBHOOK_SECRET",
}

# Non-secret knobs staff may set from UI without treating as credentials.
PUBLIC_KEYS = {
    "ROBOKASSA_TEST",
    "ROBOKASSA_HASH",
    "ROBOKASSA_RECEIPT_TAX",
    "ROBOKASSA_RECEIPT_SNO",
    "YOOKASSA_ENABLED",
}

SECRET_KEYS = ALLOWED_KEYS - PUBLIC_KEYS

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def secrets_path() -> Path:
    raw = (os.getenv("CRM_PAYMENT_SECRETS_PATH") or "").strip()
    if raw:
        return Path(raw)
    # Dev/test default under cwd; production sets explicit path (e.g. /run/voronka/payment-secrets.env).
    return Path(os.getenv("CRM_PAYMENT_SECRETS_DEFAULT", ".voronka-payment-secrets.env"))


def load_into_environ() -> dict[str, bool]:
    """Load vault file into os.environ (does not override already-set process env)."""
    path = secrets_path()
    present: dict[str, bool] = {k: bool(os.getenv(k)) for k in ALLOWED_KEYS}
    if not path.is_file():
        return present
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return present
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in ALLOWED_KEYS:
            continue
        if key not in os.environ or os.environ.get(key) == "":
            os.environ[key] = value
        present[key] = bool(os.environ.get(key))
    return present


def read_vault_map() -> dict[str, str]:
    path = secrets_path()
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in ALLOWED_KEYS:
            out[key] = value
    return out


def apply_updates(updates: dict[str, str | None]) -> dict[str, bool]:
    """Merge updates into vault + environ. Empty string / None clears a key.

    Returns presence map (never secret values).
    """
    current = read_vault_map()
    # Seed from live environ so UI edits don't wipe keys only present in compose env.
    for key in ALLOWED_KEYS:
        if key not in current and os.getenv(key):
            current[key] = os.environ[key]
    for key, value in updates.items():
        if key not in ALLOWED_KEYS or not _KEY_RE.match(key):
            raise ValueError(f"Недопустимый ключ: {key}")
        if value is None or str(value).strip() == "":
            current.pop(key, None)
            os.environ.pop(key, None)
            continue
        # Never allow UI to flip live Robokassa gate.
        if key == "ROBOKASSA_LIVE_READY":
            raise ValueError("ROBOKASSA_LIVE_READY нельзя менять из CRM")
        text = str(value).strip()
        if key in {"ROBOKASSA_TEST", "YOOKASSA_ENABLED"} and text not in {"0", "1"}:
            raise ValueError(f"{key} должен быть 0 или 1")
        current[key] = text
        os.environ[key] = text

    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Voronka payment secrets — managed by CRM UI. Do not commit.", ""]
    for key in sorted(current):
        lines.append(f"{key}={current[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return {k: bool(os.getenv(k)) for k in ALLOWED_KEYS}


def presence() -> dict[str, bool]:
    load_into_environ()
    return {k: bool(os.getenv(k)) for k in ALLOWED_KEYS}
