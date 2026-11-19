"""Voronka delivery cache bridged to Signal/Registry asset_id (Media SoR Wave B).

Bank rows store delivery bytes + private telegram_refs. Canonical originals stay
in Media Registry. Soft CRM refs store signal_asset_id only — no second library.
"""
from __future__ import annotations

import hashlib
import shutil
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app import media as media_mod
from app.crm_service import uid
from app.db import Asset, Connection
from app import signal_media

SOFT_REF_KIND = "media.soft_ref"


class MediaBridgeError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def find_asset_by_signal_id(db, signal_asset_id: str) -> Asset | None:
    aid = (signal_asset_id or "").strip()
    if not aid:
        return None
    # Exact local id first (Wave A already imported → campaign uses voronka id).
    local = db.get(Asset, aid)
    if local is not None:
        return local
    for asset in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(500)):
        meta = asset.metadata_json or {}
        if meta.get("signal_asset_id") == aid:
            return asset
    return None


def find_asset_by_sha(db, digest: str, connection_ids: list[str] | None = None) -> Asset | None:
    for asset in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(500)):
        meta = asset.metadata_json or {}
        if meta.get("sha256") != digest:
            continue
        if not meta.get("signal_import") and not meta.get("signal_asset_id"):
            continue
        if connection_ids:
            overlap = set(asset.connection_ids or []) & set(connection_ids)
            if not overlap and asset.connection_ids:
                continue
        return asset
    return None


def import_bytes_to_bank(
    db,
    *,
    name: str,
    kind: str,
    connection_ids: list[str],
    raw: bytes,
    sha256: str | None = None,
    signal_asset_id: str | None = None,
    content_type: str | None = None,
) -> tuple[Asset, bool]:
    """Write bytes into Voronka media bank. Idempotent by sha256 (+ optional signal_asset_id).

    Returns (asset, duplicate).
    """
    if kind not in {"photo", "document", "video", "video_note", "voice"}:
        raise MediaBridgeError("invalid_kind", f"Unsupported kind: {kind}")
    if not raw:
        raise MediaBridgeError("empty_file", "Empty media bytes")
    digest = (sha256 or hashlib.sha256(raw).hexdigest()).lower()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise MediaBridgeError("checksum_mismatch", "checksum mismatch")
    max_size = 10_000_000 if kind == "photo" else 50_000_000
    if len(raw) > max_size:
        raise MediaBridgeError("too_large", "Размер превышает лимит")

    if signal_asset_id:
        existing = find_asset_by_signal_id(db, signal_asset_id)
        if existing is not None and (existing.local_path or existing.telegram_refs):
            return existing, True
    prior = find_asset_by_sha(db, digest, connection_ids)
    if prior is not None:
        meta = dict(prior.metadata_json or {})
        if signal_asset_id and not meta.get("signal_asset_id"):
            meta["signal_asset_id"] = signal_asset_id
            prior.metadata_json = meta
        return prior, True

    media_mod.ROOT.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(media_mod.ROOT).free < media_mod.MIN_FREE:
        raise MediaBridgeError("disk_full", "Недостаточно места на диске")

    asset_id = uid()
    extension = {
        "video": ".mp4",
        "video_note": ".mp4",
        "photo": ".img",
        "voice": ".audio",
        "document": ".pdf",
    }[kind]
    path = media_mod.ROOT / (asset_id + extension)
    try:
        path.write_bytes(raw)
        mime, metadata = media_mod.inspect_file(path, kind)
        real_extension = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "audio/ogg": ".ogg",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
        }.get(mime, extension)
        if path.suffix != real_extension:
            updated = path.with_suffix(real_extension)
            path.rename(updated)
            path = updated
        meta = {
            **metadata,
            "size": len(raw),
            "sha256": digest,
            "signal_import": True,
        }
        if signal_asset_id:
            meta["signal_asset_id"] = signal_asset_id
        if content_type:
            meta["source_content_type"] = content_type
        asset = Asset(
            id=asset_id,
            name=(name or signal_asset_id or asset_id)[:160],
            kind=kind,
            mime=mime,
            local_path=path.name,
            connection_ids=list(connection_ids),
            telegram_refs={},
            metadata_json=meta,
        )
        db.add(asset)
        db.flush()
        return asset, False
    except MediaBridgeError:
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise


def ensure_voronka_asset_from_signal(
    db,
    *,
    signal_asset_id: str,
    connection_ids: list[str],
    name: str | None = None,
    kind: str | None = None,
    communication_ref: str | None = None,
) -> tuple[Asset, bool]:
    """Fetch Registry/Signal bytes → import into bank. Returns (asset, already_present)."""
    aid = (signal_asset_id or "").strip()
    if not aid:
        raise MediaBridgeError("missing_asset_id", "signal_asset_id required")
    if not connection_ids:
        raise MediaBridgeError("connection_required", "connection_ids required")

    existing = find_asset_by_signal_id(db, aid)
    if existing is not None and (existing.local_path or existing.telegram_refs):
        return existing, True

    try:
        fetched = signal_media.fetch_asset_bytes(aid)
    except signal_media.SignalMediaError as exc:
        raise MediaBridgeError(exc.code, exc.message, details=exc.body) from exc

    resolved_kind = kind or fetched.kind or "photo"
    if resolved_kind not in {"photo", "document", "video", "video_note", "voice"}:
        resolved_kind = "photo"
    asset, dup = import_bytes_to_bank(
        db,
        name=name or fetched.original_filename or aid,
        kind=resolved_kind,
        connection_ids=connection_ids,
        raw=fetched.data,
        sha256=fetched.sha256,
        signal_asset_id=aid,
        content_type=fetched.content_type,
    )
    if communication_ref:
        signal_media.link_communication_asset(
            asset_id=aid,
            communication_ref=communication_ref,
            role="attachment",
            meta_json=None,
        )
    return asset, dup


def ensure_campaign_asset(
    db,
    connection: Connection,
    asset_id: str | None,
) -> tuple[str | None, str | None, bool]:
    """Resolve campaign asset_id to a Voronka bank id.

    Returns (voronka_asset_id, blocker, rewritten).
    When asset_id is a Signal/Registry id and fetch is configured, imports once.
    """
    if not asset_id:
        return None, None, False
    asset = db.get(Asset, asset_id)
    if asset is not None:
        return asset.id, None, False

    bridged = find_asset_by_signal_id(db, asset_id)
    if bridged is not None and bridged.id != asset_id:
        return bridged.id, None, True

    if not signal_media.media_fetch_configured():
        return asset_id, "Медиафайл не найден", False

    try:
        asset, _dup = ensure_voronka_asset_from_signal(
            db,
            signal_asset_id=asset_id,
            connection_ids=[connection.id],
            communication_ref=f"campaign:{connection.project_id or 'x'}:{asset_id}",
        )
        return asset.id, None, True
    except MediaBridgeError as exc:
        return asset_id, exc.message, False


def soft_ref_event_key(person_id: str, signal_asset_id: str) -> str:
    return f"media.soft_ref:{person_id}:{signal_asset_id}"


def soft_ref_payload(
    *,
    signal_asset_id: str,
    role: str = "attachment",
    voronka_asset_id: str | None = None,
    name: str | None = None,
    content_type: str | None = None,
    communication_id: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "signal_asset_id": signal_asset_id,
        "role": role,
        "source": "signal_registry",
        # Explicit: soft ref is not a blob store entry.
        "library": False,
    }
    if voronka_asset_id:
        out["voronka_asset_id"] = voronka_asset_id
    if name:
        out["name"] = name[:160]
    if content_type:
        out["content_type"] = content_type
    if communication_id:
        out["communication_id"] = communication_id
    return out


def list_soft_refs(db, person_id: str, *, project_id: str | None = None, limit: int = 100) -> list[dict]:
    from app.crm_models import Interaction

    q = (
        select(Interaction)
        .where(
            Interaction.person_id == person_id,
            Interaction.kind == SOFT_REF_KIND,
        )
        .order_by(Interaction.occurred_at.desc())
        .limit(limit)
    )
    items = []
    for ix in db.scalars(q):
        if project_id and ix.project_id and ix.project_id != project_id:
            continue
        payload = ix.payload or {}
        items.append(
            {
                "id": ix.id,
                "person_id": ix.person_id,
                "project_id": ix.project_id,
                "occurred_at": ix.occurred_at,
                "signal_asset_id": payload.get("signal_asset_id"),
                "voronka_asset_id": payload.get("voronka_asset_id"),
                "role": payload.get("role") or "attachment",
                "name": payload.get("name"),
                "content_type": payload.get("content_type"),
                "communication_id": payload.get("communication_id") or ix.communication_id,
            }
        )
    return items


def bridge_http_error(exc: MediaBridgeError) -> HTTPException:
    code = 422
    if exc.code in {"not_found"}:
        code = 404
    elif exc.code in {
        "registry_unreachable",
        "signal_internal_unreachable",
        "registry_presign_failed",
        "registry_download_failed",
        "signal_internal_failed",
    }:
        code = 502
    elif exc.code in {"disk_full"}:
        code = 507
    elif exc.code in {"too_large"}:
        code = 413
    elif exc.code in {"media_fetch_not_configured", "signal_internal_unauthorized"}:
        code = 503
    return HTTPException(code, exc.message)
