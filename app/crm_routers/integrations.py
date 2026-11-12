"""Inbound/outbound neighbor APIs: solo_pr leads, LMS completion, public checkout quote.

Wire contract v1 dual-read: type|kind, event_id|idempotency_key, nested data|flat,
X-Voronka-* | X-Solo-* | X-Lms-* HMAC (+ Bearer).
"""
from __future__ import annotations

import json
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field, ValidationError
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
    course_id: str = Field(default="", max_length=200)
    commerce_product_id: str = Field(default="", max_length=200)
    keycloak_sub: str = Field(default="", max_length=200)
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


def _merge_envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept nested `data` or flat fields; top-level wins on key clash."""
    nested = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    merged = dict(nested)
    for key, value in raw.items():
        if key == "data":
            continue
        merged[key] = value
    return merged


def normalize_lead_body(raw: dict[str, Any]) -> LeadBody:
    """Dual-read solo_pr lead.created: type|kind, idempotency_key|event_id, data|flat."""
    if not isinstance(raw, dict):
        raise HTTPException(422, "Ожидался JSON-объект")
    nested = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    merged = _merge_envelope(raw)
    event_id = str(merged.get("event_id") or merged.get("idempotency_key") or "")
    envelope_type = raw.get("type") or raw.get("kind") or nested.get("type") or "lead.created"
    if str(envelope_type) != "lead.created":
        raise HTTPException(422, "Ожидался lead.created")
    service = str(merged.get("service") or "")
    if not service:
        # Nested kind may be a service label when envelope type is lead.created.
        nested_kind = nested.get("kind")
        if nested_kind and str(nested_kind) != "lead.created":
            service = str(nested_kind)
    try:
        return LeadBody(
            event_id=event_id,
            kind="lead.created",
            contact_type=str(merged.get("contact_type") or ""),
            contact=str(merged.get("contact") or ""),
            email=str(merged.get("email") or ""),
            name=str(merged.get("name") or ""),
            service=service,
            message=str(merged.get("message") or ""),
            consent=bool(merged.get("consent", False)),
            ads_consent=bool(merged.get("ads_consent", False)),
            created=str(merged.get("created") or ""),
            source="solo_pr",
            project_id=merged.get("project_id"),
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


def normalize_completion_body(raw: dict[str, Any]) -> CompletionBody:
    """Dual-read LMS learning.completed: course_id / commerce_product_id / keycloak_sub."""
    if not isinstance(raw, dict):
        raise HTTPException(422, "Ожидался JSON-объект")
    merged = _merge_envelope(raw)
    event_id = str(merged.get("event_id") or merged.get("idempotency_key") or "")
    envelope_type = merged.get("type") or merged.get("kind") or "learning.completed"
    if str(envelope_type) not in {"learning.completed", "completion", "course.completed"}:
        # Soft-accept if LMS omits kind but sends course markers.
        if not (merged.get("course_id") or merged.get("commerce_product_id") or merged.get("resource")):
            raise HTTPException(422, "Ожидался learning.completed")
    resource = str(
        merged.get("resource")
        or merged.get("course_id")
        or merged.get("commerce_product_id")
        or ""
    )
    try:
        return CompletionBody(
            event_id=event_id,
            kind="learning.completed",
            voronka_profile_id=merged.get("voronka_profile_id"),
            email=str(merged.get("email") or ""),
            resource=resource,
            course_id=str(merged.get("course_id") or ""),
            commerce_product_id=str(merged.get("commerce_product_id") or ""),
            keycloak_sub=str(merged.get("keycloak_sub") or ""),
            evidence=str(merged.get("evidence") or ""),
            source="lms_end",
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


def _parse_json_body(raw: bytes) -> dict[str, Any]:
    if len(raw) > 100_000:
        raise HTTPException(413, "Слишком большой payload")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, "Некорректный JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "Ожидался JSON-объект")
    return payload


def _resource_markers(body: CompletionBody) -> set[str]:
    return {m for m in {body.resource, body.course_id, body.commerce_product_id} if m}


def _find_profile_by_keycloak(db, keycloak_sub: str) -> Profile | None:
    if not keycloak_sub:
        return None
    # Extra is JSON; filter in Python for SQLite/Postgres portability on small CRM sets.
    for profile in db.scalars(select(Profile)).all():
        extra = profile.extra if isinstance(profile.extra, dict) else {}
        if str(extra.get("keycloak_sub") or "") == keycloak_sub:
            return profile
    return None


@router.post("/integrations/solo_pr/leads")
async def inbound_lead(request: Request):
    raw = await _require_integration(request)
    payload = _parse_json_body(raw)
    body = normalize_lead_body(payload)
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
async def inbound_completion(request: Request):
    raw = await _require_integration(request)
    payload = _parse_json_body(raw)
    body = normalize_completion_body(payload)
    markers = _resource_markers(body)
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
        if profile is None and body.keycloak_sub:
            profile = _find_profile_by_keycloak(db, body.keycloak_sub)
        if profile is None:
            raise HTTPException(404, "Профиль не найден")
        # Mark open course orders academic_complete when resource/course markers match snapshot/product.
        orders = list(db.scalars(select(Order).where(Order.profile_id == profile.id, Order.state == "open")))
        updated = []
        for order in orders:
            snap = order.snapshot or {}
            order_markers = {
                snap.get("name", ""),
                order.product_id,
                snap.get("kind", ""),
                snap.get("course_id", ""),
                (snap.get("config") or {}).get("course_id", "") if isinstance(snap.get("config"), dict) else "",
            }
            if markers and markers.isdisjoint({m for m in order_markers if m}):
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
                    body=(body.evidence or body.resource or body.course_id or "Обучение завершено")[:20000],
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
        "wire_contract": "v1-dual-read-dual-emit",
    }
