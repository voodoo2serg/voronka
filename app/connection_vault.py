"""Project-scoped CRM Connection Vault — metadata + Fernet secrets, dual-read legacy.

Types: telegram_bot | telegram_channel | max_bot | email_smtp | robokassa | yookassa.
List/API never returns raw secrets — only has_secret + ••••last4 masks.
"""
from __future__ import annotations

import os
import secrets as pysecrets
from typing import Any

from sqlalchemy import select

from app import payment_vault, secret_box
from app.crm_models import ConnectionVault
from app.crm_service import uid
from app.db import Connection, now

VAULT_TYPES = (
    "telegram_bot",
    "telegram_channel",
    "max_bot",
    "email_smtp",
    "robokassa",
    "yookassa",
)

# Secret field names accepted on create/rotate (never echoed back).
SECRET_FIELDS: dict[str, frozenset[str]] = {
    "telegram_bot": frozenset({"bot_token", "webhook_secret"}),
    "telegram_channel": frozenset(),
    "max_bot": frozenset({"token", "webhook_secret"}),
    "email_smtp": frozenset({"smtp_password"}),
    "robokassa": frozenset({"password1", "password2", "test_password1", "test_password2"}),
    "yookassa": frozenset({"secret_key", "webhook_secret"}),
}

# Non-secret metadata stored in `meta` JSON (safe for list API).
META_FIELDS: dict[str, frozenset[str]] = {
    "telegram_bot": frozenset({"bot_username"}),
    "telegram_channel": frozenset({"invite_url", "chat_id", "linked_bot_vault_id"}),
    "max_bot": frozenset({"bot_username"}),
    "email_smtp": frozenset({"from_address", "smtp_host", "smtp_user", "smtp_port"}),
    "robokassa": frozenset({"shop_id", "login", "test_mode", "hash", "receipt_tax", "receipt_sno"}),
    "yookassa": frozenset({"shop_id", "enabled"}),
}

TYPE_LABELS_RU = {
    "telegram_bot": "Telegram бот",
    "telegram_channel": "Telegram канал",
    "max_bot": "MAX бот",
    "email_smtp": "Email (SMTP)",
    "robokassa": "Robokassa",
    "yookassa": "ЮKassa",
}


def type_label(vault_type: str) -> str:
    return TYPE_LABELS_RU.get(vault_type, vault_type)


def sanitize_meta(vault_type: str, raw: dict | None) -> dict[str, Any]:
    allowed = META_FIELDS.get(vault_type, frozenset())
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key not in allowed:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (int, float)):
            out[key] = value
        else:
            text = str(value).strip()
            if text:
                out[key] = text[:500]
    return out


def extract_secrets(vault_type: str, raw: dict | None) -> dict[str, str]:
    allowed = SECRET_FIELDS.get(vault_type, frozenset())
    out: dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key not in allowed:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text:
            out[key] = text
    return out


def public_row(row: ConnectionVault, *, include_legacy: bool = True) -> dict[str, Any]:
    """Safe API shape — never secrets_blob or plaintext."""
    masks = row.secret_masks if isinstance(row.secret_masks, dict) else {}
    meta = row.meta if isinstance(row.meta, dict) else {}
    item = {
        "id": row.id,
        "project_id": row.project_id,
        "branch_id": row.branch_id,
        "type": row.type,
        "type_label": type_label(row.type),
        "label": row.label,
        "meta": meta,
        "has_secret": bool(row.has_secret),
        "secret_masks": {k: v for k, v in masks.items() if v},
        "active": bool(row.active),
        "legacy_connection_id": row.legacy_connection_id if include_legacy else None,
        "legacy_source": row.legacy_source,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "rotated_at": row.rotated_at,
        "secrets_exposed": False,
    }
    return item


def seal_secrets(secrets: dict[str, str]) -> tuple[str | None, dict[str, str], bool]:
    cleaned = {k: v for k, v in secrets.items() if v}
    if not cleaned:
        return None, {}, False
    blob = secret_box.encrypt_mapping(cleaned)
    masks = secret_box.masks_from_secrets(cleaned)
    return blob, masks, True


def merge_rotate(existing_blob: str | None, incoming: dict[str, str], clear_keys: list[str] | None = None) -> tuple[str | None, dict[str, str], bool]:
    current = secret_box.decrypt_mapping(existing_blob)
    for key in clear_keys or []:
        current.pop(key, None)
    for key, value in incoming.items():
        if value is None or str(value).strip() == "":
            continue
        current[key] = str(value).strip()
    return seal_secrets(current)


def reveal_secrets(row: ConnectionVault) -> dict[str, str]:
    """Internal runtime only — never wire to list/HTML/CSV."""
    return secret_box.decrypt_mapping(row.secrets_blob)


def auto_webhook_secret(secrets: dict[str, str], field: str = "webhook_secret") -> dict[str, str]:
    out = dict(secrets)
    if not out.get(field):
        out[field] = pysecrets.token_urlsafe(24)
    return out


def _env_token_name(platform: str, vault_id: str) -> str:
    prefix = "TG" if platform == "telegram" else "MAX"
    short = vault_id.replace("-", "")[:12].upper()
    return f"{prefix}_VAULT_{short}_TOKEN"


def _env_secret_name(platform: str, vault_id: str) -> str:
    prefix = "TG" if platform == "telegram" else "MAX"
    short = vault_id.replace("-", "")[:12].upper()
    return f"{prefix}_VAULT_{short}_WEBHOOK_SECRET"


def apply_bot_to_environ(row: ConnectionVault, secrets: dict[str, str]) -> tuple[str, str]:
    """Dual-write bot secrets into process env so Activate/runtime keep working."""
    platform = "telegram" if row.type == "telegram_bot" else "max"
    token_key = "bot_token" if row.type == "telegram_bot" else "token"
    token = secrets.get(token_key) or ""
    webhook = secrets.get("webhook_secret") or ""
    token_env = _env_token_name(platform, row.id)
    secret_env = _env_secret_name(platform, row.id)
    if token:
        os.environ[token_env] = token
    if webhook:
        os.environ[secret_env] = webhook
    # Persist alongside payment vault file pattern.
    _write_connection_env_file(token_env, token, secret_env, webhook)
    return token_env, secret_env


def _connection_env_path():
    raw = (os.getenv("CRM_CONNECTION_SECRETS_PATH") or "").strip()
    if raw:
        from pathlib import Path

        return Path(raw)
    from pathlib import Path

    return Path(os.getenv("CRM_CONNECTION_SECRETS_DEFAULT", ".voronka-connection-secrets.env"))


def _write_connection_env_file(token_env: str, token: str, secret_env: str, webhook: str) -> None:
    path = _connection_env_path()
    current: dict[str, str] = {}
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                current[k.strip()] = v
        except OSError:
            pass
    if token:
        current[token_env] = token
    if webhook:
        current[secret_env] = webhook
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Voronka connection vault dual-write — do not commit.", ""]
    for key in sorted(current):
        lines.append(f"{key}={current[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_connection_env_file() -> None:
    path = _connection_env_path()
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and (key not in os.environ or os.environ.get(key) == ""):
            os.environ[key] = value


def ensure_runtime_connection(db, row: ConnectionVault, secrets: dict[str, str]) -> Connection | None:
    """Create/update legacy Connection for telegram_bot / max_bot so Activate works."""
    if row.type not in {"telegram_bot", "max_bot"}:
        return None
    platform = "telegram" if row.type == "telegram_bot" else "max"
    token_env, secret_env = apply_bot_to_environ(row, secrets)
    username = ""
    if isinstance(row.meta, dict):
        username = str(row.meta.get("bot_username") or "").lstrip("@")
    cfg = {"username": username, "vault_id": row.id} if username or True else {}
    if row.legacy_connection_id:
        conn = db.get(Connection, row.legacy_connection_id)
        if conn:
            conn.name = row.label[:120]
            conn.token_env = token_env
            conn.secret_env = secret_env
            conn.project_id = row.project_id
            conn.config = cfg
            conn.active = bool(row.active)
            return conn
    conn = Connection(
        id=uid(),
        name=row.label[:120] or type_label(row.type),
        platform=platform,
        token_env=token_env,
        secret_env=secret_env,
        project_id=row.project_id,
        role="bot",
        active=bool(row.active),
        config=cfg,
    )
    db.add(conn)
    row.legacy_connection_id = conn.id
    return conn


def dual_write_payment(row: ConnectionVault, secrets: dict[str, str], meta: dict) -> None:
    """CRM-R9: vault row → global payment_vault file + process env (sandbox-safe)."""
    updates: dict[str, str | None] = {}
    if row.type == "robokassa":
        if meta.get("login"):
            updates["ROBOKASSA_LOGIN"] = str(meta["login"])
        # Default sandbox when staff stores test passwords without an explicit mode flag.
        if "test_mode" in meta:
            updates["ROBOKASSA_TEST"] = "1" if meta.get("test_mode") in (True, "1", "true", 1) else "0"
        elif secrets.get("test_password1") or secrets.get("test_password2"):
            updates["ROBOKASSA_TEST"] = "1"
        if meta.get("hash"):
            updates["ROBOKASSA_HASH"] = str(meta["hash"])
        if meta.get("receipt_tax"):
            updates["ROBOKASSA_RECEIPT_TAX"] = str(meta["receipt_tax"])
        if meta.get("receipt_sno"):
            updates["ROBOKASSA_RECEIPT_SNO"] = str(meta["receipt_sno"])
        mapping = {
            "password1": "ROBOKASSA_PASSWORD1",
            "password2": "ROBOKASSA_PASSWORD2",
            "test_password1": "ROBOKASSA_TEST_PASSWORD1",
            "test_password2": "ROBOKASSA_TEST_PASSWORD2",
        }
        for src, env_key in mapping.items():
            if src in secrets:
                updates[env_key] = secrets[src]
    elif row.type == "yookassa":
        if meta.get("shop_id"):
            updates["YOOKASSA_SHOP_ID"] = str(meta["shop_id"])
        if "enabled" in meta:
            updates["YOOKASSA_ENABLED"] = "1" if meta.get("enabled") in (True, "1", "true", 1) else "0"
        if "secret_key" in secrets:
            updates["YOOKASSA_SECRET_KEY"] = secrets["secret_key"]
        if "webhook_secret" in secrets:
            updates["YOOKASSA_WEBHOOK_SECRET"] = secrets["webhook_secret"]
    if updates:
        payment_vault.apply_updates(updates)


def upsert_from_payment_credentials(
    db,
    *,
    project_id: str,
    provider: str,
    secrets: dict[str, str],
    meta: dict[str, Any],
    actor_id: int | None = None,
) -> ConnectionVault | None:
    """Payments UI → Connection Vault row (same project), then dual-write already applied upstream."""
    if provider not in {"robokassa", "yookassa"}:
        return None
    existing = list(
        db.scalars(
            select(ConnectionVault).where(
                ConnectionVault.project_id == project_id,
                ConnectionVault.type == provider,
                ConnectionVault.active.is_(True),
            )
        )
    )
    row = existing[0] if existing else None
    clean_meta = sanitize_meta(provider, meta)
    if row:
        merged_meta = dict(row.meta or {})
        merged_meta.update(clean_meta)
        row.meta = sanitize_meta(provider, merged_meta)
        if secrets:
            blob, masks, has = merge_rotate(row.secrets_blob, secrets)
            row.secrets_blob = blob
            row.secret_masks = masks
            row.has_secret = has
            row.rotated_at = now()
            row.rotated_by = actor_id
        row.updated_at = now()
        dual_write_payment(row, reveal_secrets(row), row.meta or {})
        return row
    blob, masks, has = seal_secrets(secrets)
    label = "Robokassa" if provider == "robokassa" else "ЮKassa"
    row = ConnectionVault(
        id=uid(),
        project_id=project_id,
        type=provider,
        label=label,
        meta=clean_meta,
        secrets_blob=blob,
        secret_masks=masks,
        has_secret=has,
        active=True,
        legacy_source="payment_settings",
        created_by=actor_id,
        created_at=now(),
        updated_at=now(),
    )
    db.add(row)
    dual_write_payment(row, secrets, clean_meta)
    return row


def sync_legacy_into_vault(db, project_id: str, actor_id: int | None = None) -> list[ConnectionVault]:
    """Seed vault rows from Connection env + payment_vault + SMTP (idempotent dual-read)."""
    created: list[ConnectionVault] = []
    existing = list(db.scalars(select(ConnectionVault).where(ConnectionVault.project_id == project_id)))
    linked_conn = {r.legacy_connection_id for r in existing if r.legacy_connection_id}
    labels = {(r.type, (r.label or "").lower()) for r in existing}

    load_connection_env_file()
    payment_vault.load_into_environ()

    for conn in db.scalars(select(Connection).where(Connection.project_id == project_id)):
        if conn.id in linked_conn:
            continue
        if conn.platform == "telegram":
            vtype = "telegram_bot"
        elif conn.platform == "max":
            vtype = "max_bot"
        elif conn.platform == "email":
            vtype = "email_smtp"
        else:
            continue
        cfg = conn.config if isinstance(conn.config, dict) else {}
        secrets: dict[str, str] = {}
        meta: dict[str, Any] = {}
        if vtype == "telegram_bot":
            if cfg.get("username"):
                meta["bot_username"] = str(cfg["username"]).lstrip("@")
            token = os.environ.get(conn.token_env or "") or ""
            wh = os.environ.get(conn.secret_env or "") or ""
            if token:
                secrets["bot_token"] = token
            if wh:
                secrets["webhook_secret"] = wh
        elif vtype == "max_bot":
            if cfg.get("username"):
                meta["bot_username"] = str(cfg["username"]).lstrip("@")
            token = os.environ.get(conn.token_env or "") or ""
            wh = os.environ.get(conn.secret_env or "") or ""
            if token:
                secrets["token"] = token
            if wh:
                secrets["webhook_secret"] = wh
        elif vtype == "email_smtp":
            meta["from_address"] = os.getenv("SMTP_FROM") or ""
            meta["smtp_host"] = os.getenv("SMTP_HOST") or ""
            meta["smtp_user"] = os.getenv("SMTP_USER") or ""
            meta["smtp_port"] = os.getenv("SMTP_PORT") or "587"
            if os.getenv("SMTP_PASSWORD"):
                secrets["smtp_password"] = os.environ["SMTP_PASSWORD"]

        blob, masks, has = seal_secrets(secrets)
        row = ConnectionVault(
            id=uid(),
            project_id=project_id,
            type=vtype,
            label=conn.name or type_label(vtype),
            meta=sanitize_meta(vtype, meta),
            secrets_blob=blob,
            secret_masks=masks,
            has_secret=has,
            active=bool(conn.active),
            legacy_connection_id=conn.id,
            legacy_source="connection",
            created_by=actor_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        created.append(row)
        linked_conn.add(conn.id)

    # Global payment credentials → project vault rows (once per type if missing).
    present = payment_vault.presence()
    if present.get("ROBOKASSA_LOGIN") and ("robokassa", "robokassa (legacy)") not in labels:
        if not any(r.type == "robokassa" and r.legacy_source == "payment_vault" for r in existing + created):
            secrets = {}
            for src, env_key in [
                ("password1", "ROBOKASSA_PASSWORD1"),
                ("password2", "ROBOKASSA_PASSWORD2"),
                ("test_password1", "ROBOKASSA_TEST_PASSWORD1"),
                ("test_password2", "ROBOKASSA_TEST_PASSWORD2"),
            ]:
                if os.getenv(env_key):
                    secrets[src] = os.environ[env_key]
            meta = {
                "login": os.getenv("ROBOKASSA_LOGIN") or "",
                "test_mode": os.getenv("ROBOKASSA_TEST", "1") == "1",
                "hash": os.getenv("ROBOKASSA_HASH") or "sha256",
            }
            blob, masks, has = seal_secrets(secrets)
            row = ConnectionVault(
                id=uid(),
                project_id=project_id,
                type="robokassa",
                label="Robokassa (legacy)",
                meta=sanitize_meta("robokassa", meta),
                secrets_blob=blob,
                secret_masks=masks,
                has_secret=has,
                active=True,
                legacy_source="payment_vault",
                created_by=actor_id,
                created_at=now(),
                updated_at=now(),
            )
            db.add(row)
            created.append(row)

    if present.get("YOOKASSA_SHOP_ID") and not any(
        r.type == "yookassa" and r.legacy_source == "payment_vault" for r in existing + created
    ):
        secrets = {}
        if os.getenv("YOOKASSA_SECRET_KEY"):
            secrets["secret_key"] = os.environ["YOOKASSA_SECRET_KEY"]
        if os.getenv("YOOKASSA_WEBHOOK_SECRET"):
            secrets["webhook_secret"] = os.environ["YOOKASSA_WEBHOOK_SECRET"]
        meta = {
            "shop_id": os.getenv("YOOKASSA_SHOP_ID") or "",
            "enabled": os.getenv("YOOKASSA_ENABLED", "0") == "1",
        }
        blob, masks, has = seal_secrets(secrets)
        row = ConnectionVault(
            id=uid(),
            project_id=project_id,
            type="yookassa",
            label="ЮKassa (legacy)",
            meta=sanitize_meta("yookassa", meta),
            secrets_blob=blob,
            secret_masks=masks,
            has_secret=has,
            active=True,
            legacy_source="payment_vault",
            created_by=actor_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        created.append(row)

    # SMTP global → email_smtp if no email vault yet and SMTP configured.
    if os.getenv("SMTP_HOST") and not any(r.type == "email_smtp" for r in existing + created):
        secrets = {}
        if os.getenv("SMTP_PASSWORD"):
            secrets["smtp_password"] = os.environ["SMTP_PASSWORD"]
        meta = {
            "from_address": os.getenv("SMTP_FROM") or "",
            "smtp_host": os.getenv("SMTP_HOST") or "",
            "smtp_user": os.getenv("SMTP_USER") or "",
            "smtp_port": os.getenv("SMTP_PORT") or "587",
        }
        blob, masks, has = seal_secrets(secrets)
        row = ConnectionVault(
            id=uid(),
            project_id=project_id,
            type="email_smtp",
            label="SMTP (legacy)",
            meta=sanitize_meta("email_smtp", meta),
            secrets_blob=blob,
            secret_masks=masks,
            has_secret=has,
            active=True,
            legacy_source="smtp_env",
            created_by=actor_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        created.append(row)

    # Channel invite URL env → telegram_channel synthetic once.
    invite = (os.getenv("SOLO_PR_CHANNEL_INVITE_URL") or "").strip()
    if invite and not any(r.type == "telegram_channel" for r in existing + created):
        row = ConnectionVault(
            id=uid(),
            project_id=project_id,
            type="telegram_channel",
            label="Канал Solo-PR (legacy)",
            meta=sanitize_meta("telegram_channel", {"invite_url": invite}),
            secrets_blob=None,
            secret_masks={},
            has_secret=False,
            active=True,
            legacy_source="channel_env",
            created_by=actor_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        created.append(row)

    return created


def list_for_picker(db, project_id: str, types: list[str] | None = None) -> list[dict[str, Any]]:
    q = select(ConnectionVault).where(ConnectionVault.project_id == project_id, ConnectionVault.active.is_(True))
    rows = list(db.scalars(q.order_by(ConnectionVault.type, ConnectionVault.label)))
    if types:
        allowed = set(types)
        rows = [r for r in rows if r.type in allowed]
    return [public_row(r) for r in rows]
