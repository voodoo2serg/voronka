"""Inbound/outbound neighbor APIs: solo_pr leads, LMS completion, public checkout quote."""
from __future__ import annotations

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner, robokassa_ready
from app.crm_models import IntegrationInbox, IntegrationOutbox, Order, Product, Profile, Deal, Activity
from app.crm_service import uid, create_profile, project_access, normalize_email, normalize_phone, balance
from app.db import now
from app.integrations import verify_bearer_or_hmac

router = APIRouter()


class LeadBody(Body):
    event_id: str = Field(min_length=4, max_length=160)
    kind: Literal["lead.created"] = "lead.created"
    contact_type: str = Field(default="", max_length=40)
    contact: str = Field(default="", max_length=250)
    email: str = Field(default="", max_length=250)
    name: str = Field(default="", max_length=200)
    service: str = Field(default="", max_length=200)
    message: str = Field(default="", max_length=10000)
    consent: bool = False
    ads_consent: bool = False
    created: str = Field(default="", max_length=80)
    source: Literal["solo_pr"] = "solo_pr"
    project_id: str | None = None


class CompletionBody(Body):
    event_id: str = Field(min_length=4, max_length=160)
    kind: Literal["learning.completed"] = "learning.completed"
    voronka_profile_id: str | None = None
    email: str = Field(default="", max_length=250)
    resource: str = Field(default="", max_length=200)
    evidence: str = Field(default="", max_length=2000)
    source: Literal["lms_end"] = "lms_end"


class QuoteBody(Body):
    project_id: str
    email: str = Field(min_length=3, max_length=250)
    name: str = Field(default="", max_length=200)
    product_id: str
    request_key: str = Field(min_length=8, max_length=100)


async def _require_integration(request: Request):
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    if not verify_bearer_or_hmac(headers, body):
        raise HTTPException(401, "Неверная подпись интеграции")
    return body


@router.post("/integrations/solo_pr/leads")
async def inbound_lead(request: Request, body: LeadBody):
    raw = await _require_integration(request)
    # Body already parsed by FastAPI; re-check size only.
    if len(raw) > 100_000:
        raise HTTPException(413, "Слишком большой payload")
    project_id = body.project_id or os.getenv("DEFAULT_PROJECT_ID", "")
    if not project_id:
        raise HTTPException(503, "DEFAULT_PROJECT_ID не задан для входящих лидов")
    try:
        email = normalize_email(body.email or (body.contact if body.contact_type == "email" else ""))
        phone = normalize_phone(body.contact if body.contact_type == "phone" else "")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with Session.begin() as db:
        prior = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == "solo_pr", IntegrationInbox.event_key == body.event_id
            )
        )
        if prior:
            return {"ok": True, "duplicate": True, "profile_id": prior.profile_id, "inbox_id": prior.id}
        profile = None
        if email:
            profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.email == email))
        if profile is None and phone:
            profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.phone == phone))
        if profile is None:
            profile = create_profile(
                db,
                project_id,
                {
                    "name": body.name or email or phone or "Лид сайта",
                    "email": email,
                    "phone": phone,
                    "source": "solo_pr",
                    "extra": {
                        "lead_event_id": body.event_id,
                        "contact_type": body.contact_type,
                        "service": body.service,
                        "consent": body.consent,
                        "ads_consent": body.ads_consent,
                        "created": body.created,
                    },
                },
            )
        db.add(
            Deal(
                id=uid(),
                project_id=project_id,
                profile_id=profile.id,
                title=body.service or "Заявка с сайта",
                stage="new",
                next_action="Связаться с лидом",
            )
        )
        from app.db import Admin
        owner = db.scalar(select(Admin).where(Admin.role == "owner", Admin.active.is_(True)))
        if owner and body.message:
            db.add(
                Activity(
                    id=uid(),
                    profile_id=profile.id,
                    actor_id=owner.telegram_id,
                    kind="lead",
                    channel="solo_pr",
                    outcome="lead.created",
                    body=body.message[:20000],
                )
            )
        row = IntegrationInbox(
            id=uid(),
            source="solo_pr",
            event_key=body.event_id,
            kind="lead.created",
            payload=body.model_dump(),
            state="applied",
            profile_id=profile.id,
            created_at=now(),
        )
        db.add(row)
        db.flush()
        return {"ok": True, "duplicate": False, "profile_id": profile.id, "inbox_id": row.id}


@router.post("/integrations/lms_end/completions")
async def inbound_completion(request: Request, body: CompletionBody):
    await _require_integration(request)
    with Session.begin() as db:
        prior = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == "lms_end", IntegrationInbox.event_key == body.event_id
            )
        )
        if prior:
            return {"ok": True, "duplicate": True}
        profile = None
        if body.voronka_profile_id:
            profile = db.get(Profile, body.voronka_profile_id)
        if profile is None and body.email:
            try:
                email = normalize_email(body.email)
            except ValueError:
                raise HTTPException(422, "Некорректный email")
            profile = db.scalar(select(Profile).where(Profile.email == email))
        if profile is None:
            raise HTTPException(404, "Профиль не найден")
        # Mark open course orders academic_complete when resource matches snapshot/product.
        orders = list(db.scalars(select(Order).where(Order.profile_id == profile.id, Order.state == "open")))
        updated = []
        for order in orders:
            snap = order.snapshot or {}
            if body.resource and body.resource not in {snap.get("name", ""), order.product_id, snap.get("kind", "")}:
                # Soft match: still allow explicit empty resource to mark latest course order.
                if snap.get("kind") != "course":
                    continue
            order.academic_complete = True
            updated.append(order.id)
        from app.db import Admin
        owner = db.scalar(select(Admin).where(Admin.role == "owner", Admin.active.is_(True)))
        if owner:
            db.add(
                Activity(
                    id=uid(),
                    profile_id=profile.id,
                    actor_id=owner.telegram_id,
                    kind="learning",
                    channel="lms_end",
                    outcome="learning.completed",
                    body=(body.evidence or body.resource or "Обучение завершено")[:20000],
                )
            )
        row = IntegrationInbox(
            id=uid(),
            source="lms_end",
            event_key=body.event_id,
            kind="learning.completed",
            payload=body.model_dump(),
            state="applied",
            profile_id=profile.id,
            created_at=now(),
        )
        db.add(row)
        return {"ok": True, "orders_updated": updated, "profile_id": profile.id}


@router.post("/integrations/commerce/quote")
async def public_quote(request: Request, body: QuoteBody):
    """solo_pr BFF checkout quote — creates/returns Order; never writes site ledger."""
    await _require_integration(request)
    try:
        email = normalize_email(body.email)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    with Session.begin() as db:
        product = must(db.get(Product, body.product_id))
        if product.project_id != body.project_id or not product.active:
            raise HTTPException(422, "Продукт не продаётся")
        prior = db.scalar(select(Order).where(Order.project_id == body.project_id, Order.request_key == body.request_key))
        if prior:
            return {**encode(prior), **balance(db, prior), "duplicate": True}
        profile = db.scalar(select(Profile).where(Profile.project_id == body.project_id, Profile.email == email))
        if profile is None:
            profile = create_profile(
                db,
                body.project_id,
                {"name": body.name or email, "email": email, "source": "solo_pr_checkout"},
            )
        row = Order(
            id=uid(),
            project_id=body.project_id,
            profile_id=profile.id,
            product_id=product.id,
            request_key=body.request_key,
            total_minor=product.price_minor,
            snapshot={
                "name": product.name,
                "kind": product.kind,
                "price_minor": product.price_minor,
                "catalog_price_minor": product.price_minor,
                "currency": product.currency,
                "discount_minor": 0,
                "promo": "",
                "config": product.config,
                "source": "solo_pr_checkout",
            },
            installments=[],
        )
        db.add(row)
        db.flush()
        return {**encode(row), **balance(db, row), "duplicate": False, "robokassa_ready": robokassa_ready()}


@router.get("/projects/{pid}/integration-outbox")
def list_outbox(pid: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(db.scalars(select(IntegrationOutbox).order_by(IntegrationOutbox.created_at.desc()).limit(200)))
        # Filter loosely by payload project_id when present.
        return [
            encode(r)
            for r in rows
            if (r.payload or {}).get("project_id") in {None, pid} or r.destination in {"solo_pr", "lms_end"}
        ]


@router.get("/integrations/status")
def integration_status(admin=Depends(crm_actor)):
    staff_owner(admin)
    return {
        "solo_pr": {
            "events_url_set": bool(os.getenv("SOLO_PR_EVENTS_URL")),
            "secret_set": bool(os.getenv("SOLO_PR_WEBHOOK_SECRET")),
        },
        "lms_end": {
            "enrollment_url_set": bool(os.getenv("LMS_END_ENROLLMENT_URL")),
            "secret_set": bool(os.getenv("LMS_END_WEBHOOK_SECRET") or os.getenv("LMS_M2M_TOKEN")),
        },
        "inbound_token_set": bool(os.getenv("INTEGRATION_INBOUND_TOKEN") or os.getenv("SOLO_PR_WEBHOOK_SECRET") or os.getenv("LMS_M2M_TOKEN")),
        "default_project_id": os.getenv("DEFAULT_PROJECT_ID", ""),
        "shared_database": False,
        "chatwoot": False,
        "espocrm": False,
    }
