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


def media_library_bff_configured() -> bool:
    """True when CRM can list/create Signal media via internal token (B-02)."""
    return _signal_internal_configured()


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


# --- B-02 CRM embedded library (CART-02 Signal project-assets + internal create) ---

CONTENT_KIND_LABELS_RU: dict[str, str] = {
    "tg_post": "Пост Telegram",
    "tg_circle": "Кружочек TG",
    "tg_cover": "Обложка TG",
    "vk_video": "VK video",
    "email_body": "Письмо",
    "poll": "Опрос",
    "script": "Скрипт",
    "image": "Изображение",
    "file": "Файл",
}

CONTENT_KINDS: frozenset[str] = frozenset(CONTENT_KIND_LABELS_RU)
FILE_REQUIRED_KINDS: frozenset[str] = frozenset({"image", "tg_cover", "tg_circle", "file"})


def _signal_api_base() -> str:
    return (os.getenv("SIGNAL_INTERNAL_MEDIA_BASE_URL") or "").rstrip("/")


def _signal_internal_headers() -> dict[str, str]:
    token = (os.getenv("SIGNAL_INTERNAL_MEDIA_TOKEN") or "").strip()
    return {"X-Signal-Internal-Token": token}


def _raise_from_signal_response(resp: httpx.Response, *, fallback_code: str) -> None:
    detail: Any = None
    try:
        detail = resp.json()
    except Exception:
        detail = (resp.text or "")[:300]
    message = fallback_code
    code = fallback_code
    if isinstance(detail, dict):
        inner = detail.get("detail") if "detail" in detail else detail
        if isinstance(inner, dict):
            code = str(inner.get("code") or fallback_code)
            message = str(inner.get("message") or code)
        elif isinstance(inner, str):
            message = inner
    elif isinstance(detail, str) and detail:
        message = detail
    raise SignalMediaError(code, message, status_code=resp.status_code, body=detail)


def list_project_assets(
    voronka_project_id: str,
    *,
    content_kind: str | None = None,
) -> dict[str, Any]:
    """CART-02 M2M: GET /internal/media/project-assets (Signal SoR catalog)."""
    if not _signal_internal_configured():
        raise SignalMediaError(
            "media_fetch_not_configured",
            "Set SIGNAL_INTERNAL_MEDIA_BASE_URL + SIGNAL_INTERNAL_MEDIA_TOKEN for CRM медиатека",
        )
    project_id = (voronka_project_id or "").strip()
    if not project_id:
        raise SignalMediaError("project_required", "voronka_project_id required")
    params: dict[str, str] = {"voronka_project_id": project_id}
    kind = (content_kind or "").strip().lower()
    if kind:
        params["content_kind"] = kind
    url = f"{_signal_api_base()}/internal/media/project-assets"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url, params=params, headers=_signal_internal_headers())
    except httpx.HTTPError as exc:
        raise SignalMediaError("signal_internal_unreachable", str(exc)) from exc
    if resp.status_code in {401, 403}:
        raise SignalMediaError(
            "signal_internal_unauthorized",
            "Signal internal media token rejected",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        _raise_from_signal_response(resp, fallback_code="signal_list_failed")
    data = resp.json() if resp.content else {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        items = []
    return {
        "voronka_project_id": project_id,
        "items": items,
        "content_kinds": sorted(CONTENT_KINDS),
        "content_kind_labels": dict(CONTENT_KIND_LABELS_RU),
        "source": "signal_internal",
    }


def create_meta_asset(
    *,
    voronka_project_id: str,
    content_kind: str,
    title: str = "",
    body_text: str | None = None,
    brand_id: str | None = None,
) -> dict[str, Any]:
    """B-02: POST /internal/media/create (meta kinds: poll/script/email/…)."""
    if not _signal_internal_configured():
        raise SignalMediaError(
            "media_fetch_not_configured",
            "Set SIGNAL_INTERNAL_MEDIA_BASE_URL + SIGNAL_INTERNAL_MEDIA_TOKEN for CRM медиатека",
        )
    project_id = (voronka_project_id or "").strip()
    kind = (content_kind or "").strip().lower()
    if not project_id:
        raise SignalMediaError("project_required", "voronka_project_id required")
    if kind not in CONTENT_KINDS:
        raise SignalMediaError("content_kind_required", f"Unknown content_kind: {kind}")
    payload: dict[str, Any] = {
        "voronka_project_id": project_id,
        "content_kind": kind,
        "title": (title or "").strip()[:255],
    }
    if body_text is not None:
        payload["body_text"] = body_text
    bid = (brand_id or os.getenv("SIGNAL_MEDIA_DEFAULT_BRAND_ID") or "").strip()
    if bid:
        payload["brand_id"] = bid
    url = f"{_signal_api_base()}/internal/media/create"
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, headers=_signal_internal_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise SignalMediaError("signal_internal_unreachable", str(exc)) from exc
    if resp.status_code in {401, 403}:
        raise SignalMediaError(
            "signal_internal_unauthorized",
            "Signal internal media token rejected",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        _raise_from_signal_response(resp, fallback_code="signal_create_failed")
    return resp.json() if resp.content else {}


def upload_project_asset(
    *,
    voronka_project_id: str,
    content_kind: str,
    filename: str,
    data: bytes,
    content_type: str | None = None,
    title: str | None = None,
    body_text: str | None = None,
    brand_id: str | None = None,
) -> dict[str, Any]:
    """B-02: POST /internal/media multipart (images + Project bind)."""
    if not _signal_internal_configured():
        raise SignalMediaError(
            "media_fetch_not_configured",
            "Set SIGNAL_INTERNAL_MEDIA_BASE_URL + SIGNAL_INTERNAL_MEDIA_TOKEN for CRM медиатека",
        )
    project_id = (voronka_project_id or "").strip()
    kind = (content_kind or "").strip().lower() or "image"
    if not project_id:
        raise SignalMediaError("project_required", "voronka_project_id required")
    if kind not in CONTENT_KINDS:
        raise SignalMediaError("content_kind_required", f"Unknown content_kind: {kind}")
    if not data:
        raise SignalMediaError("empty_file", "Empty upload")
    form: dict[str, str] = {
        "voronka_project_id": project_id,
        "content_kind": kind,
    }
    if title:
        form["title"] = title.strip()[:255]
    if body_text is not None:
        form["body_text"] = body_text
    bid = (brand_id or os.getenv("SIGNAL_MEDIA_DEFAULT_BRAND_ID") or "").strip()
    if bid:
        form["brand_id"] = bid
    ctype = (content_type or "application/octet-stream").split(";")[0].strip()
    files = {"file": (filename or "upload", data, ctype)}
    url = f"{_signal_api_base()}/internal/media"
    try:
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(url, headers=_signal_internal_headers(), data=form, files=files)
    except httpx.HTTPError as exc:
        raise SignalMediaError("signal_internal_unreachable", str(exc)) from exc
    if resp.status_code in {401, 403}:
        raise SignalMediaError(
            "signal_internal_unauthorized",
            "Signal internal media token rejected",
            status_code=resp.status_code,
        )
    if resp.status_code >= 400:
        _raise_from_signal_response(resp, fallback_code="signal_upload_failed")
    return resp.json() if resp.content else {}
