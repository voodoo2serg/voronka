"""IMPORT-101: confirmed email/Telegram audience → Person + ContactPoint + Consent.

Never marks marketing sendable without explicit consent grant. Quarantine mode
creates/updates the graph with marketing Consent status=unknown so send_allowed
(missing_policy=deny) stays closed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import select

from app import person_graph as graph
from app.crm_models import Consent, ContactPoint, ImportBatch, Person, PersonLink, Profile
from app.crm_service import normalize_email, uid
from app.db import Contact, now

ConsentMode = Literal["quarantine", "grant_marketing"]

AUDIENCE_ALIASES = {
    "name": ["имя", "фио", "name", "клиент", "display_name"],
    "email": ["email", "e-mail", "почта", "mail"],
    "telegram_id": ["telegram_id", "tg_id", "telegramid", "user_id", "external_id"],
    "telegram_username": ["telegram_username", "tg_username", "username", "telegram", "tg"],
}


def normalize_telegram_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not re.fullmatch(r"-?\d{3,20}", value):
        raise ValueError("Некорректный telegram_id")
    return value


def normalize_telegram_username(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("@"):
        value = value[1:]
    value = value.lower()
    if not re.fullmatch(r"[a-z0-9_]{3,64}", value):
        raise ValueError("Некорректный telegram username")
    return value


def parse_audience_csv(raw: bytes, mapping: dict | None = None) -> tuple[list[str], dict, list[dict]]:
    if len(raw) > 5_000_000:
        raise HTTPException(413, "Максимум 5 МБ")
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(422, "Не удалось определить кодировку")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or len(headers) > 80:
        raise HTTPException(422, "Повторяющиеся заголовки или больше 80 колонок")
    fields = dict(mapping or {})
    if not fields:
        fields = {
            field: header
            for field, names in AUDIENCE_ALIASES.items()
            for header in headers
            if header.strip().lower() in names
        }
    if set(fields) - set(AUDIENCE_ALIASES) or any(v not in headers for v in fields.values()):
        raise HTTPException(422, "Неизвестное поле или колонка сопоставления")
    rows: list[dict] = []
    for index, raw_row in enumerate(reader, start=2):
        if len(rows) >= 5000:
            raise HTTPException(422, "Одна партия: максимум 5000 строк")
        data = {field: (raw_row.get(column) or "").strip() for field, column in fields.items()}
        error = ""
        try:
            if "email" in data:
                data["email"] = normalize_email(data.get("email", ""))
            if "telegram_id" in data:
                data["telegram_id"] = normalize_telegram_id(data.get("telegram_id", ""))
            if "telegram_username" in data:
                data["telegram_username"] = normalize_telegram_username(data.get("telegram_username", ""))
            if not any(data.get(k) for k in ("email", "telegram_id", "telegram_username")):
                raise ValueError("Нужен email и/или telegram_id/username")
            name = data.get("name", "")
            if len(name) > 200:
                raise ValueError("Имя: максимум 200 символов")
        except ValueError as exc:
            error = str(exc)
        rows.append(
            {
                "line": index,
                "data": data,
                "error": error,
                "extra": {k: v for k, v in raw_row.items() if k and k not in fields.values()},
            }
        )
    return headers, fields, rows


def audience_fingerprint(raw: bytes, mapping: dict, consent_mode: str, evidence: str) -> str:
    payload = {
        "kind": "audience",
        "mapping": mapping,
        "consent_mode": consent_mode,
        "evidence": evidence,
    }
    import json

    return hashlib.sha256(raw + json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _find_contact_point(db, *, channel: str, normalized: str) -> ContactPoint | None:
    digest = graph.address_hash(channel, normalized)
    return db.scalar(
        select(ContactPoint).where(
            ContactPoint.channel == channel,
            ContactPoint.address_hash == digest,
        )
    )


def _resolve_person_for_project(db, project_id: str, person: Person) -> Person:
    if person.merged_into_id:
        person = db.get(Person, person.merged_into_id) or person
    return person


def _person_in_project(db, person_id: str, project_id: str) -> bool:
    return (
        db.scalar(
            select(Profile.id).where(Profile.person_id == person_id, Profile.project_id == project_id).limit(1)
        )
        is not None
    )


def _find_existing_person(
    db,
    project_id: str,
    *,
    email: str,
    telegram_id: str,
    telegram_username: str,
) -> tuple[Person | None, str]:
    """Return (person, match_key) using natural keys; prefer project-bound Person."""
    candidates: list[tuple[Person, str]] = []

    def consider(cp: ContactPoint | None, key: str) -> None:
        if cp is None:
            return
        person = db.get(Person, cp.person_id)
        if person is None:
            return
        person = _resolve_person_for_project(db, project_id, person)
        candidates.append((person, key))

    if email:
        consider(_find_contact_point(db, channel="email", normalized=email), f"email:{email}")
        profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.email == email))
        if profile and profile.person_id:
            person = db.get(Person, profile.person_id)
            if person:
                candidates.append((_resolve_person_for_project(db, project_id, person), f"profile_email:{email}"))
    if telegram_id:
        consider(_find_contact_point(db, channel="telegram", normalized=telegram_id), f"telegram_id:{telegram_id}")
    if telegram_username:
        consider(
            _find_contact_point(db, channel="telegram", normalized=telegram_username),
            f"telegram_username:{telegram_username}",
        )

    if not candidates:
        return None, ""

    # Prefer a person already linked to this project.
    project_bound = [(p, k) for p, k in candidates if _person_in_project(db, p.id, project_id)]
    pool = project_bound or candidates
    unique = {p.id: (p, k) for p, k in pool}
    if len(unique) > 1:
        # Ambiguous natural keys across persons — caller treats as error.
        return None, "ambiguous"
    person, key = next(iter(unique.values()))
    return person, key


def _ensure_project_profile(
    db,
    project_id: str,
    person: Person,
    *,
    name: str,
    email: str,
    source: str,
    batch_id: str,
    line: int,
) -> Profile:
    profile = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.person_id == person.id))
    if profile:
        if email and not profile.email:
            profile.email = email
        if name and not profile.name:
            profile.name = name
        return profile
    if email:
        by_email = db.scalar(select(Profile).where(Profile.project_id == project_id, Profile.email == email))
        if by_email:
            by_email.person_id = person.id
            if name and not by_email.name:
                by_email.name = name
            return by_email
    contact = Contact(id=uid())
    db.add(contact)
    db.flush()
    contact.person_id = person.id
    profile = Profile(
        id=uid(),
        contact_id=contact.id,
        project_id=project_id,
        person_id=person.id,
        name=name or email or person.display_name or "Импортированная аудитория",
        email=email or "",
        phone="",
        company="",
        source=source or "audience_csv",
        tags=[],
        extra={"import_kind": "audience", "import_batch": batch_id, "import_line": line},
    )
    db.add(profile)
    db.flush()
    return profile


def _apply_consent(
    db,
    contact_point_id: str,
    *,
    consent_mode: ConsentMode,
    evidence: str,
) -> tuple[str, bool]:
    """Return (consent_status, newly_granted)."""
    if consent_mode == "grant_marketing":
        prior = db.scalar(
            select(Consent).where(Consent.contact_point_id == contact_point_id, Consent.purpose == "marketing")
        )
        was_granted = bool(prior and prior.status == "granted")
        graph.set_consent(
            db,
            contact_point_id,
            purpose="marketing",
            status="granted",
            evidence=evidence,
            lawful_basis="consent",
        )
        return "granted", not was_granted
    # Quarantine: never grant marketing; record unknown for audit.
    graph.set_consent(
        db,
        contact_point_id,
        purpose="marketing",
        status="unknown",
        evidence=evidence or "audience_import:quarantine",
        lawful_basis="consent",
    )
    return "unknown", False


def plan_or_apply_audience_rows(
    db,
    *,
    project_id: str,
    rows: list[dict],
    consent_mode: ConsentMode,
    evidence: str,
    batch_id: str,
    actor_id: int | None,
    dry_run: bool,
    source_label: str = "audience_csv",
) -> dict[str, Any]:
    """Idempotent audience upsert. dry_run=True never mutates (uses nested rollback)."""
    if consent_mode not in {"quarantine", "grant_marketing"}:
        raise HTTPException(422, "consent_mode: quarantine|grant_marketing")
    if consent_mode == "grant_marketing" and not (evidence or "").strip():
        raise HTTPException(422, "Для grant_marketing нужна evidence (источник согласия)")

    counts = {
        "total": len(rows),
        "errors": 0,
        "created_persons": 0,
        "updated_persons": 0,
        "created_contact_points": 0,
        "updated_contact_points": 0,
        "consent_granted": 0,
        "quarantined": 0,
        "unchanged": 0,
        "ambiguous": 0,
    }
    samples: list[dict] = []
    error_rows: list[dict] = []

    nested = db.begin_nested() if dry_run else None
    try:
        for entry in rows:
            if entry.get("error"):
                counts["errors"] += 1
                error_rows.append({"line": entry["line"], "error": entry["error"]})
                continue
            data = entry["data"]
            email = data.get("email", "") or ""
            telegram_id = data.get("telegram_id", "") or ""
            telegram_username = data.get("telegram_username", "") or ""
            name = data.get("name", "") or ""

            person, match_key = _find_existing_person(
                db,
                project_id,
                email=email,
                telegram_id=telegram_id,
                telegram_username=telegram_username,
            )
            if match_key == "ambiguous":
                counts["ambiguous"] += 1
                counts["errors"] += 1
                error_rows.append({"line": entry["line"], "error": "ambiguous_natural_key"})
                continue

            created_person = False
            if person is None:
                person = Person(
                    id=uid(),
                    display_name=name or email or telegram_username or telegram_id or "Audience",
                    lifecycle_stage="active",
                    extra={"source": source_label, "import_batch": batch_id},
                    created_at=now(),
                    updated_at=now(),
                )
                db.add(person)
                db.flush()
                db.add(
                    PersonLink(
                        id=uid(),
                        person_id=person.id,
                        service="voronka",
                        native_id=f"audience:{project_id}:{person.id}",
                        verified_by="audience_import",
                        created_at=now(),
                    )
                )
                created_person = True
                counts["created_persons"] += 1
            else:
                if name and not person.display_name:
                    person.display_name = name
                    person.updated_at = now()
                counts["updated_persons"] += 1

            _ensure_project_profile(
                db,
                project_id,
                person,
                name=name,
                email=email,
                source=source_label,
                batch_id=batch_id,
                line=entry["line"],
            )

            channels: list[tuple[str, str]] = []
            if email:
                channels.append(("email", email))
            if telegram_id:
                channels.append(("telegram", telegram_id))
            if telegram_username:
                channels.append(("telegram", telegram_username))

            row_granted = 0
            row_quarantine = 0
            row_cp_created = 0
            row_cp_updated = 0
            point_ids: list[str] = []

            for channel, value in channels:
                before = _find_contact_point(db, channel=channel, normalized=value)
                existed_for_person = bool(
                    before and before.person_id == person.id
                )
                verified = consent_mode == "grant_marketing"
                cp = graph.upsert_contact_point(
                    db,
                    person,
                    channel=channel,
                    value=value,
                    verified=verified,
                    raw_value=value,
                )
                if cp is None:
                    continue
                if before is None or not existed_for_person:
                    row_cp_created += 1
                else:
                    row_cp_updated += 1
                    if verified and not before.verified:
                        cp.verified = True
                status, newly_granted = _apply_consent(
                    db,
                    cp.id,
                    consent_mode=consent_mode,
                    evidence=evidence
                    or f"audience_import:{batch_id}:line:{entry['line']}",
                )
                if status == "granted":
                    if newly_granted:
                        row_granted += 1
                else:
                    row_quarantine += 1
                point_ids.append(cp.id)

            counts["created_contact_points"] += row_cp_created
            counts["updated_contact_points"] += row_cp_updated
            counts["consent_granted"] += row_granted
            counts["quarantined"] += row_quarantine
            if not created_person and row_cp_created == 0 and row_granted == 0 and row_quarantine == 0:
                counts["unchanged"] += 1

            # Audit interaction (idempotent per batch+line)
            graph.record_interaction(
                db,
                person_id=person.id,
                kind="audience.import",
                direction="system",
                channel="import",
                project_id=project_id,
                source_service="voronka",
                external_event_key=f"audience_import:{batch_id}:{entry['line']}",
                payload={
                    "consent_mode": consent_mode,
                    "match_key": match_key,
                    "contact_point_ids": point_ids,
                    "actor_id": actor_id,
                    "dry_run": dry_run,
                },
                evidence_status="recorded",
            )

            if len(samples) < 10:
                allowed, reason = graph.send_allowed(
                    db,
                    person_id=person.id,
                    channel=channels[0][0] if channels else "email",
                    purpose="marketing",
                    missing_policy="deny",
                )
                samples.append(
                    {
                        "line": entry["line"],
                        "person_id": person.id,
                        "email": email,
                        "telegram_id": telegram_id,
                        "telegram_username": telegram_username,
                        "created_person": created_person,
                        "consent_mode": consent_mode,
                        "marketing_send_allowed": allowed,
                        "marketing_reason": reason,
                    }
                )
    finally:
        if nested is not None:
            nested.rollback()

    return {
        "counts": counts,
        "samples": samples,
        "errors": error_rows[:50],
        "consent_mode": consent_mode,
        "dry_run": dry_run,
    }


def empty_batch_report() -> dict:
    return {
        "counts": {},
        "samples": [],
        "errors": [],
        "consent_mode": "",
        "dry_run": False,
    }


def get_or_create_audience_batch(
    db,
    *,
    project_id: str,
    fingerprint: str,
    mapping: dict,
    rows: list[dict],
    actor_id: int,
    consent_mode: str,
    evidence: str,
) -> ImportBatch:
    row = db.scalar(
        select(ImportBatch).where(ImportBatch.project_id == project_id, ImportBatch.fingerprint == fingerprint)
    )
    if row:
        return row
    stored_mapping = {
        **mapping,
        "_kind": "audience",
        "_consent_mode": consent_mode,
        "_evidence": evidence,
    }
    row = ImportBatch(
        id=uid(),
        project_id=project_id,
        fingerprint=fingerprint,
        mapping=stored_mapping,
        rows=rows,
        actor_id=actor_id,
        state="preview",
        report=empty_batch_report(),
    )
    db.add(row)
    db.flush()
    return row
