"""CRM campaigns, overview and email connection helpers."""
import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, func

from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner, robokassa_ready
from app.crm_models import Campaign, CampaignRecipient, Profile, Deal, Order, Product
from app.crm_service import (
    uid, project_access, get_profile, audit, available_identity, balance,
    assigned_profile_filter, manager_scope,
)
from app.db import Connection, Outbox

router = APIRouter()


class CampaignBody(Body):
    connection_id: str
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=4000)
    profile_ids: list[str] = Field(min_length=1, max_length=1000)


@router.post("/projects/{pid}/campaigns")
def add_campaign(pid: str, body: CampaignBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        connection = must(db.get(Connection, body.connection_id))
        if connection.project_id != pid or (admin.role != "owner" and connection.id not in admin.connection_ids):
            raise HTTPException(404, "Подключение недоступно")
        report = []
        for profile_id in set(body.profile_ids):
            profile = get_profile(db, admin, profile_id)
            if profile.project_id != pid: raise HTTPException(404, "Клиент другого проекта")
            identity, reason = available_identity(db, profile, connection.id)
            report.append({"profile_id": profile_id, "eligible": identity is not None, "reason": reason})
        row = Campaign(id=uid(), project_id=pid, **body.model_dump(), report={"recipients": report})
        db.add(row); db.flush()
        return encode(row)


@router.post("/campaigns/{cid}/launch")
def launch_campaign(cid: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        campaign = must(db.scalar(select(Campaign).where(Campaign.id == cid).with_for_update()))
        project_access(db, admin, campaign.project_id)
        if admin.role != "owner" and campaign.connection_id not in admin.connection_ids:
            raise HTTPException(403, "Подключение недоступно")
        if campaign.state != "draft":
            return {"state": campaign.state, **campaign.report}
        sent, excluded = 0, []
        for profile_id in set(campaign.profile_ids):
            try:
                profile = get_profile(db, admin, profile_id)
            except HTTPException:
                excluded.append({"profile_id": profile_id, "reason": "Клиент недоступен"}); continue
            identity, reason = available_identity(db, profile, campaign.connection_id)
            if not identity:
                excluded.append({"profile_id": profile_id, "reason": reason}); continue
            out = Outbox(id=uid(), connection_id=identity.connection_id, identity_id=identity.id, chat_id=identity.chat_id,
                         payload={"type": "text", "subject": campaign.title, "text": campaign.text.replace("{name}", profile.name), "crm_campaign_id": cid, "crm_profile_id": profile.id})
            db.add(out); db.flush()
            db.add(CampaignRecipient(id=uid(), campaign_id=cid, profile_id=profile_id, outbox_id=out.id))
            sent += 1
        campaign.state, campaign.report = "queued", {"queued": sent, "excluded": excluded}
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


@router.get("/projects/{pid}/campaigns")
def campaigns(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        result = []
        for campaign in db.scalars(select(Campaign).where(Campaign.project_id == pid)):
            states = {}
            for status in db.scalars(select(Outbox.status).join(CampaignRecipient, CampaignRecipient.outbox_id == Outbox.id).where(CampaignRecipient.campaign_id == campaign.id)):
                states[status] = states.get(status, 0) + 1
            result.append({**encode(campaign), "delivery": states})
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
        return {"contacts": contact_count,
                "deals": db.scalar(deal_q),
                "pipeline_minor": db.scalar(pipeline_q),
                "received_minor": sum(balance(db, o)["paid_minor"] for o in orders),
                "connections": [{"id": c.id, "name": c.name, "platform": c.platform, "active": c.active} for c in db.scalars(select(Connection).where(Connection.project_id == pid))],
                "integrations": {"robokassa": robokassa_ready(),
                                 "email": bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM")), "raga": bool(os.getenv("RAG_API_URL")), "chamilo": "not_implemented"}}


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
