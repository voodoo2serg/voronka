"""Scoped M2M communications API for Signal (SIG-4).

Auth: HMAC wire v1 via app.m2m_signal — NOT crm_session.
Contract: docs/M2M_COMMUNICATIONS_OPENAPI.yaml (+ signal docs/VORONKA_M2M_OPENAPI.yaml).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select, func

from app.capabilities import platform_capabilities
from app.crm_deps import Body, Session, encode, must
from app.crm_models import Campaign, CampaignRecipient, Profile, IntegrationInbox
from app.crm_service import uid, available_identity, ensure_profile
from app.db import Connection, Outbox, Asset, Identity, Event, Funnel, Version, Run, Project, now
from app import media as media_mod
from app.graph import graph_runtime_ready, validate_bindings, validate_published_graph
from app.m2m_signal import (
    SignalM2MPrincipal,
    assert_project_allowed,
    make_m2m_dep,
)
from app.crm_routers.campaigns import _asset_blocker

router = APIRouter(prefix="/m2m", tags=["M2M Communications"])

M2M_ACTOR_ID = -900_001  # synthetic audit actor for Signal service


class M2MCampaignCreate(Body):
    connection_id: str
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    profile_ids: list[str] = Field(default_factory=list, max_length=1000)
    identity_ids: list[str] = Field(default_factory=list, max_length=1000)
    asset_id: str | None = None
    external_idempotency_key: str = Field(min_length=8, max_length=120)


class AssetImportBody(Body):
    name: str = Field(min_length=1, max_length=160)
    kind: str
    connection_ids: list[str] = Field(min_length=1)
    content_base64: str
    sha256: str = Field(min_length=64, max_length=64)
    content_type: str | None = None


class M2MSegmentExport(Body):
    """CAMP-101: paginated sendable Person/ContactPoint export for Signal mass campaigns.

    When only_sendable=true (default), each candidate must pass send_allowed with
    missing_policy=deny for the requested purpose (marketing for mass email).

    Defined above the route that uses it — FastAPI resolves annotations at import.
    """

    channel: str = Field(default="email", min_length=1, max_length=30)
    purpose: str = Field(default="marketing", max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    missing_policy: str = Field(default="deny", max_length=20)
    only_sendable: bool = True
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


def _m2m_audit(db, principal: SignalM2MPrincipal, kind: str, **payload: Any) -> None:
    db.add(
        Event(
            id=uid(),
            actor_id=M2M_ACTOR_ID,
            kind=f"m2m.{kind}",
            payload={"service": principal.service_id, "jti": principal.jti, **payload},
        )
    )


def _resolve_profile_ids(db, pid: str, body: M2MCampaignCreate) -> list[str]:
    ids = list(body.profile_ids or [])
    for iid in body.identity_ids or []:
        identity = db.get(Identity, iid)
        if not identity:
            raise HTTPException(404, f"Identity не найден: {iid}")
        if identity.connection_id != body.connection_id:
            raise HTTPException(422, "Identity другого подключения")
        profile = ensure_profile(db, identity, pid)
        ids.append(profile.id)
    unique = list(dict.fromkeys(ids))
    if not unique:
        raise HTTPException(422, "Укажите profile_ids или identity_ids")
    if len(unique) > 1000:
        raise HTTPException(422, "Аудитория: не больше 1000")
    return unique


def _campaign_public(row: Campaign) -> dict:
    data = encode(row)
    # Never leak internal-only keys beyond what's needed
    return data


def _find_by_idempotency(db, pid: str, key: str) -> Campaign | None:
    inbox = db.scalar(
        select(IntegrationInbox).where(
            IntegrationInbox.source == "signal",
            IntegrationInbox.event_key == f"campaign.create:{key}",
        )
    )
    if not inbox:
        return None
    cid = (inbox.payload or {}).get("campaign_id")
    if not cid:
        return None
    row = db.get(Campaign, cid)
    if row and row.project_id == pid:
        return row
    return None


@router.get("/projects/{pid}/connections/hub")
async def m2m_connections_hub(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("connection:hub:read")),
):
    _body, principal = auth_ctx
    assert_project_allowed(pid)
    with Session() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        root = media_mod.ROOT
        root.mkdir(parents=True, exist_ok=True)
        media_writable = os.access(root, os.W_OK)
        free = shutil.disk_usage(root).free
        rows = []
        for connection in db.scalars(select(Connection).where(Connection.project_id == pid)):
            token_present = bool(connection.token_env and os.environ.get(connection.token_env))
            secret_present = bool(connection.secret_env and os.environ.get(connection.secret_env))
            failed = db.scalar(
                select(func.count()).select_from(Outbox).where(
                    Outbox.connection_id == connection.id, Outbox.status.in_(["failed", "unknown"])
                )
            ) or 0
            pending = db.scalar(
                select(func.count()).select_from(Outbox).where(
                    Outbox.connection_id == connection.id, Outbox.status == "pending"
                )
            ) or 0
            rows.append(
                {
                    "id": connection.id,
                    "name": connection.name,
                    "platform": connection.platform,
                    "role": connection.role,
                    "active": connection.active,
                    # Presence only — never env names or values for M2M
                    "token_present": token_present,
                    "secret_present": secret_present,
                    "webhook_preflight": {
                        "ok": bool(
                            connection.active
                            and (connection.platform == "email" or (token_present and secret_present))
                        ),
                        "active": connection.active,
                        "credentials_present": connection.platform == "email"
                        or (token_present and secret_present),
                    },
                    "media_writable": media_writable,
                    "media_free_bytes": free,
                    "outbox_failed_or_unknown": failed,
                    "outbox_pending": pending,
                    "capabilities": platform_capabilities(connection.platform),
                }
            )
        return {"items": rows, "media_writable": media_writable, "media_free_bytes": free}


@router.get("/projects/{pid}/media-assets")
async def m2m_media_assets(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("asset:read")),
):
    _body, _principal = auth_ctx
    assert_project_allowed(pid)
    with Session() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        connection_ids = {
            c.id for c in db.scalars(select(Connection).where(Connection.project_id == pid))
        }
        items = []
        for asset in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(200)):
            overlap = [cid for cid in (asset.connection_ids or []) if cid in connection_ids]
            if not overlap and asset.connection_ids:
                continue
            blocker = None
            if not asset.local_path and not asset.telegram_refs:
                blocker = "Нет локального файла и file_id — рассылка невозможна"
            items.append(
                {
                    "id": asset.id,
                    "name": asset.name,
                    "kind": asset.kind,
                    "mime": asset.mime,
                    "has_local_file": bool(asset.local_path),
                    "connection_ids": overlap,
                    "blocker": blocker,
                    "sha256": (asset.metadata_json or {}).get("sha256"),
                    "created_at": asset.created_at,
                    # intentionally omit telegram_refs / file_id maps
                }
            )
        return {"items": items}


@router.post("/projects/{pid}/assets/import")
async def m2m_asset_import(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("asset:import")),
):
    body_raw, principal = auth_ctx
    assert_project_allowed(pid)
    try:
        payload = AssetImportBody.model_validate_json(body_raw)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    if payload.kind not in {"photo", "document", "video", "video_note", "voice"}:
        raise HTTPException(422, "Неверный тип")
    try:
        raw = base64.b64decode(payload.content_base64, validate=True)
    except Exception as exc:
        raise HTTPException(422, "content_base64 некорректен") from exc
    digest = hashlib.sha256(raw).hexdigest()
    if digest != payload.sha256.lower():
        raise HTTPException(422, "checksum mismatch")
    max_size = 10_000_000 if payload.kind == "photo" else 50_000_000
    if len(raw) > max_size:
        raise HTTPException(413, "Размер превышает лимит")
    if not raw:
        raise HTTPException(422, "Пустой файл")

    with Session.begin() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        for cid in payload.connection_ids:
            connection = db.get(Connection, cid)
            if not connection or connection.project_id != pid:
                raise HTTPException(404, "Подключение недоступно")
        # Idempotent import by sha256 within project connections
        for asset in db.scalars(select(Asset).order_by(Asset.created_at.desc()).limit(500)):
            meta = asset.metadata_json or {}
            if meta.get("sha256") == digest and meta.get("signal_import"):
                overlap = set(asset.connection_ids or []) & set(payload.connection_ids)
                if overlap or not asset.connection_ids:
                    return {
                        "id": asset.id,
                        "name": asset.name,
                        "kind": asset.kind,
                        "mime": asset.mime,
                        "duplicate": True,
                        "sha256": digest,
                    }

        media_mod.ROOT.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(media_mod.ROOT).free < media_mod.MIN_FREE:
            raise HTTPException(507, "Недостаточно места на диске")
        asset_id = uid()
        extension = {
            "video": ".mp4",
            "video_note": ".mp4",
            "photo": ".img",
            "voice": ".audio",
            "document": ".pdf",
        }[payload.kind]
        path = media_mod.ROOT / (asset_id + extension)
        try:
            path.write_bytes(raw)
            mime, metadata = media_mod.inspect_file(path, payload.kind)
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
            asset = Asset(
                id=asset_id,
                name=payload.name,
                kind=payload.kind,
                mime=mime,
                local_path=path.name,
                connection_ids=list(payload.connection_ids),
                telegram_refs={},
                metadata_json={**metadata, "size": len(raw), "sha256": digest, "signal_import": True},
            )
            db.add(asset)
            db.flush()
            _m2m_audit(db, principal, "asset.imported", asset_id=asset.id, sha256=digest, project_id=pid)
            return {
                "id": asset.id,
                "name": asset.name,
                "kind": asset.kind,
                "mime": asset.mime,
                "duplicate": False,
                "sha256": digest,
            }
        except Exception:
            path.unlink(missing_ok=True)
            raise


@router.get("/projects/{pid}/contacts")
async def m2m_contacts(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("contact:segment:read")),
    tag: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    _body, _principal = auth_ctx
    assert_project_allowed(pid)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with Session() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        q = select(Profile).where(Profile.project_id == pid).order_by(Profile.created_at.desc())
        profiles = list(db.scalars(q.offset(offset).limit(limit + (50 if tag else 0))))
        items = []
        for profile in profiles:
            tags = profile.tags or []
            if tag and tag not in tags:
                continue
            identities = list(
                db.scalars(select(Identity).where(Identity.contact_id == profile.contact_id))
            )
            identity_refs = []
            for identity in identities:
                connection = db.get(Connection, identity.connection_id)
                if not connection or connection.project_id != pid:
                    continue
                identity_refs.append(
                    {
                        "identity_id": identity.id,
                        "connection_id": identity.connection_id,
                        "platform": connection.platform,
                    }
                )
            items.append(
                {
                    "id": profile.id,
                    "name": profile.name,
                    "tags": tags,
                    "stopped": profile.stopped,
                    "source": profile.source,
                    "identity_refs": identity_refs,
                }
            )
            if len(items) >= limit:
                break
        return {"items": items, "limit": limit, "offset": offset}


@router.post("/projects/{pid}/segments/export")
def m2m_segment_export(
    pid: str,
    body: M2MSegmentExport,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("contact:segment:read")),
):
    """CAMP-101: export sendable segment (Person + ContactPoint) for Signal dry-run/plan.

    Unlike GET /contacts (profile/tag projection only), this returns person_id,
    contact_point_id and channel address for candidates that pass send_allowed.
    """
    from app.crm_models import ContactPoint, Person
    from app import person_graph as graph

    _body, principal = auth_ctx
    assert_project_allowed(pid)
    if body.purpose not in {"marketing", "transactional", "service", "analytics"}:
        raise HTTPException(422, "Неизвестная цель согласия")
    if body.missing_policy not in {"soft_allow", "deny"}:
        raise HTTPException(422, "Неизвестный missing_policy")
    if body.channel not in {"email", "telegram", "phone", "whatsapp", "max"}:
        raise HTTPException(422, "Неизвестный канал")

    excluded = 0
    sendable_index = 0
    items: list[dict[str, Any]] = []
    with Session() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")

        # Walk Profiles with a linked Person; optional tag filter.
        profiles = list(
            db.scalars(
                select(Profile)
                .where(Profile.project_id == pid, Profile.person_id.is_not(None))
                .order_by(Profile.created_at.desc())
            )
        )
        seen_cp: set[str] = set()
        for profile in profiles:
            tags = profile.tags or []
            if body.tag and body.tag not in tags:
                continue
            if profile.stopped and body.purpose == "marketing" and body.only_sendable:
                excluded += 1
                continue
            person = db.get(Person, profile.person_id)
            if person is None or person.lifecycle_stage == "merged":
                excluded += 1
                continue
            points = list(
                db.scalars(
                    select(ContactPoint).where(
                        ContactPoint.person_id == person.id,
                        ContactPoint.channel == body.channel,
                    )
                )
            )
            if not points:
                excluded += 1
                continue
            for cp in points:
                if cp.id in seen_cp:
                    continue
                seen_cp.add(cp.id)
                reason = "ok"
                if body.only_sendable:
                    allowed, reason = graph.send_allowed(
                        db,
                        person_id=person.id,
                        contact_point_id=cp.id,
                        channel=body.channel,
                        purpose=body.purpose,
                        missing_policy=body.missing_policy,
                    )
                    if not allowed:
                        excluded += 1
                        continue
                # Paginate over sendable (or unfiltered) candidates only.
                if sendable_index < body.offset:
                    sendable_index += 1
                    continue
                sendable_index += 1
                items.append(
                    {
                        "person_id": person.id,
                        "contact_point_id": cp.id,
                        "channel": cp.channel,
                        "address": cp.normalized_value,
                        "verified": bool(cp.verified),
                        "reachable": bool(cp.reachable),
                        "display_name": person.display_name or profile.name,
                        "profile_id": profile.id,
                        "tags": tags,
                        "send_allowed": True if body.only_sendable else None,
                        "send_reason": reason if body.only_sendable else None,
                    }
                )
                if len(items) >= body.limit:
                    break
            if len(items) >= body.limit:
                break

        _m2m_audit(
            db,
            principal,
            "segment.export",
            project_id=pid,
            channel=body.channel,
            purpose=body.purpose,
            tag=body.tag,
            returned=len(items),
            excluded=excluded,
            offset=body.offset,
            limit=body.limit,
        )
        db.commit()
        return {
            "project_id": pid,
            "channel": body.channel,
            "purpose": body.purpose,
            "tag": body.tag,
            "only_sendable": body.only_sendable,
            "missing_policy": body.missing_policy,
            "items": items,
            "count": len(items),
            "excluded": excluded,
            "limit": body.limit,
            "offset": body.offset,
            "has_more": len(items) >= body.limit,
            "sor": "voronka",
        }


@router.post("/projects/{pid}/campaigns")
async def m2m_create_campaign(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("campaign:create")),
):
    body_raw, principal = auth_ctx
    assert_project_allowed(pid)
    try:
        body = M2MCampaignCreate.model_validate_json(body_raw)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc

    with Session.begin() as db:
        from app.db import Project

        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        prior = _find_by_idempotency(db, pid, body.external_idempotency_key)
        if prior:
            out = _campaign_public(prior)
            out["idempotent_replay"] = True
            return out

        connection = must(db.get(Connection, body.connection_id))
        if connection.project_id != pid:
            raise HTTPException(404, "Подключение недоступно")
        profile_ids = _resolve_profile_ids(db, pid, body)
        blocker = _asset_blocker(db, connection, body.asset_id)
        report = []
        for profile_id in profile_ids:
            profile = db.get(Profile, profile_id)
            if not profile or profile.project_id != pid:
                raise HTTPException(404, "Клиент другого проекта")
            identity, reason = available_identity(db, profile, connection.id)
            report.append(
                {
                    "profile_id": profile_id,
                    "eligible": identity is not None and not blocker,
                    "reason": blocker or reason,
                }
            )
        payload = body.model_dump()
        payload["profile_ids"] = profile_ids
        payload.pop("identity_ids", None)
        external_key = payload.pop("external_idempotency_key")
        row = Campaign(
            id=uid(),
            project_id=pid,
            connection_id=payload["connection_id"],
            title=payload["title"],
            text=payload["text"],
            asset_id=payload.get("asset_id"),
            profile_ids=profile_ids,
            report={
                "recipients": report,
                "asset_blocker": blocker,
                "external_idempotency_key": external_key,
                "source": "signal_m2m",
            },
        )
        db.add(row)
        db.flush()
        db.add(
            IntegrationInbox(
                id=uid(),
                source="signal",
                event_key=f"campaign.create:{external_key}",
                kind="campaign.create",
                payload={"campaign_id": row.id, "project_id": pid},
                state="applied",
                created_at=now(),
            )
        )
        _m2m_audit(
            db,
            principal,
            "campaign.created",
            campaign_id=row.id,
            project_id=pid,
            external_idempotency_key=external_key,
        )
        out = _campaign_public(row)
        out["idempotent_replay"] = False
        return out


@router.post("/campaigns/{cid}/preflight")
async def m2m_preflight(
    cid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("campaign:preflight")),
):
    _body, _principal = auth_ctx
    with Session() as db:
        campaign = must(db.get(Campaign, cid))
        assert_project_allowed(campaign.project_id)
        connection = must(db.get(Connection, campaign.connection_id))
        blocker = _asset_blocker(db, connection, campaign.asset_id)
        media_mod.ROOT.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(media_mod.ROOT).free
        recipients = []
        for profile_id in set(campaign.profile_ids or []):
            profile = db.get(Profile, profile_id)
            if not profile or profile.project_id != campaign.project_id:
                recipients.append({"profile_id": profile_id, "ok": False, "reason": "Клиент недоступен"})
                continue
            identity, reason = available_identity(db, profile, connection.id)
            recipients.append(
                {
                    "profile_id": profile_id,
                    "ok": identity is not None and not blocker and not profile.stopped,
                    "reason": blocker or reason,
                    "stopped": profile.stopped,
                }
            )
        return {
            "connection_alive": bool(connection.active),
            "credentials_present": connection.platform == "email"
            or bool(os.environ.get(connection.token_env)),
            "asset_blocker": blocker,
            "media_free_bytes": free,
            "capabilities": platform_capabilities(connection.platform),
            "rate_limit_per_second": int(os.getenv("BOT_MESSAGES_PER_SECOND", "20")),
            "recipients": recipients,
            "ready": connection.active and not blocker and any(r["ok"] for r in recipients),
            "campaign_id": cid,
            "state": campaign.state,
        }


@router.post("/campaigns/{cid}/launch")
async def m2m_launch(
    cid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("campaign:launch")),
):
    _body, principal = auth_ctx
    with Session.begin() as db:
        campaign = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        assert_project_allowed(campaign.project_id)
        if campaign.state != "draft":
            return {"state": campaign.state, **(campaign.report or {}), "idempotent_replay": True}
        connection = must(db.get(Connection, campaign.connection_id))
        if not connection.active:
            raise HTTPException(409, "Подключение выключено")
        blocker = _asset_blocker(db, connection, campaign.asset_id)
        if blocker:
            raise HTTPException(409, blocker)
        asset = db.get(Asset, campaign.asset_id) if campaign.asset_id else None
        sent, excluded = 0, []
        for profile_id in set(campaign.profile_ids or []):
            profile = db.get(Profile, profile_id)
            if not profile or profile.project_id != campaign.project_id:
                excluded.append({"profile_id": profile_id, "reason": "Клиент недоступен"})
                continue
            identity, reason = available_identity(db, profile, campaign.connection_id)
            if not identity:
                excluded.append({"profile_id": profile_id, "reason": reason})
                continue
            payload = {
                "type": asset.kind if asset else "text",
                "subject": campaign.title,
                "text": campaign.text.replace("{name}", profile.name),
                "crm_campaign_id": cid,
                "crm_profile_id": profile.id,
                "source": "signal_m2m",
            }
            if asset:
                payload["asset_id"] = asset.id
            out = Outbox(
                id=uid(),
                connection_id=identity.connection_id,
                identity_id=getattr(identity, "id", None),
                chat_id=identity.chat_id,
                payload=payload,
            )
            db.add(out)
            db.flush()
            db.add(CampaignRecipient(id=uid(), campaign_id=cid, profile_id=profile_id, outbox_id=out.id))
            sent += 1
        campaign.state = "queued"
        campaign.report = {
            **(campaign.report or {}),
            "queued": sent,
            "excluded": excluded,
            "asset_id": campaign.asset_id,
        }
        _m2m_audit(db, principal, "campaign.launched", campaign_id=cid, queued=sent)
        return {**campaign.report, "state": campaign.state, "idempotent_replay": False}


@router.post("/campaigns/{cid}/cancel")
async def m2m_cancel(
    cid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("campaign:cancel")),
):
    _body, principal = auth_ctx
    with Session.begin() as db:
        row = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        assert_project_allowed(row.project_id)
        row.state = "cancelled"
        for recipient in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)):
            out = db.get(Outbox, recipient.outbox_id)
            if out and out.status == "pending":
                out.status = "cancelled"
        _m2m_audit(db, principal, "campaign.cancelled", campaign_id=cid)
        return {"state": row.state}


@router.get("/campaigns/{cid}/delivery")
async def m2m_delivery(
    cid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("campaign:read")),
):
    _body, _principal = auth_ctx
    with Session() as db:
        campaign = must(db.get(Campaign, cid))
        assert_project_allowed(campaign.project_id)
        states: dict[str, int] = {}
        problems = []
        for recipient, status, error in db.execute(
            select(CampaignRecipient.profile_id, Outbox.status, Outbox.error)
            .join(Outbox, CampaignRecipient.outbox_id == Outbox.id)
            .where(CampaignRecipient.campaign_id == campaign.id)
        ):
            states[status] = states.get(status, 0) + 1
            if status in {"failed", "unknown"}:
                problems.append({"profile_id": recipient, "status": status, "error": error})
        # Honest aggregate — never claim delivered from queued alone
        projection = "queued"
        if campaign.state == "cancelled":
            projection = "cancelled"
        elif states.get("failed") or states.get("unknown"):
            if states.get("sent") or states.get("pending"):
                projection = "partial"
            else:
                projection = "failed"
        elif states.get("sent") and not states.get("pending") and not states.get("held"):
            projection = "accepted"
        elif states.get("pending") or states.get("held") or campaign.state == "queued":
            projection = "queued"
        return {
            "campaign_id": cid,
            "state": campaign.state,
            "delivery": states,
            "problems": problems,
            "projection": projection,
            "external_idempotency_key": (campaign.report or {}).get("external_idempotency_key"),
        }


# --- Wave C additive M2M (existing campaign/asset/hub contracts unchanged) ---


@router.get("/projects/{pid}/persons/{person_id}")
def m2m_person(
    pid: str,
    person_id: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("person:read")),
):
    from app.crm_models import ContactPoint, Person, PersonLink

    _body, principal = auth_ctx
    assert_project_allowed(pid)
    with Session() as db:
        person = must(db.get(Person, person_id))
        if person.merged_into_id:
            person = must(db.get(Person, person.merged_into_id))
        profiles = list(
            db.scalars(select(Profile).where(Profile.person_id == person.id, Profile.project_id == pid))
        )
        if not profiles:
            raise HTTPException(404, "Person не в проекте")
        points = list(db.scalars(select(ContactPoint).where(ContactPoint.person_id == person.id)))
        links = list(db.scalars(select(PersonLink).where(PersonLink.person_id == person.id)))
        _m2m_audit(db, principal, "person.read", person_id=person.id, project_id=pid)
        return {
            "person": encode(person),
            "contact_points": [encode(p) for p in points],
            "links": [encode(l) for l in links],
            "profile_ids": [p.id for p in profiles],
        }


@router.get("/projects/{pid}/persons/{person_id}/timeline")
def m2m_timeline(
    pid: str,
    person_id: str,
    request: Request,
    limit: int = 50,
    auth_ctx=Depends(make_m2m_dep("timeline:read")),
):
    from app.crm_models import Interaction, Order, Payment, Agreement

    _body, principal = auth_ctx
    assert_project_allowed(pid)
    if not 1 <= limit <= 200:
        raise HTTPException(422, "limit 1..200")
    with Session() as db:
        profiles = list(
            db.scalars(select(Profile).where(Profile.person_id == person_id, Profile.project_id == pid))
        )
        if not profiles:
            raise HTTPException(404, "Person не в проекте")
        items = []
        for ix in db.scalars(
            select(Interaction)
            .where(Interaction.person_id == person_id)
            .order_by(Interaction.occurred_at.desc())
            .limit(limit)
        ):
            items.append({"type": "interaction", **encode(ix)})
        for profile in profiles:
            for order in db.scalars(select(Order).where(Order.profile_id == profile.id)):
                items.append({"type": "order", **encode(order)})
                for pay in db.scalars(select(Payment).where(Payment.order_id == order.id)):
                    items.append({"type": "payment", **encode(pay)})
        for ag in db.scalars(
            select(Agreement).where(Agreement.person_id == person_id, Agreement.project_id == pid)
        ):
            items.append({"type": "agreement", **encode(ag)})
        _m2m_audit(db, principal, "timeline.read", person_id=person_id, project_id=pid)
        return {"person_id": person_id, "project_id": pid, "items": items[:limit]}


class M2MSendPolicyCheck(Body):
    channel: str = Field(min_length=1, max_length=30)
    purpose: str = Field(default="marketing", max_length=40)
    person_id: str | None = None
    contact_point_id: str | None = None
    connection_id: str | None = None
    missing_policy: str = Field(default="deny", max_length=20)
    # Signal transactional path often only has the mailbox address.
    email: str | None = Field(default=None, max_length=250)


class M2MCommunicationIngest(Body):
    """CJ-108: Signal CE writeback — Communication + DeliveryEvent + Interaction.

    Never accept raw HTML / full email bodies. body_preview is capped at 500 chars.
    """

    channel: str = Field(default="email", min_length=1, max_length=30)
    purpose: str = Field(default="transactional", max_length=40)
    status: str = Field(min_length=1, max_length=30)  # queued | accepted | failed
    external_event_key: str = Field(min_length=8, max_length=160)
    # Stable key grouping queued→accepted for one CE event (defaults from external_event_key).
    communication_key: str | None = Field(default=None, max_length=160)
    person_id: str | None = None
    email: str | None = Field(default=None, max_length=250)
    kind: str = Field(default="email.send", max_length=60)
    body_preview: str = Field(default="", max_length=500)
    evidence_ref: str = Field(default="", max_length=200)
    message_id: str | None = Field(default=None, max_length=160)


_STATUS_SUFFIXES = (":queued", ":accepted", ":failed", ":delivered")


def _default_communication_key(external_event_key: str, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    key = external_event_key.strip()
    for suffix in _STATUS_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)]
    return key


def _reject_html_preview(preview: str) -> str:
    text = (preview or "").strip()
    if "<" in text and ">" in text:
        raise HTTPException(422, "body_preview must not contain HTML")
    return text[:500]


def _resolve_person_for_project(
    db,
    pid: str,
    *,
    person_id: str | None,
    email: str | None,
) -> tuple[str, str | None]:
    """Return (person_id, contact_point_id). Ambiguous email → 422."""
    from app.crm_models import ContactPoint, Person
    from app import person_graph as graph
    from app.crm_service import normalize_email

    contact_point_id: str | None = None
    if person_id:
        person = must(db.get(Person, person_id))
        if person.merged_into_id:
            person = must(db.get(Person, person.merged_into_id))
            person_id = person.id
        profiles = list(
            db.scalars(select(Profile).where(Profile.person_id == person_id, Profile.project_id == pid))
        )
        if not profiles:
            raise HTTPException(404, "Person не в проекте")
        return person_id, None

    if not email:
        raise HTTPException(422, "person_id или email обязателен")
    try:
        normalized = normalize_email(email)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    digest = graph.address_hash("email", normalized)
    project_matches: list = []
    for cp in db.scalars(
        select(ContactPoint).where(
            ContactPoint.channel == "email",
            ContactPoint.address_hash == digest,
        )
    ):
        profiles = list(
            db.scalars(
                select(Profile).where(Profile.person_id == cp.person_id, Profile.project_id == pid)
            )
        )
        if profiles:
            project_matches.append(cp)
    unique_persons = {cp.person_id for cp in project_matches}
    if len(unique_persons) > 1:
        raise HTTPException(422, "ambiguous_email")
    if len(project_matches) != 1:
        raise HTTPException(404, "Person не найден по email в проекте")
    cp = project_matches[0]
    person = must(db.get(Person, cp.person_id))
    if person.merged_into_id:
        person = must(db.get(Person, person.merged_into_id))
    return person.id, cp.id


@router.post("/projects/{pid}/communications/ingest")
async def m2m_communications_ingest(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("timeline:write")),
):
    """CJ-108: ingest Signal CE queued/accepted/failed into Person timeline.

    Idempotent on external_event_key (DeliveryEvent + Interaction) and
    communication_key (Communication row via IntegrationInbox).
    """
    from app.crm_models import Communication
    from app import person_graph as graph
    from app.db import Project

    body_raw, principal = auth_ctx
    assert_project_allowed(pid)
    try:
        body = M2MCommunicationIngest.model_validate_json(body_raw)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.status not in {"queued", "accepted", "failed"}:
        raise HTTPException(422, "status must be queued|accepted|failed")
    if body.purpose not in {"marketing", "transactional", "service", "analytics"}:
        raise HTTPException(422, "Неизвестная цель")
    preview = _reject_html_preview(body.body_preview)
    communication_key = _default_communication_key(body.external_event_key, body.communication_key)

    with Session.begin() as db:
        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        person_id, contact_point_id = _resolve_person_for_project(
            db, pid, person_id=body.person_id, email=body.email
        )

        inbox_key = f"communication.ingest:{communication_key}"
        inbox = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == "signal",
                IntegrationInbox.event_key == inbox_key,
            )
        )
        communication: Communication | None = None
        created_communication = False
        if inbox:
            cid = (inbox.payload or {}).get("communication_id")
            if cid:
                communication = db.get(Communication, cid)
                if communication and communication.project_id and communication.project_id != pid:
                    raise HTTPException(403, "Communication другого проекта")

        if not communication:
            communication = graph.create_communication(
                db,
                person_id=person_id,
                purpose=body.purpose,
                channel=body.channel,
                contact_point_id=contact_point_id,
                project_id=pid,
                body_preview=preview,
                status=body.status if body.status == "queued" else "queued",
            )
            if body.message_id:
                communication.provider_message_id = body.message_id
            created_communication = True
            db.add(
                IntegrationInbox(
                    id=uid(),
                    source="signal",
                    event_key=inbox_key,
                    kind="communication.ingest",
                    payload={
                        "communication_id": communication.id,
                        "project_id": pid,
                        "communication_key": communication_key,
                    },
                    state="applied",
                    created_at=now(),
                )
            )
        elif preview and not communication.body_preview:
            communication.body_preview = preview
        if body.message_id and not communication.provider_message_id:
            communication.provider_message_id = body.message_id

        delivery, delivery_dup = graph.record_delivery_event(
            db,
            communication.id,
            normalized_status=body.status,
            platform_status=body.status,
            evidence_ref=body.evidence_ref or (body.message_id or ""),
            external_event_key=body.external_event_key,
            payload={"communication_key": communication_key, "source": "signal"},
        )
        interaction, interaction_dup = graph.record_interaction(
            db,
            person_id=person_id,
            kind=body.kind,
            direction="outbound",
            channel=body.channel,
            project_id=pid,
            contact_point_id=contact_point_id,
            communication_id=communication.id,
            source_service="signal",
            external_event_key=body.external_event_key,
            payload={
                "status": body.status,
                "communication_key": communication_key,
                "purpose": body.purpose,
            },
            evidence_status=body.status,
        )
        duplicate = (not created_communication) and delivery_dup and interaction_dup
        _m2m_audit(
            db,
            principal,
            "communication.ingested",
            person_id=person_id,
            project_id=pid,
            communication_id=communication.id,
            delivery_event_id=delivery.id,
            interaction_id=interaction.id,
            status=body.status,
            duplicate=duplicate,
        )
        return {
            "person_id": person_id,
            "project_id": pid,
            "communication_id": communication.id,
            "delivery_event_id": delivery.id,
            "interaction_id": interaction.id,
            "status": communication.status,
            "communication_key": communication_key,
            "external_event_key": body.external_event_key,
            "duplicate": duplicate,
            "sor": "voronka",
        }


@router.post("/projects/{pid}/send-policy/check")
def m2m_send_policy_check(
    pid: str,
    body: M2MSendPolicyCheck,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("person:read")),
):
    """CRM-102 send-time preflight for Signal/Mailu/other senders (HMAC M2M)."""
    from app.crm_models import ContactPoint, Person
    from app import person_graph as graph
    from app.crm_service import normalize_email

    _body, principal = auth_ctx
    assert_project_allowed(pid)
    if body.purpose not in {"marketing", "transactional", "service", "analytics"}:
        raise HTTPException(422, "Неизвестная цель согласия")
    if body.missing_policy not in {"soft_allow", "deny"}:
        raise HTTPException(422, "Неизвестный missing_policy")
    with Session() as db:
        person_id = body.person_id
        contact_point_id = body.contact_point_id
        if contact_point_id:
            cp = must(db.get(ContactPoint, contact_point_id))
            person_id = cp.person_id
        elif not person_id and body.email and body.channel == "email":
            try:
                normalized = normalize_email(body.email)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            digest = graph.address_hash("email", normalized)
            # Prefer ContactPoints bound to a Profile in this project.
            # Ambiguous multi-person matches must deny (never pick an arbitrary first).
            project_matches: list[ContactPoint] = []
            for cp in db.scalars(
                select(ContactPoint).where(
                    ContactPoint.channel == "email",
                    ContactPoint.address_hash == digest,
                )
            ):
                profiles = list(
                    db.scalars(
                        select(Profile).where(
                            Profile.person_id == cp.person_id, Profile.project_id == pid
                        )
                    )
                )
                if profiles:
                    project_matches.append(cp)
            unique_persons = {cp.person_id for cp in project_matches}
            if len(unique_persons) > 1:
                _m2m_audit(
                    db,
                    principal,
                    "send_policy.check",
                    person_id=None,
                    project_id=pid,
                    allowed=False,
                    reason="ambiguous_email",
                )
                db.commit()
                return {
                    "allowed": False,
                    "reason": "ambiguous_email",
                    "channel": body.channel,
                    "purpose": body.purpose,
                    "person_id": None,
                    "project_id": pid,
                    "sor": "voronka",
                    "listmonk": False,
                }
            if len(project_matches) == 1:
                person_id = project_matches[0].person_id
                contact_point_id = project_matches[0].id
            if not person_id:
                # No Person yet: transactional inquiry replies may soft-allow;
                # marketing stays denied unless soft_allow requested.
                missing = "soft_allow" if body.purpose == "transactional" else body.missing_policy
                allowed = missing == "soft_allow"
                reason = "no_person_transactional" if allowed else "no_person"
                _m2m_audit(
                    db,
                    principal,
                    "send_policy.check",
                    person_id=None,
                    project_id=pid,
                    allowed=allowed,
                    reason=reason,
                )
                db.commit()
                return {
                    "allowed": allowed,
                    "reason": reason,
                    "channel": body.channel,
                    "purpose": body.purpose,
                    "person_id": None,
                    "project_id": pid,
                    "sor": "voronka",
                    "listmonk": False,
                }
        if not person_id:
            raise HTTPException(422, "person_id, contact_point_id или email обязателен")
        person = must(db.get(Person, person_id))
        if person.merged_into_id:
            person = must(db.get(Person, person.merged_into_id))
            person_id = person.id
        profiles = list(
            db.scalars(select(Profile).where(Profile.person_id == person_id, Profile.project_id == pid))
        )
        if not profiles:
            raise HTTPException(404, "Person не в проекте")
        missing = "soft_allow" if body.purpose == "transactional" else body.missing_policy
        allowed, reason = graph.send_allowed(
            db,
            person_id=person_id,
            contact_point_id=contact_point_id,
            channel=body.channel,
            purpose=body.purpose,
            connection_id=body.connection_id,
            missing_policy=missing,
        )
        _m2m_audit(
            db,
            principal,
            "send_policy.check",
            person_id=person_id,
            project_id=pid,
            allowed=allowed,
            reason=reason,
        )
        db.commit()
        return {
            "allowed": allowed,
            "reason": reason,
            "channel": body.channel,
            "purpose": body.purpose,
            "person_id": person_id,
            "project_id": pid,
            "sor": "voronka",
            "listmonk": False,
        }


# ----- PDialog P0: scenario publish / pause / status -----


class M2MScenarioPublish(Body):
    schema_version: int = 1
    # JSON key remains "construct"; rename attr to avoid BaseModel.construct shadow warning
    pdialog_construct: str = Field(default="personal_dialog", alias="construct")
    brand_id: str
    scenario_id: str
    signal_version_id: str
    rules_version: str = Field(min_length=1, max_length=120)
    rules_text: str = Field(default="", max_length=8192)
    connection_ids: list[str] = Field(default_factory=list, max_length=20)
    external_idempotency_key: str = Field(min_length=8, max_length=120)
    funnel_name: str = Field(default="Personal Dialog", max_length=160)
    voronka_funnel_id: str | None = None
    graph: dict[str, Any]


def _find_scenario_publish(db, pid: str, key: str) -> dict[str, Any] | None:
    inbox = db.scalar(
        select(IntegrationInbox).where(
            IntegrationInbox.source == "signal",
            IntegrationInbox.event_key == f"scenario.publish:{key}",
        )
    )
    if not inbox:
        return None
    payload = inbox.payload or {}
    if payload.get("project_id") and payload.get("project_id") != pid:
        return None
    return payload


@router.post("/projects/{pid}/scenarios/versions")
async def m2m_publish_scenario_version(
    pid: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("scenario:publish")),
):
    """Accept immutable Personal Dialog Version from Signal. No auto_send. No second Outbox."""
    raw, principal = auth_ctx
    assert_project_allowed(pid)
    try:
        data = json.loads(raw.decode() or "{}")
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Invalid JSON") from exc
    if "auto_send" in data:
        raise HTTPException(422, "auto_send is forbidden on scenario publish")
    try:
        body = M2MScenarioPublish.model_validate(data)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.schema_version != 1:
        raise HTTPException(422, "schema_version must be 1")
    if body.pdialog_construct != "personal_dialog":
        raise HTTPException(422, "construct must be personal_dialog")

    with Session() as db:
        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")

        prior = _find_scenario_publish(db, pid, body.external_idempotency_key)
        if prior and prior.get("voronka_version_id"):
            _m2m_audit(
                db,
                principal,
                "scenario.publish.replay",
                project_id=pid,
                version_id=prior.get("voronka_version_id"),
                key=body.external_idempotency_key,
            )
            db.commit()
            return {
                "voronka_version_id": prior["voronka_version_id"],
                "voronka_funnel_id": prior.get("voronka_funnel_id"),
                "signal_version_id": prior.get("signal_version_id"),
                "rules_version": prior.get("rules_version"),
                "activated": bool(prior.get("activated")),
                "idempotent_replay": True,
            }

        try:
            validate_published_graph(body.graph)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

        connection_ids = list(body.connection_ids or [])
        for cid in connection_ids:
            conn = db.get(Connection, cid)
            if not conn or conn.project_id != pid:
                raise HTTPException(422, f"connection_id не в проекте: {cid}")

        # Activate when every node is in TYPES (includes VR-124 llm_reply/escalate).
        # Remaining forward-compat descriptors (send_template/tag_contact) stay stored_inactive.
        activate = graph_runtime_ready(body.graph)
        if activate:
            if not connection_ids:
                raise HTTPException(422, "connection_ids required to activate a runtime-ready Version")
            try:
                validate_bindings(db, body.graph, connection_ids)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

        funnel = None
        if body.voronka_funnel_id:
            funnel = db.get(Funnel, body.voronka_funnel_id)
            if not funnel or funnel.project_id != pid:
                raise HTTPException(404, "Funnel не найден в проекте")
        else:
            # Bind existing funnel previously published for this Signal scenario
            for row in db.scalars(select(Funnel).where(Funnel.project_id == pid)):
                draft_meta = (row.draft or {}).get("meta") or {}
                if draft_meta.get("signal_scenario_id") == body.scenario_id:
                    funnel = row
                    break

        graph_store = dict(body.graph)
        meta = dict(graph_store.get("meta") or {})
        meta.update(
            {
                "construct": "personal_dialog",
                "signal_scenario_id": body.scenario_id,
                "signal_version_id": body.signal_version_id,
                "rules_version": body.rules_version,
                "rules_text": (body.rules_text or "")[:8192],
                "brand_id": body.brand_id,
                "source": "signal_pdialog",
                "activation": "active" if activate else "stored_inactive",
            }
        )
        graph_store["meta"] = meta

        if funnel is None:
            funnel = Funnel(
                id=uid(),
                name=(body.funnel_name or "Personal Dialog")[:160],
                project_id=pid,
                connection_ids=connection_ids,
                draft=graph_store,
                revision=1,
            )
            db.add(funnel)
        else:
            funnel.draft = graph_store
            funnel.revision = int(funnel.revision or 0) + 1
            if connection_ids:
                funnel.connection_ids = connection_ids

        version = Version(id=uid(), funnel_id=funnel.id, graph=graph_store)
        db.add(version)
        # Never promote descriptor-only graphs to executable head (Entry would run them).
        if activate:
            funnel.published_version_id = version.id

        inbox_payload = {
            "project_id": pid,
            "voronka_version_id": version.id,
            "voronka_funnel_id": funnel.id,
            "signal_version_id": body.signal_version_id,
            "scenario_id": body.scenario_id,
            "rules_version": body.rules_version,
            "brand_id": body.brand_id,
            "activated": activate,
        }
        db.add(
            IntegrationInbox(
                id=uid(),
                source="signal",
                event_key=f"scenario.publish:{body.external_idempotency_key}",
                kind="scenario.publish",
                payload=inbox_payload,
                state="applied",
                created_at=now(),
            )
        )
        _m2m_audit(
            db,
            principal,
            "scenario.publish",
            project_id=pid,
            version_id=version.id,
            funnel_id=funnel.id,
            scenario_id=body.scenario_id,
            activated=activate,
        )
        db.commit()
        return {
            "voronka_version_id": version.id,
            "voronka_funnel_id": funnel.id,
            "signal_version_id": body.signal_version_id,
            "rules_version": body.rules_version,
            "activated": activate,
            "idempotent_replay": False,
        }


@router.post("/projects/{pid}/executions/{execution_id}/pause")
async def m2m_pause_execution(
    pid: str,
    execution_id: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("scenario:pause")),
):
    _body, principal = auth_ctx
    assert_project_allowed(pid)
    with Session() as db:
        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        run = db.get(Run, execution_id)
        if not run:
            raise HTTPException(404, "Execution не найден")
        version = db.get(Version, run.version_id)
        if not version:
            raise HTTPException(404, "Version не найден")
        funnel = db.get(Funnel, version.funnel_id)
        if not funnel or funnel.project_id != pid:
            raise HTTPException(403, "Execution вне проекта")
        if run.status in {"active", "waiting", "delayed"}:
            run.state = {**(run.state or {}), "_before_pause": run.status}
            run.status = "paused"
            for out in db.scalars(
                select(Outbox).where(
                    Outbox.run_id == run.id,
                    Outbox.status == "pending",
                )
            ):
                out.status = "held"
        elif run.status != "paused":
            raise HTTPException(409, f"Нельзя поставить на паузу статус {run.status}")
        _m2m_audit(db, principal, "scenario.pause", project_id=pid, execution_id=execution_id)
        db.commit()
        return {"execution_id": execution_id, "status": "paused"}


@router.get("/projects/{pid}/scenarios/versions/{version_id}/status")
async def m2m_scenario_version_status(
    pid: str,
    version_id: str,
    request: Request,
    auth_ctx=Depends(make_m2m_dep("scenario:status")),
):
    _body, principal = auth_ctx
    assert_project_allowed(pid)
    with Session() as db:
        if not db.get(Project, pid):
            raise HTTPException(404, "Проект не найден")
        version = db.get(Version, version_id)
        if not version:
            raise HTTPException(404, "Version не найден")
        funnel = db.get(Funnel, version.funnel_id)
        if not funnel or funnel.project_id != pid:
            raise HTTPException(404, "Version вне проекта")
        meta = (version.graph or {}).get("meta") or {}
        runs = list(db.scalars(select(Run).where(Run.version_id == version_id)))
        counts: dict[str, int] = {}
        for run in runs:
            counts[run.status] = counts.get(run.status, 0) + 1
        return {
            "voronka_version_id": version.id,
            "voronka_funnel_id": funnel.id,
            "project_id": pid,
            "construct": meta.get("construct"),
            "signal_scenario_id": meta.get("signal_scenario_id"),
            "signal_version_id": meta.get("signal_version_id"),
            "rules_version": meta.get("rules_version"),
            "is_published_head": funnel.published_version_id == version.id,
            "run_counts": counts,
            "run_total": len(runs),
        }
