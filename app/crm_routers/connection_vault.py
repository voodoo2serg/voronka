"""VR-454 Connection Vault API — list/create/rotate; never return raw secrets."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select

from app import connection_vault as vault
from app.crm_deps import Body, Session, crm_actor, must, staff_owner
from app.crm_models import Branch, ConnectionVault
from app.crm_service import audit, project_access, uid
from app.db import now

router = APIRouter()

VaultType = Literal[
    "telegram_bot",
    "telegram_channel",
    "max_bot",
    "email_smtp",
    "robokassa",
    "yookassa",
]


class VaultCreate(Body):
    type: VaultType
    label: str = Field(min_length=1, max_length=200)
    branch_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    active: bool = True


class VaultPatch(Body):
    label: str | None = Field(default=None, min_length=1, max_length=200)
    branch_id: str | None = None
    meta: dict[str, Any] | None = None
    active: bool | None = None


class VaultRotate(Body):
    secrets: dict[str, str] = Field(default_factory=dict)
    clear: list[str] = Field(default_factory=list)
    meta: dict[str, Any] | None = None


def _assert_branch(db, project_id: str, branch_id: str | None) -> None:
    if not branch_id:
        return
    br = must(db.get(Branch, branch_id))
    if br.project_id != project_id:
        raise HTTPException(422, "Ветка другого проекта")


def _scrub_response(payload: dict) -> dict:
    """Defense in depth: drop any accidental secret-shaped keys from API payloads."""
    banned = {"secrets_blob", "bot_token", "token", "smtp_password", "secret_key", "password1", "password2",
              "test_password1", "test_password2", "webhook_secret"}
    return {k: v for k, v in payload.items() if k not in banned}


@router.get("/projects/{pid}/connection-vault")
def list_vault(
    pid: str,
    type: str | None = Query(default=None),
    include_inactive: bool = Query(default=False),
    admin=Depends(crm_actor),
):
    with Session() as db:
        project_access(db, admin, pid)
        q = select(ConnectionVault).where(ConnectionVault.project_id == pid)
        if not include_inactive:
            q = q.where(ConnectionVault.active.is_(True))
        rows = list(db.scalars(q.order_by(ConnectionVault.type, ConnectionVault.label, ConnectionVault.id)))
        if type:
            allowed = {t.strip() for t in type.split(",") if t.strip()}
            rows = [r for r in rows if r.type in allowed]
        items = [_scrub_response(vault.public_row(r)) for r in rows]
        return {
            "items": items,
            "types": [{"id": t, "label": vault.type_label(t)} for t in vault.VAULT_TYPES],
            "secrets_exposed": False,
            "can_write": admin.role == "owner",
        }


@router.post("/projects/{pid}/connection-vault")
def create_vault(pid: str, body: VaultCreate, admin=Depends(crm_actor)):
    staff_owner(admin)
    if body.type not in vault.VAULT_TYPES:
        raise HTTPException(422, "Неизвестный тип подключения")
    with Session.begin() as db:
        project_access(db, admin, pid)
        _assert_branch(db, pid, body.branch_id)
        meta = vault.sanitize_meta(body.type, body.meta)
        secrets = vault.extract_secrets(body.type, body.secrets)
        if body.type in {"telegram_bot", "max_bot"}:
            secrets = vault.auto_webhook_secret(secrets)
        # Reject unknown secret keys early.
        unknown = set((body.secrets or {}).keys()) - set(vault.SECRET_FIELDS.get(body.type, frozenset()))
        if unknown:
            raise HTTPException(422, f"Неизвестные секретные поля: {', '.join(sorted(unknown))}")
        blob, masks, has = vault.seal_secrets(secrets)
        row = ConnectionVault(
            id=uid(),
            project_id=pid,
            branch_id=body.branch_id,
            type=body.type,
            label=body.label.strip(),
            meta=meta,
            secrets_blob=blob,
            secret_masks=masks,
            has_secret=has,
            active=body.active,
            created_by=admin.telegram_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        if body.type in {"telegram_bot", "max_bot"} and secrets:
            vault.ensure_runtime_connection(db, row, secrets)
        if body.type in {"robokassa", "yookassa"}:
            vault.dual_write_payment(row, secrets, meta)
        audit(
            db,
            admin,
            "connection_vault.created",
            vault_id=row.id,
            vault_type=row.type,
            project_id=pid,
            has_secret=has,
            secret_fields=sorted(masks.keys()),
        )
        return _scrub_response(vault.public_row(row))


@router.get("/connection-vault/{vid}")
def get_vault(vid: str, admin=Depends(crm_actor)):
    with Session() as db:
        row = must(db.get(ConnectionVault, vid))
        project_access(db, admin, row.project_id)
        return _scrub_response(vault.public_row(row))


@router.patch("/connection-vault/{vid}")
def patch_vault(vid: str, body: VaultPatch, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = must(db.get(ConnectionVault, vid))
        project_access(db, admin, row.project_id)
        if body.branch_id is not None:
            _assert_branch(db, row.project_id, body.branch_id or None)
            row.branch_id = body.branch_id or None
        if body.label is not None:
            row.label = body.label.strip()
        if body.meta is not None:
            merged = dict(row.meta or {})
            merged.update(vault.sanitize_meta(row.type, body.meta))
            row.meta = vault.sanitize_meta(row.type, merged)
        if body.active is not None:
            row.active = body.active
        row.updated_at = now()
        if row.type in {"telegram_bot", "max_bot"} and row.legacy_connection_id:
            secrets = vault.reveal_secrets(row)
            if secrets:
                vault.ensure_runtime_connection(db, row, secrets)
        audit(db, admin, "connection_vault.updated", vault_id=row.id, vault_type=row.type)
        return _scrub_response(vault.public_row(row))


@router.post("/connection-vault/{vid}/rotate")
def rotate_vault(vid: str, body: VaultRotate, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        row = must(db.get(ConnectionVault, vid))
        project_access(db, admin, row.project_id)
        unknown = set((body.secrets or {}).keys()) - set(vault.SECRET_FIELDS.get(row.type, frozenset()))
        if unknown:
            raise HTTPException(422, f"Неизвестные секретные поля: {', '.join(sorted(unknown))}")
        incoming = vault.extract_secrets(row.type, body.secrets)
        clear = [k for k in (body.clear or []) if k in vault.SECRET_FIELDS.get(row.type, frozenset())]
        blob, masks, has = vault.merge_rotate(row.secrets_blob, incoming, clear_keys=clear)
        row.secrets_blob = blob
        row.secret_masks = masks
        row.has_secret = has
        row.rotated_at = now()
        row.rotated_by = admin.telegram_id
        row.updated_at = now()
        if body.meta is not None:
            merged = dict(row.meta or {})
            merged.update(vault.sanitize_meta(row.type, body.meta))
            row.meta = vault.sanitize_meta(row.type, merged)
        secrets = vault.reveal_secrets(row)
        if row.type in {"telegram_bot", "max_bot"}:
            vault.ensure_runtime_connection(db, row, secrets)
        if row.type in {"robokassa", "yookassa"}:
            vault.dual_write_payment(row, secrets, row.meta or {})
        audit(
            db,
            admin,
            "connection_vault.rotated",
            vault_id=row.id,
            vault_type=row.type,
            secret_fields=sorted(masks.keys()),
            cleared=clear,
        )
        return _scrub_response(vault.public_row(row))


@router.post("/projects/{pid}/connection-vault/sync-legacy")
def sync_legacy(pid: str, admin=Depends(crm_actor)):
    """Dual-read seed from Connection / payment_vault / SMTP into vault rows."""
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        created = vault.sync_legacy_into_vault(db, pid, actor_id=admin.telegram_id)
        audit(db, admin, "connection_vault.sync_legacy", project_id=pid, created=len(created))
        return {
            "ok": True,
            "created": len(created),
            "items": [_scrub_response(vault.public_row(r)) for r in created],
            "secrets_exposed": False,
        }
