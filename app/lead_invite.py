"""VR-424 / VR-433: course-lead Telegram channel invite via Solo_pr_bot Outbox (not Signal CE).

Telegram cannot cold-DM by @username. Flow:
1) lead.created + telegram ContactPoint + marketing consent → queue Outbox if Identity
   already exists for Solo_pr connection; else store pending invite on profile.extra.
2) On first private message /start to that bot → match pending by:
   - deep-link start payload (`inv_<token>`), or
   - Telegram username / numeric id, or
   - ContactPoint.normalized_value for any Person on the project,
   then enqueue Outbox DM with channel URL + unsubscribe link.

External leads only get a DM after their /start reaches this bot's webhook (not the
bot creator alone). Staff UI must show that /start is required.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse

from sqlalchemy import select

from app.db import Connection, Event, Identity, Outbox, now

PENDING_KEY = "pending_tg_channel_invite"
BOT_USERNAME_HINTS = ("solo_pr_bot", "solo_pr")
DEFAULT_CHANNEL_URL = "https://t.me/+45nxMJzQ-X4wZTUy"
COURSE_TITLE_HINTS = {
    "intensiv-novye-vozmozhnosti-kommunikatsiy": "Интенсив «Новые возможности коммуникаций»",
}
START_PAYLOAD_PREFIX = "inv_"


def _uid() -> str:
    from uuid import uuid4

    return str(uuid4())


def normalize_tg_handle(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("https://t.me/"):
        value = value.rsplit("/", 1)[-1]
    if value.startswith("@"):
        value = value[1:]
    value = value.lower().strip()
    if re.fullmatch(r"-?\d{3,20}", value):
        return value  # numeric telegram id
    if re.fullmatch(r"[a-z0-9_]{3,64}", value):
        return value
    return value


def start_payload_for_lead(lead_event_id: str) -> str:
    """Telegram /start payload (≤64 chars): stable token derived from lead event id."""
    raw = (lead_event_id or "").strip().encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:32]
    return f"{START_PAYLOAD_PREFIX}{digest}"


def webhook_public_domain() -> str:
    """Hostname Telegram should POST webhooks to.

    Prefer VORONKA_PUBLIC_URL (production DNS) over legacy DOMAIN=*.sslip.io —
    Telegram has timed out delivering updates to the sslip hostname even when
    the same IP serves voronka.solo-pr.ru.
    """
    for key in ("VORONKA_PUBLIC_URL", "CRM_PUBLIC_URL"):
        base = (os.getenv(key) or "").strip()
        if not base:
            continue
        host = urlparse(base if "://" in base else f"https://{base}").hostname
        if host and "sslip.io" not in host.lower():
            return host
    domain = (os.getenv("DOMAIN") or "").strip()
    if domain and domain != "funnels.example.ru":
        return domain
    return ""


def bot_start_url(connection: Connection, *, start_payload: str | None = None) -> str | None:
    cfg = connection.config if isinstance(connection.config, dict) else {}
    username = str(cfg.get("username") or "").strip().lstrip("@")
    if not username:
        return None
    if start_payload:
        token = re.sub(r"[^A-Za-z0-9_-]", "", start_payload)[:64]
        if token:
            return f"https://t.me/{username}?start={token}"
    return f"https://t.me/{username}"


def channel_invite_url() -> str:
    return (os.getenv("SOLO_PR_CHANNEL_INVITE_URL") or DEFAULT_CHANNEL_URL).strip() or DEFAULT_CHANNEL_URL


def course_title(service: str | None) -> str:
    raw = (service or "").strip()
    if not raw:
        return "курс Solo-PR"
    hint = COURSE_TITLE_HINTS.get(raw.lower())
    if hint:
        return hint
    if re.fullmatch(r"[a-z0-9_-]+", raw.lower()) and "-" in raw:
        # slug → readable fallback; known courses prefer COURSE_TITLE_HINTS
        return raw.replace("-", " ").strip()
    return raw


def _signing_secret() -> bytes:
    for key in (
        "UNSUBSCRIBE_SIGNING_KEY",
        "INTEGRATION_INBOUND_TOKEN",
        "SOLO_PR_WEBHOOK_SECRET",
        "SIGNAL_M2M_SECRET",
    ):
        value = (os.getenv(key) or "").strip()
        if value:
            return value.encode("utf-8")
    # Dev/tests only — production always has one of the above.
    return b"voronka-dev-unsub"


def public_base_url() -> str:
    for key in ("CRM_PUBLIC_URL", "VORONKA_PUBLIC_URL"):
        base = (os.getenv(key) or "").strip().rstrip("/")
        if base:
            return base
    domain = (os.getenv("DOMAIN") or "").strip()
    if domain and domain != "funnels.example.ru":
        return f"https://{domain}"
    return "https://crm.solo-pr.ru"


def build_unsubscribe_url(contact_point_id: str) -> str:
    """Signed marketing-revoke link shown in invite footer."""
    exp = int((datetime.now(timezone.utc) + timedelta(days=90)).timestamp())
    payload = {"cp": contact_point_id, "p": "marketing", "exp": exp}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{public_base_url()}/u/marketing/{quote(body)}.{sig}"


def verify_unsubscribe_token(token: str) -> dict[str, Any] | None:
    token = (token or "").strip()
    if "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if payload.get("p") != "marketing":
        return None
    exp = int(payload.get("exp") or 0)
    if exp < int(datetime.now(timezone.utc).timestamp()):
        return None
    cp = str(payload.get("cp") or "").strip()
    if not cp:
        return None
    return {"contact_point_id": cp, "purpose": "marketing"}


def build_invite_text(*, course: str, channel_url: str, unsubscribe_url: str | None = None) -> str:
    text = (
        f"Добрый день! Вы интересовались курсом «{course}». "
        "Мы с радостью приглашаем вас в сообщество Solo-PR специалистов "
        "и просим подключиться к каналу в Telegram для оперативного получения "
        f"информации о курсе: {channel_url}"
    )
    if unsubscribe_url:
        text += f"\n\nОтписаться от рассылки: {unsubscribe_url}"
    return text


def resolve_solo_pr_telegram_connection(db, project_id: str) -> Connection | None:
    """Prefer explicit env / Solo_pr_bot username / TG_SOLO_PR_TOKEN over other TG bots."""
    forced = (os.getenv("SOLO_PR_TELEGRAM_CONNECTION_ID") or "").strip()
    if forced:
        row = db.get(Connection, forced)
        if row and row.platform == "telegram" and row.project_id == project_id and row.active:
            return row

    rows = list(
        db.scalars(
            select(Connection).where(
                Connection.project_id == project_id,
                Connection.platform == "telegram",
                Connection.active.is_(True),
            )
        )
    )
    if not rows:
        return None

    def score(c: Connection) -> tuple[int, str]:
        cfg = c.config if isinstance(c.config, dict) else {}
        username = str(cfg.get("username") or "").lower()
        token_env = (c.token_env or "").upper()
        name = (c.name or "").lower()
        points = 0
        if username in {"solo_pr_bot"} or username.replace("_", "") == "soloprbot":
            points += 100
        if token_env == "TG_SOLO_PR_TOKEN" or token_env.startswith("TG_SOLO_PR_"):
            points += 80
        if any(h in username for h in BOT_USERNAME_HINTS):
            points += 40
        if "solo" in name and "pr" in name:
            points += 20
        if os.environ.get(c.token_env or ""):
            points += 5
        return (points, c.id)

    ranked = sorted(rows, key=score, reverse=True)
    best = ranked[0]
    if score(best)[0] <= 0:
        # No Solo_pr signal — do not hijack unrelated bots (e.g. Prazdnik).
        return None
    return best


def _find_identity_for_lead(
    db, *, profile, connection: Connection, telegram_value: str
) -> Identity | None:
    if not profile or not profile.contact_id:
        return None
    identity = db.scalar(
        select(Identity).where(
            Identity.contact_id == profile.contact_id,
            Identity.connection_id == connection.id,
        )
    )
    if identity:
        return identity
    handle = normalize_tg_handle(telegram_value)
    if handle and re.fullmatch(r"-?\d{3,20}", handle):
        return db.scalar(
            select(Identity).where(
                Identity.connection_id == connection.id,
                Identity.external_user_id == handle,
            )
        )
    return None


def _enqueue_invite(
    db,
    *,
    connection: Connection,
    identity: Identity,
    text: str,
    lead_event_id: str,
) -> Outbox:
    # Idempotent: one pending/sent invite per lead event + connection.
    existing = db.scalar(
        select(Outbox)
        .where(
            Outbox.connection_id == connection.id,
            Outbox.identity_id == identity.id,
            Outbox.status.in_(["pending", "sending", "sent", "unknown"]),
        )
        .order_by(Outbox.created_at.desc())
        .limit(50)
    )
    # Scan recent payloads for same lead event marker.
    for row in db.scalars(
        select(Outbox)
        .where(Outbox.connection_id == connection.id, Outbox.identity_id == identity.id)
        .order_by(Outbox.created_at.desc())
        .limit(30)
    ):
        payload = row.payload if isinstance(row.payload, dict) else {}
        if payload.get("lead_event_id") == lead_event_id and payload.get("kind") == "course_channel_invite":
            return row

    out = Outbox(
        id=_uid(),
        connection_id=connection.id,
        identity_id=identity.id,
        chat_id=identity.chat_id,
        payload={
            "type": "text",
            "text": text,
            "kind": "course_channel_invite",
            "lead_event_id": lead_event_id,
            "buttons": [{"text": "Открыть канал", "url": channel_invite_url()}],
        },
        status="pending",
        created_at=now(),
        due_at=now(),
        updated_at=now(),
    )
    db.add(out)
    db.add(
        Event(
            id=_uid(),
            kind="lead.channel_invite.queued",
            connection_id=connection.id,
            contact_id=identity.contact_id,
            payload={"outbox_id": out.id, "lead_event_id": lead_event_id},
            created_at=now(),
        )
    )
    db.flush()
    return out


def schedule_course_channel_invite(
    db,
    *,
    profile,
    project_id: str,
    telegram_value: str,
    contact_point_id: str,
    ads_consent: bool,
    consent: bool,
    service: str | None,
    lead_event_id: str,
) -> dict[str, Any]:
    """Call after ContactPoint + consent write for a telegram channel on inbound lead."""
    if not consent or not ads_consent:
        return {"status": "skipped", "reason": "no_marketing_consent"}
    telegram_value = (telegram_value or "").strip()
    if not telegram_value:
        return {"status": "skipped", "reason": "no_telegram"}
    connection = resolve_solo_pr_telegram_connection(db, project_id)
    if connection is None:
        return {"status": "skipped", "reason": "no_solo_pr_telegram_connection"}
    if not os.environ.get(connection.token_env or ""):
        return {"status": "skipped", "reason": "token_missing"}

    from app import person_graph

    allowed, reason = person_graph.send_allowed(
        db,
        person_id=profile.person_id,
        contact_point_id=contact_point_id,
        channel="telegram",
        purpose="marketing",
        connection_id=connection.id,
        missing_policy="deny",
    )
    if not allowed:
        return {"status": "skipped", "reason": f"send_policy:{reason}"}

    title = course_title(service)
    channel_url = channel_invite_url()
    unsub = build_unsubscribe_url(contact_point_id)
    text = build_invite_text(course=title, channel_url=channel_url, unsubscribe_url=unsub)

    identity = _find_identity_for_lead(
        db, profile=profile, connection=connection, telegram_value=telegram_value
    )
    handle = normalize_tg_handle(telegram_value)
    start_payload = start_payload_for_lead(lead_event_id)
    deep_link = bot_start_url(connection, start_payload=start_payload)
    pending = {
        "connection_id": connection.id,
        "contact_point_id": contact_point_id,
        "telegram": handle,
        "course_title": title,
        "channel_url": channel_url,
        "lead_event_id": lead_event_id,
        "start_payload": start_payload,
        "bot_start_url": deep_link,
        "created_at": now().isoformat(),
    }

    if identity is not None:
        # Prefer attaching identity to lead contact when numeric id matched another contact.
        if identity.contact_id != profile.contact_id:
            identity.contact_id = profile.contact_id
            db.flush()
        out = _enqueue_invite(
            db,
            connection=connection,
            identity=identity,
            text=text,
            lead_event_id=lead_event_id,
        )
        extra = dict(profile.extra) if isinstance(profile.extra, dict) else {}
        extra.pop(PENDING_KEY, None)
        extra["last_tg_channel_invite"] = {
            **pending,
            "outbox_id": out.id,
            "status": "queued",
        }
        profile.extra = extra
        return {
            "status": "queued",
            "outbox_id": out.id,
            "connection_id": connection.id,
            "bot_start_url": deep_link,
            "start_payload": start_payload,
        }

    extra = dict(profile.extra) if isinstance(profile.extra, dict) else {}
    extra[PENDING_KEY] = pending
    profile.extra = extra
    db.add(
        Event(
            id=_uid(),
            kind="lead.channel_invite.pending",
            connection_id=connection.id,
            contact_id=profile.contact_id,
            payload=pending,
            created_at=now(),
        )
    )
    db.flush()
    return {
        "status": "pending",
        "connection_id": connection.id,
        "telegram": handle,
        "bot_start_url": deep_link,
        "start_payload": start_payload,
        "requires_start": True,
    }


def _pending_profiles_for_project(db, project_id: str) -> list:
    """All project profiles that still wait for a Solo_pr course invite flush."""
    from app.crm_models import Profile

    # Avoid silent drops: scan all project profiles with a pending invite marker.
    # (JSON path filters vary by dialect; project lead volume stays modest.)
    out = []
    for profile in db.scalars(select(Profile).where(Profile.project_id == project_id)):
        extra = profile.extra if isinstance(profile.extra, dict) else {}
        pending = extra.get(PENDING_KEY)
        if isinstance(pending, dict):
            out.append(profile)
    return out


def flush_pending_channel_invites(
    db,
    *,
    identity: Identity,
    connection: Connection,
    telegram_username: str | None = None,
    start_payload: str | None = None,
) -> list[str]:
    """On /start (or first DM) to Solo_pr bot: deliver deferred course channel invites."""
    if connection.platform != "telegram" or not connection.project_id:
        return []
    # Only Solo_pr-bound connection (or forced id) flushes course invites.
    solo = resolve_solo_pr_telegram_connection(db, connection.project_id)
    if solo is None or solo.id != connection.id:
        return []

    from app.crm_models import ContactPoint, Profile
    from app import person_graph

    handle = normalize_tg_handle(telegram_username or "")
    numeric = str(identity.external_user_id or "")
    start_token = re.sub(r"[^A-Za-z0-9_-]", "", (start_payload or "").strip())[:64]
    queued: list[str] = []

    candidates: list[Profile] = []
    seen: set[str] = set()

    def _add(profile: Profile) -> None:
        if profile.id in seen:
            return
        seen.add(profile.id)
        candidates.append(profile)

    for profile in _pending_profiles_for_project(db, connection.project_id):
        extra = profile.extra if isinstance(profile.extra, dict) else {}
        pending = extra.get(PENDING_KEY)
        if not isinstance(pending, dict):
            continue
        if pending.get("connection_id") and pending.get("connection_id") != connection.id:
            continue
        pending_tg = normalize_tg_handle(str(pending.get("telegram") or ""))
        pending_start = str(pending.get("start_payload") or "")
        matched = False
        if start_token and pending_start and start_token == pending_start:
            matched = True
        elif pending_tg and handle and pending_tg == handle:
            matched = True
        elif pending_tg and pending_tg == numeric:
            matched = True
        elif profile.contact_id == identity.contact_id:
            matched = True
        if matched:
            _add(profile)

    # Also match ContactPoint.normalized_value (may still carry leading @).
    if handle or numeric:
        needles = {v for v in (handle, numeric, f"@{handle}" if handle else "") if v}
        for cp in db.scalars(
            select(ContactPoint).where(ContactPoint.channel == "telegram")
        ):
            cp_norm = normalize_tg_handle(cp.normalized_value or cp.raw_value or "")
            if not cp_norm or cp_norm not in {normalize_tg_handle(n) for n in needles}:
                continue
            for profile in db.scalars(
                select(Profile).where(
                    Profile.project_id == connection.project_id,
                    Profile.person_id == cp.person_id,
                )
            ):
                extra = profile.extra if isinstance(profile.extra, dict) else {}
                if isinstance(extra.get(PENDING_KEY), dict):
                    pending = extra[PENDING_KEY]
                    if pending.get("connection_id") and pending.get("connection_id") != connection.id:
                        continue
                    _add(profile)

    for profile in candidates:
        extra = dict(profile.extra) if isinstance(profile.extra, dict) else {}
        pending = dict(extra.get(PENDING_KEY) or {})
        cp_id = str(pending.get("contact_point_id") or "")
        if not cp_id and profile.person_id:
            # Fallback: any telegram CP on person.
            cp = db.scalar(
                select(ContactPoint).where(
                    ContactPoint.person_id == profile.person_id,
                    ContactPoint.channel == "telegram",
                )
            )
            cp_id = cp.id if cp else ""
        if not cp_id:
            continue

        allowed, reason = person_graph.send_allowed(
            db,
            person_id=profile.person_id,
            contact_point_id=cp_id,
            channel="telegram",
            purpose="marketing",
            connection_id=connection.id,
            missing_policy="deny",
        )
        if not allowed:
            extra[PENDING_KEY] = {**pending, "blocked_reason": reason}
            profile.extra = extra
            continue

        # Attach dialog Identity to the lead CRM contact when safe.
        if identity.contact_id != profile.contact_id:
            other = db.scalar(
                select(Identity).where(
                    Identity.contact_id == profile.contact_id,
                    Identity.connection_id == connection.id,
                )
            )
            if other is None:
                identity.contact_id = profile.contact_id
                db.flush()

        cp = db.get(ContactPoint, cp_id)
        if cp is not None:
            cp.connection_id = connection.id
            cp.identity_id = identity.id
            cp.verified = True

        title = str(pending.get("course_title") or course_title(None))
        channel_url = str(pending.get("channel_url") or channel_invite_url())
        unsub = build_unsubscribe_url(cp_id)
        text = build_invite_text(course=title, channel_url=channel_url, unsubscribe_url=unsub)
        lead_event_id = str(pending.get("lead_event_id") or f"flush:{identity.id}")
        out = _enqueue_invite(
            db,
            connection=connection,
            identity=identity,
            text=text,
            lead_event_id=lead_event_id,
        )
        extra.pop(PENDING_KEY, None)
        extra["last_tg_channel_invite"] = {
            **pending,
            "outbox_id": out.id,
            "status": "queued",
            "flushed_at": now().isoformat(),
        }
        profile.extra = extra
        queued.append(out.id)

    db.flush()
    return queued
