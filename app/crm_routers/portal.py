"""CRM portal surfaces: payment providers UX, mail status, period analytics, chrome links."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func, and_

from app.crm_deps import Session, crm_actor
from app.crm_models import Profile, Payment, Order, Campaign, OutreachBatch, ProviderEvent
from app.crm_service import project_access, assigned_profile_filter, manager_scope
from app.db import Outbox, Connection
from app import portals

router = APIRouter()


def _parse_dt(value: str | None, *, end: bool = False) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            # date-only
            dt = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
            if end:
                return dt.replace(hour=23, minute=59, second=59, microsecond=999999)
            return dt
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as exc:
        raise HTTPException(422, "Некорректная дата периода") from exc


@router.get("/portals")
def portals_chrome(admin=Depends(crm_actor)):
    return {
        "host_aware": True,
        "crm_host": portals.PORTAL_HOSTS["crm"],
        "links": portals.portal_chrome(admin.role),
        "mailu_webmail": portals.MAILU_WEBMAIL_URL,
        "voronka_ops": portals.voronka_public_base() + "/",
        "role": admin.role,
    }


@router.get("/projects/{pid}/payment-providers")
def payment_providers(pid: str, request: Request, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        if manager_scope(admin):
            orders = list(
                db.scalars(
                    select(Order)
                    .join(Profile, Profile.id == Order.profile_id)
                    .where(Order.project_id == pid, Profile.owner_id == admin.telegram_id)
                )
            )
        else:
            orders = list(db.scalars(select(Order).where(Order.project_id == pid)))
        order_ids = [o.id for o in orders]
        payments = []
        if order_ids:
            payments = list(db.scalars(select(Payment).where(Payment.order_id.in_(order_ids))))
        received = sum(p.amount_minor for p in payments if p.kind == "payment")
        refunded = sum(p.amount_minor for p in payments if p.kind == "refund")
        last = db.scalar(
            select(ProviderEvent)
            .where(ProviderEvent.provider == "robokassa")
            .order_by(ProviderEvent.created_at.desc())
            .limit(1)
        )
        robokassa = portals.robokassa_status()
        # Prefer request host when already on crm.* so ResultURL matches the live vhost.
        host = portals.normalize_host(request.headers.get("host"))
        if portals.is_crm_host(host):
            scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
            robokassa["result_url"] = f"{scheme}://{host}/api/crm/robokassa/result"
        robokassa["last_result"] = (
            {
                "id": last.id,
                "state": last.state,
                "created_at": last.created_at.isoformat() if last.created_at else None,
                "order_id": last.order_id,
                "invoice_id": last.invoice_id,
            }
            if last
            else None
        )
        return {
            "robokassa": robokassa,
            "yookassa": portals.yookassa_skeleton(),
            "stats": {
                "orders_count": len(orders),
                "payments_count": sum(1 for p in payments if p.kind == "payment"),
                "refunds_count": sum(1 for p in payments if p.kind == "refund"),
                "received_minor": received,
                "refunded_minor": refunded,
                "source": "Payment/Order",
            },
        }


@router.get("/projects/{pid}/mail-status")
def mail_status(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        row = db.scalar(select(Connection).where(Connection.project_id == pid, Connection.platform == "email"))
        status = portals.mail_status()
        status["email_connection"] = (
            {"id": row.id, "name": row.name, "active": row.active, "platform": row.platform} if row else None
        )
        status["messenger_blasts_url"] = portals.voronka_public_base() + "/"
        status["messenger_note"] = "Мессенджер-рассылки остаются на voronka.solo-pr.ru — не дублируем bot ops в CRM."
        return status


@router.get("/projects/{pid}/analytics")
def period_analytics(pid: str, from_date: str | None = None, to_date: str | None = None, admin=Depends(crm_actor)):
    start = _parse_dt(from_date) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    end = _parse_dt(to_date, end=True) or datetime.now(timezone.utc)
    if start > end:
        raise HTTPException(422, "Дата начала позже даты конца")
    with Session() as db:
        project_access(db, admin, pid)
        profile_filter = assigned_profile_filter(admin)

        contacts_q = select(Profile).where(
            Profile.project_id == pid,
            profile_filter,
            Profile.created_at >= start,
            Profile.created_at <= end,
        )
        contacts = list(db.scalars(contacts_q))
        by_source: dict[str, int] = {}
        for c in contacts:
            key = c.source or "manual"
            by_source[key] = by_source.get(key, 0) + 1

        if manager_scope(admin):
            orders = list(
                db.scalars(
                    select(Order)
                    .join(Profile, Profile.id == Order.profile_id)
                    .where(
                        Order.project_id == pid,
                        Profile.owner_id == admin.telegram_id,
                        Order.created_at >= start,
                        Order.created_at <= end,
                    )
                )
            )
            order_ids_all = [
                o.id
                for o in db.scalars(
                    select(Order)
                    .join(Profile, Profile.id == Order.profile_id)
                    .where(Order.project_id == pid, Profile.owner_id == admin.telegram_id)
                )
            ]
        else:
            orders = list(
                db.scalars(
                    select(Order).where(
                        Order.project_id == pid,
                        Order.created_at >= start,
                        Order.created_at <= end,
                    )
                )
            )
            order_ids_all = list(db.scalars(select(Order.id).where(Order.project_id == pid)))

        payments = []
        if order_ids_all:
            payments = list(
                db.scalars(
                    select(Payment).where(
                        Payment.order_id.in_(order_ids_all),
                        Payment.created_at >= start,
                        Payment.created_at <= end,
                    )
                )
            )
        received = sum(p.amount_minor for p in payments if p.kind == "payment")
        refunded = sum(p.amount_minor for p in payments if p.kind == "refund")

        all_profiles = list(db.scalars(select(Profile).where(Profile.project_id == pid, profile_filter)))
        all_ids = [p.id for p in all_profiles]
        purchasers_total = 0
        if all_ids:
            purchasers_total = db.scalar(
                select(func.count(func.distinct(Order.profile_id)))
                .select_from(Order)
                .join(Payment, and_(Payment.order_id == Order.id, Payment.kind == "payment"))
                .where(Order.project_id == pid, Order.profile_id.in_(all_ids))
            ) or 0

        campaigns = list(db.scalars(select(Campaign).where(Campaign.project_id == pid)))
        conn_ids = list(db.scalars(select(Connection.id).where(Connection.project_id == pid)))
        outbox_sent = outbox_failed = outbox_unknown = 0
        if conn_ids:
            rows = db.execute(
                select(Outbox.status, func.count())
                .where(
                    Outbox.connection_id.in_(conn_ids),
                    Outbox.created_at >= start,
                    Outbox.created_at <= end,
                )
                .group_by(Outbox.status)
            ).all()
            for status, count in rows:
                if status == "sent":
                    outbox_sent = count
                elif status == "failed":
                    outbox_failed = count
                elif status == "unknown":
                    outbox_unknown = count

        outreach_in_period = list(
            db.scalars(
                select(OutreachBatch).where(
                    OutreachBatch.project_id == pid,
                    OutreachBatch.created_at >= start,
                    OutreachBatch.created_at <= end,
                )
            )
        )

        import os

        lms_configured = bool(
            os.getenv("LMS_END_ENROLLMENT_URL")
            and (os.getenv("LMS_END_WEBHOOK_SECRET") or os.getenv("LMS_M2M_TOKEN"))
        )

        return {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "money": {
                "orders_in_period": len(orders),
                "payments_count": sum(1 for p in payments if p.kind == "payment"),
                "refunds_count": sum(1 for p in payments if p.kind == "refund"),
                "received_minor": received,
                "refunded_minor": refunded,
                "net_minor": received - refunded,
            },
            "contacts": {
                "new": len(contacts),
                "by_source": by_source,
                "purchasers_total": purchasers_total,
            },
            "learning": {
                "available": False,
                "lms_events": lms_configured,
                "attendance": [],
                "sparks": [],
                "note": "Посещаемость/«огоньки» появятся когда LMS-21/22 начнут слать signed events. CRM только читает — BBB не хостит.",
                "lms_portal": portals.PORTAL_URLS["lms"],
            },
            "comms": {
                "campaigns_total": len(campaigns),
                "campaigns_by_state": _count_by(campaigns, "state"),
                "outbox_sent": outbox_sent,
                "outbox_failed": outbox_failed,
                "outbox_unknown": outbox_unknown,
                "outreach_batches_in_period": len(outreach_in_period),
            },
        }


def _count_by(rows, attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = getattr(row, attr, None) or "unknown"
        out[key] = out.get(key, 0) + 1
    return out


@router.get("/staff/cms-matrix")
def staff_cms_matrix(admin=Depends(crm_actor)):
    """Manual sync checklist with CMS zone matrix (SPO-23). No second learner matrix."""
    return {
        "source_of_truth": "cms.solo-pr.ru · Администраторы и роли (SPO-23)",
        "cms_url": portals.PORTAL_URLS["cms"] + "/admin",
        "crm_roles": [
            {"id": "owner", "label": "Владелец", "maps_to_cms_zone": "crm=owner"},
            {"id": "editor", "label": "Партнёр", "maps_to_cms_zone": "crm=partner"},
            {"id": "manager", "label": "Менеджер", "maps_to_cms_zone": "crm=manager"},
        ],
        "checklist": [
            {"id": "cms_matrix", "label": "Матрица зон назначена в CMS", "auto": False},
            {"id": "crm_staff", "label": "Сотрудник создан в CRM (login/роль)", "auto": True},
            {"id": "no_learner_matrix", "label": "Нет второй матрицы ученика в CRM", "auto": True, "done": True},
            {"id": "keycloak", "label": "Синхронизация Keycloak groups — позже", "auto": False, "done": False},
        ],
        "sync": "manual_v1",
        "actor_role": admin.role,
    }


@router.post("/yookassa/webhook")
async def yookassa_webhook_stub():
    """Placeholder until YooKassa adapter ships. Always 501 — no silent accept."""
    return JSONResponse(
        status_code=501,
        content={"detail": "YooKassa adapter не реализован", "adapter": "not_implemented"},
    )
