"""CRM campaigns, overview and email connection helpers."""
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, func

from app.capabilities import assert_sendable, platform_capabilities
from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner, robokassa_ready
from app.crm_models import Campaign, CampaignRecipient, Profile, Deal, Order, Product
from app.crm_service import (
    uid, project_access, get_profile, audit, available_identity, balance,
    assigned_profile_filter, manager_scope,
)
from app.db import Connection, Outbox, Asset
from app import ai_providers
from app.observability import queue_metrics

router = APIRouter()


class CampaignBody(Body):
    connection_id: str
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    profile_ids: list[str] = Field(default_factory=list, max_length=1000)
    identity_ids: list[str] = Field(default_factory=list, max_length=1000)
    asset_id: str | None = None


def _resolve_profile_ids(db, admin, pid: str, body: CampaignBody) -> list[str]:
    """Ops may pass identity_ids; CRM keeps profile_ids. At least one audience required."""
    from app.db import Identity
    from app.crm_service import ensure_profile

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


def _asset_blocker(db, connection: Connection, asset_id: str | None) -> str | None:
    if not asset_id:
        return None
    asset = db.get(Asset, asset_id)
    if not asset:
        return "Медиафайл не найден"
    if connection.id not in (asset.connection_ids or []) and connection.platform == "telegram":
        # Allow if local file exists to re-upload to this bot.
        if not asset.local_path:
            return "Кружок/файл привязан к другому боту без локальной копии"
    ok, note = assert_sendable(connection.platform, asset.kind)
    if not ok:
        return note
    if connection.platform == "telegram":
        has_ref = bool((asset.telegram_refs or {}).get(connection.id))
        if not asset.local_path and not has_ref:
            return "Нет локального файла и нет file_id для этого бота — рассылка заблокирована"
    elif connection.platform in {"max", "vk"}:
        return f"{connection.platform}: video_note/native media недоступны — не маскируем успехом"
    return None


@router.post("/projects/{pid}/campaigns")
def add_campaign(pid: str, body: CampaignBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        connection = must(db.get(Connection, body.connection_id))
        if connection.project_id != pid or (admin.role != "owner" and connection.id not in admin.connection_ids):
            raise HTTPException(404, "Подключение недоступно")
        # Instant messenger blasts live on voronka ops; email stays on CRM.
        profile_ids = _resolve_profile_ids(db, admin, pid, body)
        blocker = _asset_blocker(db, connection, body.asset_id)
        report = []
        for profile_id in profile_ids:
            profile = get_profile(db, admin, profile_id)
            if profile.project_id != pid: raise HTTPException(404, "Клиент другого проекта")
            identity, reason = available_identity(db, profile, connection.id)
            report.append({"profile_id": profile_id, "eligible": identity is not None and not blocker, "reason": blocker or reason})
        payload = body.model_dump()
        payload["profile_ids"] = profile_ids
        payload.pop("identity_ids", None)
        row = Campaign(id=uid(), project_id=pid, **payload, report={"recipients": report, "asset_blocker": blocker})
        db.add(row); db.flush()
        return encode(row)


@router.post("/campaigns/{cid}/preflight")
def preflight_campaign(cid: str, admin=Depends(crm_actor)):
    with Session() as db:
        campaign = must(db.get(Campaign, cid))
        project_access(db, admin, campaign.project_id)
        connection = must(db.get(Connection, campaign.connection_id))
        blocker = _asset_blocker(db, connection, campaign.asset_id)
        from app import media as media_mod
        import shutil
        media_mod.ROOT.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(media_mod.ROOT).free
        recipients = []
        for profile_id in set(campaign.profile_ids):
            try:
                profile = get_profile(db, admin, profile_id)
            except HTTPException:
                recipients.append({"profile_id": profile_id, "ok": False, "reason": "Клиент недоступен"})
                continue
            identity, reason = available_identity(db, profile, connection.id)
            recipients.append({
                "profile_id": profile_id,
                "ok": identity is not None and not blocker and not profile.stopped,
                "reason": blocker or reason,
                "stopped": profile.stopped,
            })
        return {
            "connection_alive": bool(connection.active),
            "credentials_present": connection.platform == "email" or bool(os.environ.get(connection.token_env)),
            "asset_blocker": blocker,
            "media_free_bytes": free,
            "capabilities": platform_capabilities(connection.platform),
            "rate_limit_per_second": int(os.getenv("BOT_MESSAGES_PER_SECOND", "20")),
            "recipients": recipients,
            "ready": connection.active and not blocker and any(r["ok"] for r in recipients),
        }


@router.post("/campaigns/{cid}/launch")
def launch_campaign(cid: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        campaign = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        project_access(db, admin, campaign.project_id)
        if admin.role != "owner" and campaign.connection_id not in admin.connection_ids:
            raise HTTPException(403, "Подключение недоступно")
        if campaign.state != "draft":
            return {"state": campaign.state, **campaign.report}
        connection = must(db.get(Connection, campaign.connection_id))
        if not connection.active:
            raise HTTPException(409, "Подключение выключено")
        blocker = _asset_blocker(db, connection, campaign.asset_id)
        if blocker:
            raise HTTPException(409, blocker)
        asset = db.get(Asset, campaign.asset_id) if campaign.asset_id else None
        sent, excluded = 0, []
        for profile_id in set(campaign.profile_ids):
            try:
                profile = get_profile(db, admin, profile_id)
            except HTTPException:
                excluded.append({"profile_id": profile_id, "reason": "Клиент недоступен"}); continue
            identity, reason = available_identity(db, profile, campaign.connection_id)
            if not identity:
                excluded.append({"profile_id": profile_id, "reason": reason}); continue
            payload = {
                "type": asset.kind if asset else "text",
                "subject": campaign.title,
                "text": campaign.text.replace("{name}", profile.name),
                "crm_campaign_id": cid,
                "crm_profile_id": profile.id,
            }
            if asset:
                payload["asset_id"] = asset.id
            out = Outbox(
                id=uid(),
                connection_id=identity.connection_id,
                identity_id=identity.id,
                chat_id=identity.chat_id,
                payload=payload,
            )
            db.add(out); db.flush()
            db.add(CampaignRecipient(id=uid(), campaign_id=cid, profile_id=profile_id, outbox_id=out.id))
            sent += 1
        campaign.state, campaign.report = "queued", {"queued": sent, "excluded": excluded, "asset_id": campaign.asset_id}
        audit(db, admin, "campaign.launched", campaign_id=cid, queued=sent)
        return campaign.report


@router.post("/campaigns/{cid}/cancel")
def cancel_campaign(cid: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        project_access(db, admin, row.project_id)
        row.state = "cancelled"
        for recipient in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)):
            out = db.get(Outbox, recipient.outbox_id)
            if out.status == "pending": out.status = "cancelled"
        return {"state": row.state}


class CampaignControl(Body):
    action: Literal["pause", "resume"]


@router.post("/campaigns/{cid}/control")
def control_campaign(cid: str, body: CampaignControl, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        project_access(db, admin, row.project_id)
        if row.state not in {"queued", "paused"}:
            raise HTTPException(409, "Кампания ещё не запущена или уже остановлена")
        row.state = "paused" if body.action == "pause" else "queued"
        for recipient in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)):
            out = db.get(Outbox, recipient.outbox_id)
            if body.action == "pause" and out.status == "pending": out.status = "held"
            if body.action == "resume" and out.status == "held": out.status = "pending"
        audit(db, admin, "campaign." + body.action, campaign_id=cid)
        return {"state": row.state}


class RetryBody(Body):
    profile_ids: list[str] | None = None
    acknowledge_duplicate_risk: bool = False


@router.post("/campaigns/{cid}/retry")
def retry_campaign(cid: str, body: RetryBody, admin=Depends(crm_actor)):
    """Manual retry for failed/unknown recipients with explicit duplicate-risk ack."""
    if not body.acknowledge_duplicate_risk:
        raise HTTPException(422, "Подтвердите риск дубликата (acknowledge_duplicate_risk)")
    with Session.begin() as db:
        campaign = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        project_access(db, admin, campaign.project_id)
        if campaign.state == "cancelled":
            raise HTTPException(409, "Кампания отменена")
        connection = must(db.get(Connection, campaign.connection_id))
        blocker = _asset_blocker(db, connection, campaign.asset_id)
        if blocker:
            raise HTTPException(409, blocker)
        retried, skipped = [], []
        for recipient in db.scalars(select(CampaignRecipient).where(CampaignRecipient.campaign_id == cid)):
            if body.profile_ids and recipient.profile_id not in body.profile_ids:
                continue
            out = db.get(Outbox, recipient.outbox_id)
            if out.status not in {"failed", "unknown"}:
                skipped.append({"profile_id": recipient.profile_id, "status": out.status})
                continue
            profile = get_profile(db, admin, recipient.profile_id)
            if profile.stopped:
                skipped.append({"profile_id": recipient.profile_id, "status": "stopped"})
                continue
            identity, reason = available_identity(db, profile, campaign.connection_id)
            if not identity:
                skipped.append({"profile_id": recipient.profile_id, "reason": reason})
                continue
            out.status = "pending"
            out.error = None
            out.due_at = out.due_at
            out.attempts = out.attempts
            retried.append(recipient.profile_id)
        audit(db, admin, "campaign.retry", campaign_id=cid, retried=len(retried), duplicate_risk_ack=True)
        return {"retried": retried, "skipped": skipped, "duplicate_risk_acknowledged": True}


@router.get("/projects/{pid}/campaigns")
def campaigns(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        result = []
        for campaign in db.scalars(select(Campaign).where(Campaign.project_id == pid)):
            states = {}
            problem = []
            for recipient, status, error in db.execute(
                select(CampaignRecipient.profile_id, Outbox.status, Outbox.error)
                .join(Outbox, CampaignRecipient.outbox_id == Outbox.id)
                .where(CampaignRecipient.campaign_id == campaign.id)
            ):
                states[status] = states.get(status, 0) + 1
                if status in {"failed", "unknown"}:
                    problem.append({"profile_id": recipient, "status": status, "error": error})
            result.append({**encode(campaign), "delivery": states, "problems": problem})
        return result


@router.get("/projects/{pid}/overview")
def overview(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        profile_filter = assigned_profile_filter(admin)
        contact_count = db.scalar(select(func.count()).select_from(Profile).where(Profile.project_id == pid, profile_filter))
        if manager_scope(admin):
            deal_q = select(func.count()).select_from(Deal).join(Profile, Profile.id == Deal.profile_id).where(
                Deal.project_id == pid, (Deal.owner_id == admin.telegram_id) | (Profile.owner_id == admin.telegram_id))
            pipeline_q = select(func.coalesce(func.sum(Deal.amount_minor), 0)).join(Profile, Profile.id == Deal.profile_id).where(
                Deal.project_id == pid, Deal.stage.notin_(["won", "lost"]),
                (Deal.owner_id == admin.telegram_id) | (Profile.owner_id == admin.telegram_id))
            orders = list(db.scalars(select(Order).join(Profile, Profile.id == Order.profile_id).where(
                Order.project_id == pid, Profile.owner_id == admin.telegram_id)))
        else:
            deal_q = select(func.count()).select_from(Deal).where(Deal.project_id == pid)
            pipeline_q = select(func.coalesce(func.sum(Deal.amount_minor), 0)).where(Deal.project_id == pid, Deal.stage.notin_(["won", "lost"]))
            orders = list(db.scalars(select(Order).where(Order.project_id == pid)))
        ai = ai_providers.env_status()
        return {"contacts": contact_count,
                "deals": db.scalar(deal_q),
                "pipeline_minor": db.scalar(pipeline_q),
                "received_minor": sum(balance(db, o)["paid_minor"] for o in orders),
                "connections": [{"id": c.id, "name": c.name, "platform": c.platform, "active": c.active, "role": c.role,
                                 "capabilities": platform_capabilities(c.platform)} for c in db.scalars(select(Connection).where(Connection.project_id == pid))],
                "integrations": {"robokassa": robokassa_ready(),
                                 "email": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM")),
                                 "raga": ai["raga"]["configured"],
                                 "openrouter": ai["openrouter"]["configured"],
                                 "ollama": ai["ollama"]["configured"],
                                 "solo_pr": bool(os.getenv("SOLO_PR_EVENTS_URL") and os.getenv("SOLO_PR_WEBHOOK_SECRET")),
                                 "lms_end": bool(os.getenv("LMS_END_ENROLLMENT_URL") and (os.getenv("LMS_END_WEBHOOK_SECRET") or os.getenv("LMS_M2M_TOKEN"))),
                                 "chamilo": "not_implemented"},
                "queue": queue_metrics(db)}


@router.post("/projects/{pid}/email-connection")
def email_connection(pid: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = db.scalar(select(Connection).where(Connection.project_id == pid, Connection.platform == "email"))
        if not row:
            row = Connection(id=uid(), project_id=pid, name="Email-рассылка", platform="email", token_env="", secret_env="", config={})
            db.add(row); db.flush()
        return {"id": row.id, "configured": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))}
