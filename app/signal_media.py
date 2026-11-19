"""Fetch Signal/Registry media originals by asset_id (Media SoR Wave B).

Preferred: Media Registry presign → GET bytes.
Fallback: Signal internal dual-read content route (X-Signal-Internal-Token).

Never returns Telegram file_id. No second operator media library.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


class SignalMediaError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class FetchedMedia:
    asset_id: str
    data: bytes
    content_type: str
    source: str  # media_registry | signal_internal
    sha256: str | None = None
    original_filename: str | None = None
    kind: str | None = None


def _registry_configured() -> bool:
    return bool(os.getenv("MEDIA_REGISTRY_URL", "").strip()) and bool(
        os.getenv("MEDIA_REGISTRY_M2M_SECRET", "").strip()
    )


def _signal_internal_configured() -> bool:
    return bool(os.getenv("SIGNAL_INTERNAL_MEDIA_BASE_URL", "").strip()) and bool(
        os.getenv("SIGNAL_INTERNAL_MEDIA_TOKEN", "").strip()
    )


def media_fetch_configured() -> bool:
    return _registry_configured() or _signal_internal_configured()


def _registry_headers() -> dict[str, str]:
    service_id = (os.getenv("MEDIA_REGISTRY_SERVICE_ID") or "voronka").strip()
    secret = (os.getenv("MEDIA_REGISTRY_M2M_SECRET") or "").strip()
    ts = str(int(time.time()))
    sig = hmac.new(
        secret.encode("utf-8"),
        f"{service_id}\n{ts}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Media-Service": service_id,
        "X-Media-Timestamp": ts,
        "X-Media-Signature": sig,
    }


def _kind_from_content_type(ctype: str) -> str:
    c = (ctype or "").split(";")[0].strip().lower()
    if c.startswith("image/"):
        return "photo"
    if c == "application/pdf":
        return "document"
    if c.startswith("video/"):
        return "video"
    if c.startswith("audio/"):
        return "voice"
    return "photo"


def fetch_registry_bytes(asset_id: str, *, profile: str | None = None) -> FetchedMedia | None:
    """Presign then download from Media Registry. Returns None when not configured / 404."""
    if not _registry_configured():
        return None
    base = os.getenv("MEDIA_REGISTRY_URL", "").rstrip("/")
    body: dict[str, Any] = {}
    if profile:
        body["rendition_profile"] = profile
    try:
        with httpx.Client(timeout=30.0) as client:
            meta = None
            try:
                got = client.get(f"{base}/v1/assets/{asset_id}", headers=_registry_headers())
                if got.status_code == 200:
                    meta = got.json()
                elif got.status_code == 404:
                    return None
            except httpx.HTTPError:
                meta = None
            pre = client.post(
                f"{base}/v1/assets/{asset_id}/presign",
                headers=_registry_headers(),
                json=body,
            )
            if pre.status_code == 404:
                return None
            if pre.status_code >= 400:
                raise SignalMediaError(
                    "registry_presign_failed",
                    "Media Registry presign failed",
                    status_code=pre.status_code,
                    body=pre.text,
                )
            url = (pre.json() or {}).get("url")
            if not url:
                return None
            if url.startswith("/"):
                url = f"{base}{url}"
            got = client.get(url)
            if got.status_code >= 400:
                raise SignalMediaError(
                    "registry_download_failed",
                    "Media Registry download failed",
                    status_code=got.status_code,
                    body=got.text[:200],
                )
            ctype = got.headers.get("content-type") or (
                (meta or {}).get("content_type") if meta else None
            ) or "application/octet-stream"
            ctype = ctype.split(";")[0].strip()
            digest = (meta or {}).get("sha256") if meta else None
            if not digest:
                digest = hashlib.sha256(got.content).hexdigest()
            return FetchedMedia(
                asset_id=asset_id,
                data=got.content,
                content_type=ctype,
                source="media_registry",
                sha256=digest,
                original_filename=(meta or {}).get("original_filename") if meta else None,
                kind=_kind_from_content_type(ctype),
            )
    except httpx.HTTPError as exc:
        raise SignalMediaError("registry_unreachable", str(exc)) from exc


def fetch_signal_internal_bytes(asset_id: str, *, profile: str = "original") -> FetchedMedia | None:
    """Dual-read / transitional: GET Signal /api/v1/internal/media/{id}/content."""
    if not _signal_internal_configured():
        return None
    base = os.getenv("SIGNAL_INTERNAL_MEDIA_BASE_URL", "").rstrip("/")
    token = os.getenv("SIGNAL_INTERNAL_MEDIA_TOKEN", "").strip()
    url = f"{base}/internal/media/{asset_id}/content"
    try:
        with httpx.Client(timeout=30.0) as client:
            got = client.get(
                url,
                params={"profile": profile},
                headers={"X-Signal-Internal-Token": token},
            )
            if got.status_code == 404:
                return None
            if got.status_code in {401, 403}:
                raise SignalMediaError(
                    "signal_internal_unauthorized",
                    "Signal internal media token rejected",
                    status_code=got.status_code,
                )
            if got.status_code >= 400:
                raise SignalMediaError(
                    "signal_internal_failed",
                    "Signal internal media fetch failed",
                    status_code=got.status_code,
                    body=got.text[:200],
                )
            ctype = (got.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
            return FetchedMedia(
                asset_id=got.headers.get("X-Media-Asset-Id") or asset_id,
                data=got.content,
                content_type=ctype,
                source=got.headers.get("X-Media-Source") or "signal_internal",
                sha256=hashlib.sha256(got.content).hexdigest(),
                kind=_kind_from_content_type(ctype),
            )
    except httpx.HTTPError as exc:
        raise SignalMediaError("signal_internal_unreachable", str(exc)) from exc


def fetch_asset_bytes(asset_id: str, *, profile: str = "original") -> FetchedMedia:
    """Resolve Registry (preferred) then Signal internal. Raises when neither works."""
    aid = (asset_id or "").strip()
    if not aid:
        raise SignalMediaError("missing_asset_id", "asset_id required")
    if not media_fetch_configured():
        raise SignalMediaError(
            "media_fetch_not_configured",
            "Set MEDIA_REGISTRY_URL(+M2M secret) or SIGNAL_INTERNAL_MEDIA_BASE_URL(+token)",
        )
    fetched = fetch_registry_bytes(aid, profile=None if profile == "original" else profile)
    if fetched is None and profile != "original":
        fetched = fetch_registry_bytes(aid, profile=None)
    if fetched is not None:
        return fetched
    fetched = fetch_signal_internal_bytes(aid, profile=profile)
    if fetched is not None:
        return fetched
    raise SignalMediaError("not_found", f"Asset not found via Registry/Signal: {aid}")


def link_communication_asset(
    *,
    asset_id: str,
    communication_ref: str,
    role: str = "attachment",
    meta_json: str | None = None,
) -> dict[str, Any] | None:
    """Best-effort Registry CommunicationAsset link (MEDIA-008). No-op if Registry unset."""
    if not _registry_configured():
        return None
    base = os.getenv("MEDIA_REGISTRY_URL", "").rstrip("/")
    payload = {
        "asset_id": asset_id,
        "communication_ref": communication_ref,
        "role": role,
    }
    if meta_json:
        payload["meta_json"] = meta_json
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(
                f"{base}/v1/links/communication",
                headers=_registry_headers(),
                json=payload,
            )
            if r.status_code >= 400:
                return None
            return r.json()
    except httpx.HTTPError:
        return None
