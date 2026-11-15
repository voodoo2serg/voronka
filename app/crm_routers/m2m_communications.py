"""Scoped M2M communications API for Signal (SIG-4).

Auth: HMAC wire v1 via app.m2m_signal — NOT crm_session.
Contract: docs/M2M_COMMUNICATIONS_OPENAPI.yaml (+ signal docs/VORONKA_M2M_OPENAPI.yaml).
"""
from __future__ import annotations

import base64
import hashlib
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
from app.db import Connection, Outbox, Asset, Identity, Event, now
from app import media as media_mod
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
