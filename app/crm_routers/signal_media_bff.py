"""B-02: CRM staff BFF for Signal media library (list / create / upload).

Staff uses crm_session; Voronka proxies to Signal via SIGNAL_INTERNAL_MEDIA_*
(CART-02 project-assets + B-02 internal create). Originals stay in Signal/Registry —
no second CRM blob SoR.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import Field

from app import media_bridge
from app import signal_media
from app.crm_deps import Body, Session, crm_actor
from app.crm_service import project_access
from app.portals import crm_constructor_square_url, signal_media_library_url


def _signal_open_url(pid: str) -> str:
    """CRM-R10: Signal media SSO deep-link with return_to → same Constructor square/project."""
    return_to = crm_constructor_square_url(project_id=pid)
    return signal_media_library_url(project=pid, return_to=return_to) or (
        f"https://signal.vodopetov.ru/?view=media&project={pid}"
    )

router = APIRouter()


def _http_from_signal(exc: signal_media.SignalMediaError) -> HTTPException:
    # Reuse Wave B mapping + B-02 create/list codes.
    mapped = media_bridge.bridge_http_error(
        media_bridge.MediaBridgeError(exc.code, exc.message, details=exc.body)
    )
    if exc.code in {
        "signal_list_failed",
        "signal_create_failed",
        "signal_upload_failed",
        "brand_required",
        "brand_ambiguous",
        "brand_project_mismatch",
        "content_kind_required",
        "project_required",
        "empty_file",
        "mime_not_allowed",
        "file_too_large",
    }:
        status = exc.status_code or 422
        if status in {401, 403}:
            status = 503
        return HTTPException(status, exc.message)
    return mapped


def _cms_item_from_create(row: dict[str, Any], *, project_id: str) -> dict[str, Any]:
    """Normalize Signal create/upload response to CMS cart item shape."""
    asset_id = str(row.get("id") or row.get("asset_id") or "")
    kind = str(row.get("content_kind") or row.get("kind") or "")
    title = str(row.get("title") or row.get("original_filename") or asset_id)
    preview = row.get("preview_url")
    return {
        "asset_id": asset_id,
        "kind": kind,
        "project_id": str(row.get("voronka_project_id") or project_id),
        "title": title,
        "preview_url": preview,
        "raw": {
            "id": asset_id,
            "content_kind": kind,
            "status": row.get("status"),
            "source": row.get("source"),
        },
    }


class MetaCreateBody(Body):
    content_kind: str = Field(min_length=1, max_length=32)
    title: str = Field(default="", max_length=255)
    body_text: str | None = Field(default=None, max_length=100_000)
    brand_id: str | None = Field(default=None, max_length=36)


@router.get("/projects/{pid}/signal-media")
def list_signal_media(
    pid: str,
    content_kind: str | None = Query(default=None, max_length=32),
    admin=Depends(crm_actor),
):
    """List Signal media for this CRM Project (staff session → Signal M2M)."""
    with Session() as db:
        project_access(db, admin, pid)
    try:
        data = signal_media.list_project_assets(pid, content_kind=content_kind)
    except signal_media.SignalMediaError as exc:
        raise _http_from_signal(exc) from exc
    return {
        **data,
        "project_id": pid,
        "signal_open_url": _signal_open_url(pid),
        "configured": signal_media.media_library_bff_configured(),
    }


@router.get("/projects/{pid}/signal-media/meta")
def signal_media_meta(pid: str, admin=Depends(crm_actor)):
    """Static kinds + Signal deep-link for the embedded library chrome."""
    with Session() as db:
        project_access(db, admin, pid)
    return {
        "project_id": pid,
        "content_kinds": sorted(signal_media.CONTENT_KINDS),
        "content_kind_labels": dict(signal_media.CONTENT_KIND_LABELS_RU),
        "file_required_kinds": sorted(signal_media.FILE_REQUIRED_KINDS),
        "signal_open_url": _signal_open_url(pid),
        "configured": signal_media.media_library_bff_configured(),
    }


@router.post("/projects/{pid}/signal-media/create", status_code=201)
def create_signal_media_meta(pid: str, body: MetaCreateBody, admin=Depends(crm_actor)):
    """Create meta asset (poll / script / email_body / …) bound to this Project."""
    with Session() as db:
        project_access(db, admin, pid)
    try:
        row = signal_media.create_meta_asset(
            voronka_project_id=pid,
            content_kind=body.content_kind,
            title=body.title,
            body_text=body.body_text,
            brand_id=body.brand_id,
        )
    except signal_media.SignalMediaError as exc:
        raise _http_from_signal(exc) from exc
    return _cms_item_from_create(row, project_id=pid)


@router.post("/projects/{pid}/signal-media", status_code=201)
async def upload_signal_media(
    pid: str,
    content_kind: str = Form(...),
    title: str | None = Form(default=None),
    body_text: str | None = Form(default=None),
    brand_id: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    admin=Depends(crm_actor),
):
    """Upload image/file asset or meta-only multipart create for this Project."""
    with Session() as db:
        project_access(db, admin, pid)
    raw = await file.read() if file is not None else b""
    kind = (content_kind or "").strip().lower()
    if not raw:
        try:
            row = signal_media.create_meta_asset(
                voronka_project_id=pid,
                content_kind=kind,
                title=(title or "").strip(),
                body_text=body_text,
                brand_id=brand_id,
            )
        except signal_media.SignalMediaError as exc:
            raise _http_from_signal(exc) from exc
        return _cms_item_from_create(row, project_id=pid)
    try:
        row = signal_media.upload_project_asset(
            voronka_project_id=pid,
            content_kind=kind,
            filename=(file.filename if file else None) or "upload",
            data=raw,
            content_type=file.content_type if file else None,
            title=title,
            body_text=body_text,
            brand_id=brand_id,
        )
    except signal_media.SignalMediaError as exc:
        raise _http_from_signal(exc) from exc
    return _cms_item_from_create(row, project_id=pid)
