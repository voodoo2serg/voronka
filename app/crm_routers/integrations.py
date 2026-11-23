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


LEAD_SOURCES = Literal["solo_pr", "raga", "raga_site"]


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
    source: LEAD_SOURCES = "solo_pr"
    project_id: str | None = None
    promo_code: str = Field(default="", max_length=40)
    # VR-408: optional messenger extras when primary contact is email.
    telegram: str = Field(default="", max_length=250)
    max: str = Field(default="", max_length=250)
    # ARCH-M2: solo/learner handoff — optional verified Keycloak sub (not email).
    keycloak_sub: str = Field(default="", max_length=200)
    issuer: str = Field(default="", max_length=250)


class CompletionBody(Body):
    event_id: str = Field(min_length=4, max_length=160)
    kind: Literal["learning.completed", "learning.progress", "progress.updated"] = "learning.completed"
    voronka_profile_id: str | None = None
    email: str = Field(default="", max_length=250)
    resource: str = Field(default="", max_length=200)
    course_id: str = Field(default="", max_length=200)
    commerce_product_id: str = Field(default="", max_length=200)
    keycloak_sub: str = Field(default="", max_length=200)
    evidence: str = Field(default="", max_length=2000)
    progress_percent: int | None = None
    completed: bool = False
    source: Literal["lms_end"] = "lms_end"


class LmsPersonBody(Body):
    """A9-05: first LMS/solo learner SSO → Person+OIDC (no Account, no email merge)."""

    keycloak_sub: str = Field(min_length=4, max_length=200)
    issuer: str = Field(default="", max_length=250)
    email: str = Field(default="", max_length=250)
    name: str = Field(default="", max_length=200)
    voronka_profile_id: str | None = None
    event_id: str = Field(default="", max_length=160)


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


def _normalize_lead_source(raw_source: Any) -> str:
    value = str(raw_source or "solo_pr").strip().lower()
    aliases = {
        "solo": "solo_pr",
        "solo-pr": "solo_pr",
        "solo_pr": "solo_pr",
        "raga": "raga",
        "raga_site": "raga_site",
        "raga-site": "raga_site",
        "rag_for_ai": "raga",
    }
    source = aliases.get(value, value)
    if source not in {"solo_pr", "raga", "raga_site"}:
        raise HTTPException(422, "source должен быть solo_pr|raga|raga_site")
    return source


def normalize_lead_body(raw: dict[str, Any]) -> LeadBody:
    """Dual-read lead.created (solo_pr / RAGA): type|kind, idempotency_key|event_id, data|flat."""
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
    source = _normalize_lead_source(merged.get("source") or nested.get("source") or "solo_pr")
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
            source=source,  # type: ignore[arg-type]
            project_id=merged.get("project_id"),
            promo_code=str(merged.get("promo_code") or merged.get("promo") or "")[:40],
            telegram=str(merged.get("telegram") or "")[:250],
            max=str(merged.get("max") or "")[:250],
            keycloak_sub=str(merged.get("keycloak_sub") or merged.get("sub") or "")[:200],
            issuer=str(merged.get("issuer") or "")[:250],
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


def _default_issuer() -> str:
    return (os.getenv("KEYCLOAK_ISSUER") or "https://auth.solo-pr.ru/realms/solo-pr").rstrip("/")


def _attach_learner_oidc(
    db,
    profile: Profile,
    *,
    keycloak_sub: str,
    issuer: str = "",
    email_hint: str = "",
    display_name: str = "",
) -> str | None:
    """ARCH-M2: grow Person/issuer+sub coverage for solo/LMS learners (no Account)."""
    sub = (keycloak_sub or "").strip()
    if not sub or "@" in sub:
        return None
    from app import person_graph

    iss = (issuer or "").rstrip("/") or _default_issuer()
    if not profile.person_id:
        person_graph.ensure_person_for_profile(db, profile)
    link, person, _changed = person_graph.ensure_oidc_person(
        db,
        issuer=iss,
        subject=sub,
        display_name=display_name or profile.name or "",
        email_hint=email_hint or profile.email or "",
        contact_id=profile.contact_id,
        profile_id=profile.id,
    )
    if profile.person_id != person.id:
        profile.person_id = person.id
    extra = dict(profile.extra) if isinstance(profile.extra, dict) else {}
    extra["keycloak_sub"] = sub
    extra["oidc_issuer"] = link.issuer
    profile.extra = extra
    return person.id


def _is_progress_envelope(envelope_type: str) -> bool:
    return str(envelope_type) in {
        "learning.progress",
        "progress.updated",
        "progress",
        "course.progress",
    }


def _is_completion_envelope(envelope_type: str) -> bool:
    return str(envelope_type) in {
        "learning.completed",
        "completion",
        "course.completed",
    }


def normalize_completion_body(raw: dict[str, Any]) -> CompletionBody:
    """Dual-read LMS learning.completed / progress.updated → History.

    Accepts course_id / commerce_product_id / keycloak_sub aliases from lms_end outbound.
    """
    if not isinstance(raw, dict):
        raise HTTPException(422, "Ожидался JSON-объект")
    merged = _merge_envelope(raw)
    event_id = str(merged.get("event_id") or merged.get("idempotency_key") or "")
    envelope_type = str(merged.get("type") or merged.get("kind") or "learning.completed")
    is_progress = _is_progress_envelope(envelope_type)
    is_completion = _is_completion_envelope(envelope_type)
    if not is_progress and not is_completion:
        # Soft-accept if LMS omits kind but sends course markers (treat as completion).
        if not (merged.get("course_id") or merged.get("commerce_product_id") or merged.get("resource")):
            raise HTTPException(422, "Ожидался learning.completed или progress.updated")
        is_completion = True
    resource = str(
        merged.get("resource")
        or merged.get("course_id")
        or merged.get("commerce_product_id")
        or ""
    )
    kind: Literal["learning.completed", "learning.progress", "progress.updated"]
    if is_progress:
        kind = "learning.progress"
    else:
        kind = "learning.completed"
    progress_raw = merged.get("progress_percent")
    progress_percent = None
    if progress_raw is not None and progress_raw != "":
        try:
            progress_percent = max(0, min(100, int(progress_raw)))
        except (TypeError, ValueError):
            progress_percent = None
    try:
        return CompletionBody(
            event_id=event_id,
            kind=kind,
            voronka_profile_id=merged.get("voronka_profile_id"),
            email=str(merged.get("email") or ""),
            resource=resource,
            course_id=str(merged.get("course_id") or ""),
            commerce_product_id=str(merged.get("commerce_product_id") or ""),
            keycloak_sub=str(merged.get("keycloak_sub") or ""),
            evidence=str(merged.get("evidence") or ""),
            progress_percent=progress_percent,
            completed=bool(merged.get("completed", False)) or kind == "learning.completed",
            source="lms_end",
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


def normalize_lms_person_body(raw: dict[str, Any]) -> LmsPersonBody:
    if not isinstance(raw, dict):
        raise HTTPException(422, "Ожидался JSON-объект")
    merged = _merge_envelope(raw)
    sub = str(merged.get("keycloak_sub") or merged.get("subject") or merged.get("sub") or "").strip()
    try:
        return LmsPersonBody(
            keycloak_sub=sub,
            issuer=str(merged.get("issuer") or "")[:250],
            email=str(merged.get("email") or "")[:250],
            name=str(merged.get("name") or merged.get("display_name") or "")[:200],
            voronka_profile_id=merged.get("voronka_profile_id") or merged.get("profile_id"),
            event_id=str(merged.get("event_id") or merged.get("idempotency_key") or "")[:160],
        )
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()) from exc


def _record_learning_history(db, *, profile: Profile, body: CompletionBody) -> None:
    """A9-03: LMS progress/completion → Person Interaction (CRM History, no SQL)."""
    from app import person_graph

    if not getattr(profile, "person_id", None):
        try:
            person_graph.ensure_person_for_profile(db, profile)
        except Exception:
            return
    person_id = getattr(profile, "person_id", None)
    if not person_id:
        return
    is_progress = body.kind in {"learning.progress", "progress.updated"}
    kind = "learning.progress" if is_progress else "learning.completed"
    course = body.course_id or body.resource or body.commerce_product_id or ""
    percent = body.progress_percent
    title_bits = []
    if course:
        title_bits.append(course)
    if is_progress and percent is not None:
        title_bits.append(f"{percent}%")
    evidence = body.evidence or ("Прогресс по курсу" if is_progress else "Курс завершён")
    person_graph.record_interaction(
        db,
        person_id=person_id,
        kind=kind,
        direction="system",
        channel="lms_end",
        project_id=profile.project_id,
        source_service="lms_end",
        external_event_key=f"{kind}:{body.event_id}",
        payload={
            "stage": "course",
            "stage_label": "Курс",
            "course_id": body.course_id or "",
            "resource": body.resource or "",
            "commerce_product_id": body.commerce_product_id or "",
            "progress_percent": percent,
            "completed": bool(body.completed) or not is_progress,
            "evidence": evidence[:500],
            "keycloak_sub": body.keycloak_sub or "",
            "summary": " · ".join(title_bits) if title_bits else evidence[:120],
        },
        evidence_status="recorded",
    )


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


def _lead_channel_values(body: LeadBody) -> tuple[str, str, str, str]:
    """Map solo_pr contact_type → email / phone / telegram / max values.

    Site form uses contact_type email|telegram|max (not phone). MAX may be a
    phone or https://max.ru/… link; only phones go into Profile.phone.
    Optional body.telegram / body.max ride alongside an email primary contact.
    """
    ct = (body.contact_type or "").strip().lower()
    raw = (body.contact or "").strip()
    email = body.email or (raw if ct == "email" else "")
    phone = raw if ct == "phone" else ""
    telegram = (body.telegram or "").strip() or (raw if ct == "telegram" else "")
    max_value = (body.max or "").strip() or (raw if ct == "max" else "")
    if ct == "max" and raw and not raw.lower().startswith("http") and not phone:
        # Phone-shaped MAX contact also fills Profile.phone for lookup.
        phone = raw
    if max_value and not max_value.lower().startswith("http") and not phone:
        phone = max_value
    try:
        email = normalize_email(email)
        phone = normalize_phone(phone)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if ct in {"telegram", "max"} and not (telegram or max_value):
        raise HTTPException(422, "Укажите контакт Telegram или MAX")
    if ct == "email" and not email:
        raise HTTPException(422, "Укажите email")
    if not any((email, phone, telegram, max_value)):
        raise HTTPException(422, "Укажите контакт")
    return email, phone, telegram, max_value


def _grant_lead_consent(db, contact_point_id: str, *, source: str, event_id: str, ads_consent: bool) -> None:
    from app import person_graph

    evidence = f"{source} lead.created:{event_id}"
    person_graph.set_consent(
        db, contact_point_id, purpose="transactional", status="granted", evidence=evidence
    )
    person_graph.set_consent(
        db, contact_point_id, purpose="service", status="granted", evidence=evidence
    )
    person_graph.set_consent(
        db,
        contact_point_id,
        purpose="marketing",
        status="granted" if ads_consent else "denied",
        evidence=evidence,
    )


def _apply_inbound_lead(body: LeadBody) -> dict[str, Any]:
    """Upsert Person/Profile/Deal from any marketing lead source (solo_pr / RAGA)."""
    project_id = body.project_id or os.getenv("DEFAULT_PROJECT_ID", "")
    if not project_id:
        raise HTTPException(503, "DEFAULT_PROJECT_ID не задан для входящих лидов")
    email, phone, telegram, max_value = _lead_channel_values(body)
    source = body.source
    default_name = {
        "solo_pr": "Лид сайта",
        "raga": "Лид RAGA",
        "raga_site": "Лид RAGA",
    }.get(source, "Лид")
    with Session.begin() as db:
        prior = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == source, IntegrationInbox.event_key == body.event_id
            )
        )
        if prior:
            person_id = None
            if prior.profile_id:
                prior_profile = db.get(Profile, prior.profile_id)
                if prior_profile:
                    person_id = prior_profile.person_id
            return {
                "ok": True,
                "duplicate": True,
                "profile_id": prior.profile_id,
                "inbox_id": prior.id,
                "person_id": person_id,
            }
        profile = None
        if email:
            profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.email == email))
        if profile is None and phone:
            profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.phone == phone))
        lead_extra = {
            "lead_event_id": body.event_id,
            "contact_type": body.contact_type,
            "contact": body.contact,
            "service": body.service,
            "consent": body.consent,
            "ads_consent": body.ads_consent,
            "created": body.created,
            "lead_source": source,
            "promo_code": body.promo_code,
        }
        if telegram:
            lead_extra["telegram"] = telegram
        if max_value:
            lead_extra["max"] = max_value
        display = body.name or email or phone or telegram or max_value or default_name
        if profile is None:
            profile = create_profile(
                db,
                project_id,
                {
                    "name": display,
                    "email": email,
                    "phone": phone,
                    "source": source,
                    "extra": lead_extra,
                },
            )
        else:
            # Existing profile: still refresh Person/ContactPoints (CRM-101).
            from app import person_graph

            person_graph.sync_profile_contact_points(db, profile)
            # Copy before mutate: plain JSON columns are not mutable-tracked.
            extra = dict(profile.extra) if isinstance(profile.extra, dict) else {}
            extra.update(lead_extra)
            profile.extra = extra
            if phone and not profile.phone:
                profile.phone = phone
            if email and not profile.email:
                profile.email = email
        # CRM-102 / MAIL-101: record form consent onto every submitted ContactPoint.
        from app import person_graph

        person = person_graph.sync_profile_contact_points(db, profile)
        channel_points: list[tuple[str, str]] = []
        if email:
            channel_points.append(("email", email))
        if phone:
            channel_points.append(("phone", phone))
        if telegram:
            channel_points.append(("telegram", telegram))
        if max_value:
            channel_points.append(("max", max_value))
        telegram_cp_id = None
        for channel, value in channel_points:
            cp = person_graph.upsert_contact_point(
                db, person, channel=channel, value=value, verified=False
            )
            if cp and body.consent:
                _grant_lead_consent(
                    db,
                    cp.id,
                    source=source,
                    event_id=body.event_id,
                    ads_consent=body.ads_consent,
                )
            if cp and channel == "telegram":
                telegram_cp_id = cp.id
        # VR-424: Telegram channel invite via Solo_pr_bot Outbox (never Signal CE).
        if telegram and telegram_cp_id and body.consent and body.ads_consent:
            from app import lead_invite

            lead_invite.schedule_course_channel_invite(
                db,
                profile=profile,
                project_id=project_id,
                telegram_value=telegram,
                contact_point_id=telegram_cp_id,
                ads_consent=body.ads_consent,
                consent=body.consent,
                service=body.service,
                lead_event_id=body.event_id,
            )
        # ARCH-M2: solo handoff with verified Keycloak sub → Person coverage (no staff Account).
        if body.keycloak_sub:
            _attach_learner_oidc(
                db,
                profile,
                keycloak_sub=body.keycloak_sub,
                issuer=body.issuer,
                email_hint=email or "",
                display_name=body.name or "",
            )
        deal_title = body.service or ("RAGA signup" if source.startswith("raga") else "Заявка с сайта")
        if body.promo_code and source.startswith("raga"):
            deal_title = f"{deal_title} ({body.promo_code})"
        db.add(
            Deal(
                id=uid(),
                project_id=project_id,
                profile_id=profile.id,
                title=deal_title[:200],
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
                    channel=source,
                    outcome="lead.created",
                    body=body.message[:20000],
                )
            )
        row = IntegrationInbox(
            id=uid(),
            source=source,
            event_key=body.event_id,
            kind="lead.created",
            payload=body.model_dump(),
            state="applied",
            profile_id=profile.id,
            created_at=now(),
        )
        db.add(row)
        db.flush()
        return {
            "ok": True,
            "duplicate": False,
            "profile_id": profile.id,
            "person_id": profile.person_id,
            "inbox_id": row.id,
        }


@router.post("/integrations/solo_pr/leads")
@router.post("/integrations/leads")
async def inbound_lead(request: Request):
    raw = await _require_integration(request)
    payload = _parse_json_body(raw)
    body = normalize_lead_body(payload)
    return _apply_inbound_lead(body)


def _resolve_lms_profile(db, body: CompletionBody | LmsPersonBody) -> Profile | None:
    profile = None
    profile_id = getattr(body, "voronka_profile_id", None)
    if profile_id:
        profile = db.get(Profile, profile_id)
    email = getattr(body, "email", "") or ""
    if profile is None and email:
        try:
            normalized = normalize_email(email)
        except ValueError:
            raise HTTPException(422, "Некорректный email")
        profile = db.scalar(select(Profile).where(Profile.email == normalized))
    keycloak_sub = getattr(body, "keycloak_sub", "") or ""
    if profile is None and keycloak_sub:
        profile = _find_profile_by_keycloak(db, keycloak_sub)
    return profile


def _apply_lms_learning_event(body: CompletionBody) -> dict[str, Any]:
    """A9-03: LMS outbound progress/completion → inbox + History (+ academic on complete)."""
    markers = _resource_markers(body)
    is_progress = body.kind in {"learning.progress", "progress.updated"}
    inbox_kind = "learning.progress" if is_progress else "learning.completed"
    with Session.begin() as db:
        prior = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == "lms_end", IntegrationInbox.event_key == body.event_id
            )
        )
        if prior:
            return {"ok": True, "duplicate": True, "kind": prior.kind}
        profile = _resolve_lms_profile(db, body)
        if profile is None:
            raise HTTPException(404, "Профиль не найден")
        # ARCH-M2 / A9-05: LMS learner sub → Person/issuer+sub (coverage growth; no Account).
        if body.keycloak_sub:
            _attach_learner_oidc(
                db,
                profile,
                keycloak_sub=body.keycloak_sub,
                email_hint=body.email or profile.email or "",
                display_name=profile.name or "",
            )
        updated: list[str] = []
        if not is_progress:
            orders = list(
                db.scalars(select(Order).where(Order.profile_id == profile.id, Order.state == "open"))
            )
            for order in orders:
                snap = order.snapshot or {}
                order_markers = {
                    snap.get("name", ""),
                    order.product_id,
                    snap.get("kind", ""),
                    snap.get("course_id", ""),
                    (snap.get("config") or {}).get("course_id", "")
                    if isinstance(snap.get("config"), dict)
                    else "",
                }
                if markers and markers.isdisjoint({m for m in order_markers if m}):
                    if snap.get("kind") != "course":
                        continue
                order.academic_complete = True
                updated.append(order.id)
        from app.db import Admin

        owner = db.scalar(select(Admin).where(Admin.role == "owner", Admin.active.is_(True)))
        if owner:
            outcome = "learning.progress" if is_progress else "learning.completed"
            percent_bit = (
                f" · {body.progress_percent}%"
                if is_progress and body.progress_percent is not None
                else ""
            )
            db.add(
                Activity(
                    id=uid(),
                    profile_id=profile.id,
                    actor_id=owner.telegram_id,
                    kind="learning",
                    channel="lms_end",
                    outcome=outcome,
                    body=(
                        body.evidence
                        or body.resource
                        or body.course_id
                        or ("Прогресс по курсу" if is_progress else "Обучение завершено")
                    )[:20000]
                    + percent_bit,
                )
            )
        _record_learning_history(db, profile=profile, body=body)
        row = IntegrationInbox(
            id=uid(),
            source="lms_end",
            event_key=body.event_id,
            kind=inbox_kind,
            payload=body.model_dump(),
            state="applied",
            profile_id=profile.id,
            created_at=now(),
        )
        db.add(row)
        return {
            "ok": True,
            "kind": inbox_kind,
            "orders_updated": updated,
            "profile_id": profile.id,
            "person_id": profile.person_id,
        }


@router.post("/integrations/lms_end/completions")
@router.post("/integrations/lms_end/progress")
async def inbound_completion(request: Request):
    """LMS → CRM: completion and progress share auth; History paints «Курс»."""
    raw = await _require_integration(request)
    payload = _parse_json_body(raw)
    body = normalize_completion_body(payload)
    return _apply_lms_learning_event(body)


@router.post("/integrations/lms_end/person")
async def inbound_lms_person(request: Request):
    """A9-05: first learner SSO / grant handoff → Person+OIDC (no Account, no email merge)."""
    raw = await _require_integration(request)
    payload = _parse_json_body(raw)
    body = normalize_lms_person_body(payload)
    if "@" in body.keycloak_sub:
        raise HTTPException(422, "keycloak_sub должен быть OIDC subject, не email")
    with Session.begin() as db:
        event_key = body.event_id or f"lms.person:{body.keycloak_sub}"
        prior = db.scalar(
            select(IntegrationInbox).where(
                IntegrationInbox.source == "lms_end", IntegrationInbox.event_key == event_key
            )
        )
        if prior and isinstance(prior.payload, dict) and prior.payload.get("person_id"):
            return {
                "ok": True,
                "duplicate": True,
                "person_id": prior.payload.get("person_id"),
                "profile_id": prior.profile_id,
            }
        profile = _resolve_lms_profile(db, body)
        if profile is None and body.email:
            # Optional thin Profile when DEFAULT_PROJECT_ID is set; otherwise Person+OIDC only.
            project_id = os.getenv("DEFAULT_PROJECT_ID", "")
            if project_id:
                try:
                    email = normalize_email(body.email)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
                profile = create_profile(
                    db,
                    project_id,
                    {
                        "name": body.name or email,
                        "email": email,
                        "source": "lms_end_sso",
                    },
                )
        if profile is None:
            # Profile-less ensure still grows learners_with_person via OidcLink alone.
            # Email is ContactPoint hint only — never merges Persons / invents Account.
            from app import person_graph

            link, person, changed = person_graph.ensure_oidc_person(
                db,
                issuer=(body.issuer or "").rstrip("/") or _default_issuer(),
                subject=body.keycloak_sub,
                display_name=body.name or "",
                email_hint=body.email or "",
            )
            if not prior:
                db.add(
                    IntegrationInbox(
                        id=uid(),
                        source="lms_end",
                        event_key=event_key,
                        kind="lms.person",
                        payload={
                            "keycloak_sub": body.keycloak_sub,
                            "person_id": person.id,
                            "oidc_link_id": link.id,
                            "email": body.email,
                        },
                        state="applied",
                        profile_id=None,
                        created_at=now(),
                    )
                )
            return {
                "ok": True,
                "person_id": person.id,
                "oidc_link_id": link.id,
                "account_id": link.account_id,
                "changed": changed,
                "coverage": person_graph.oidc_person_coverage(db),
            }
        person_id = _attach_learner_oidc(
            db,
            profile,
            keycloak_sub=body.keycloak_sub,
            issuer=body.issuer,
            email_hint=body.email or profile.email or "",
            display_name=body.name or profile.name or "",
        )
        if not prior:
            db.add(
                IntegrationInbox(
                    id=uid(),
                    source="lms_end",
                    event_key=event_key,
                    kind="lms.person",
                    payload={
                        "keycloak_sub": body.keycloak_sub,
                        "person_id": person_id or profile.person_id,
                        "email": body.email or profile.email,
                    },
                    state="applied",
                    profile_id=profile.id,
                    created_at=now(),
                )
            )
        from app import person_graph as graph

        return {
            "ok": True,
            "person_id": person_id or profile.person_id,
            "profile_id": profile.id,
            "coverage": graph.oidc_person_coverage(db),
        }


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
def list_outbox(pid: str, state: str | None = None, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session() as db:
        project_access(db, admin, pid)
        q = select(IntegrationOutbox).order_by(IntegrationOutbox.created_at.desc()).limit(200)
        if state:
            q = select(IntegrationOutbox).where(IntegrationOutbox.state == state).order_by(
                IntegrationOutbox.created_at.desc()
            ).limit(200)
        rows = list(db.scalars(q))
        # Filter loosely by payload project_id when present.
        return [
            encode(r)
            for r in rows
            if (r.payload or {}).get("project_id") in {None, pid} or r.destination in {"solo_pr", "lms_end", "signal"}
        ]


@router.get("/integrations/status")
def integration_status(admin=Depends(crm_actor)):
    staff_owner(admin)
    return {
        "solo_pr": {
            "events_url_set": bool(os.getenv("SOLO_PR_EVENTS_URL")),
            "secret_set": bool(os.getenv("SOLO_PR_WEBHOOK_SECRET")),
            "events_url_hint": "https://solo-pr.ru/api/integrations/voronka",
        },
        "leads": {
            "accepted_sources": ["solo_pr", "raga", "raga_site"],
            "paths": ["/api/crm/integrations/solo_pr/leads", "/api/crm/integrations/leads"],
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
