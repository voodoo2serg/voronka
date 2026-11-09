"""Warm-up outreach helpers: Entry deep links, caps, opt-out, Start attribution."""
from datetime import timedelta

from sqlalchemy import select, func

from app.crm_models import Activity, OutreachBatch, OutreachRecipient, Profile
from app.crm_service import uid
from app.db import Connection, Entry, Event, Identity, now


def bot_entry_link(connection: Connection, token: str) -> str:
    """Official bot deep link / payload hint. Never an unofficial account URL."""
    if connection.platform == "telegram" and connection.config.get("username"):
        return f"https://t.me/{connection.config['username']}?start={token}"
    if connection.platform == "max":
        username = connection.config.get("username") or connection.config.get("bot_username")
        if username:
            return f"https://max.ru/{username}?start={token}"
        return f"max://join?payload={token}"
    return f"payload:{token}"


def render_invite(template: str, *, name: str, link: str, phone: str) -> str:
    return (
        (template or "")
        .replace("{name}", name or "")
        .replace("{link}", link or "")
        .replace("{phone}", phone or "")
    )


def project_outreach_used_today(db, project_id: str) -> int:
    """Count prepared/invited/started recipients for the project since UTC midnight."""
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(func.count())
            .select_from(OutreachRecipient)
            .join(OutreachBatch, OutreachBatch.id == OutreachRecipient.batch_id)
            .where(
                OutreachBatch.project_id == project_id,
                OutreachRecipient.state.in_(["prepared", "invited", "started"]),
                OutreachRecipient.prepared_at.is_not(None),
                OutreachRecipient.prepared_at >= day_start,
            )
        )
        or 0
    )


def eligibility(profile: Profile) -> tuple[bool, str]:
    if profile.stopped:
        return False, "Отказ от сообщений"
    if not (profile.phone or "").strip():
        return False, "Нет телефона"
    return True, "Телефон есть; приглашение через Entry, не через Campaign"


def mark_started_for_identity(db, identity: Identity, connection: Connection) -> int:
    """When user presses Start on an official bot, close matching warm-up recipients.

    Matches by Profile.contact_id + entry_connection_id. Heuristic only — no phone auto-merge.
    """
    if not connection.project_id:
        return 0
    profiles = list(
        db.scalars(
            select(Profile).where(
                Profile.contact_id == identity.contact_id,
                Profile.project_id == connection.project_id,
            )
        )
    )
    if not profiles:
        return 0
    profile_ids = [p.id for p in profiles]
    # Look back a generous window so manual dry_run invites still attribute.
    since = now() - timedelta(days=30)
    recipients = list(
        db.scalars(
            select(OutreachRecipient)
            .join(OutreachBatch, OutreachBatch.id == OutreachRecipient.batch_id)
            .where(
                OutreachRecipient.profile_id.in_(profile_ids),
                OutreachRecipient.state.in_(["prepared", "invited"]),
                OutreachBatch.entry_connection_id == connection.id,
                OutreachBatch.state.notin_(["cancelled"]),
                OutreachRecipient.prepared_at.is_not(None),
                OutreachRecipient.prepared_at >= since,
            )
            .with_for_update()
        )
    )
    marked = 0
    for recipient in recipients:
        recipient.state = "started"
        recipient.started_at = now()
        recipient.outcome_note = (recipient.outcome_note or "") + "\nАвто: Start официального бота"
        batch = db.get(OutreachBatch, recipient.batch_id)
        profile = db.get(Profile, recipient.profile_id)
        db.add(
            Activity(
                id=uid(),
                profile_id=recipient.profile_id,
                actor_id=batch.actor_id if batch else 0,
                kind="outreach_started",
                channel=connection.platform,
                outcome="Start официального бота после прогрева",
                body=f"Identity {identity.id}; batch {recipient.batch_id}; link {recipient.bot_link}",
            )
        )
        db.add(
            Event(
                id=uid(),
                connection_id=connection.id,
                contact_id=identity.contact_id,
                kind="outreach.started",
                payload={
                    "batch_id": recipient.batch_id,
                    "recipient_id": recipient.id,
                    "profile_id": recipient.profile_id,
                    "identity_id": identity.id,
                    "phone": profile.phone if profile else recipient.phone,
                },
            )
        )
        marked += 1
    return marked
