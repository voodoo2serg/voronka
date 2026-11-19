"""Thin company / project defaults for Solo-PR style setups.

Wires consent purpose taxonomy, Signal rule checklist (welcome / inquiry /
AccessGrant mail), financier follow-up tags, and a project AiRule that keeps
LLM drafts operator-gated. Prefer this over ad-hoc env folklore.
"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select

from app.crm_models import AiRule
from app.crm_service import uid
from app.db import Project, now

CONSENT_PURPOSES = ("transactional", "service", "marketing", "analytics")

# Stable fingerprint: presence means company defaults were applied (not just any AiRule).
COMPANY_SETUP_MARKER = "<!-- company-setup:v1 -->"

# Signal Communication Engine rules that must exist for S1–S5 (seeded in Signal).
SIGNAL_RULES_CHECKLIST: list[dict[str, str]] = [
    {
        "source": "solo_pr",
        "event_type": "identity.email_verified",
        "role": "welcome",
        "note": "Приветствие после подтверждённой почты (CJ-104)",
    },
    {
        "source": "solo_pr",
        "event_type": "inquiry.business",
        "role": "inquiry",
        "note": "Ответ на заявку с сайта (CJ-103)",
    },
    {
        "source": "solo_pr",
        "event_type": "inquiry.course",
        "role": "inquiry",
        "note": "Ответ на вопрос об обучении",
    },
    {
        "source": "voronka",
        "event_type": "course.access_granted",
        "role": "access_grant",
        "note": "Письмо «доступ открыт» после AccessGrant (CJ-107)",
    },
]

FINANCIER_TAGS = ("sku:follow_up", "non_buyer")

ACCESS_GRANT_PATH = [
    "Создать Order у Profile/Person в Воронке (CRM).",
    "Записать Payment (sandbox: POST /api/crm/orders/{id}/payments).",
    "Создать AccessGrant на заказ (resource=course_id, capability=course.view).",
    "Дождаться EntitlementJob / drain outbox → LMS enrollment.desired + Signal course.access_granted.",
    "Кнопка «Открыть доступ» на сайте Solo-PR — только локальная пометка, не этот путь.",
]

DEFAULT_SYSTEM_RULES = f"""\
{COMPANY_SETUP_MARKER}
Компания: Соло-Пиар (проект CRM).
Цели писем: transactional/service — сервис; marketing — только при явном согласии.
Не подтверждай оплату и не обещай скидок без человека.
Черновики LLM (llm_reply / ассистент) — только draft; auto_send запрещён.
После оплаты путь доступа: AccessGrant в Воронке → LMS + Signal course.access_granted.
Непокупателям: финансист ставит теги sku:follow_up / non_buyer → сегмент dry-run или PDialog draft.
""".strip()


def has_company_setup_marker(system_rules: str | None) -> bool:
    return COMPANY_SETUP_MARKER in (system_rules or "")


def blueprint() -> dict[str, Any]:
    """Static company-setup contract (no DB)."""
    return {
        "consent_purposes": list(CONSENT_PURPOSES),
        "signal_rules": list(SIGNAL_RULES_CHECKLIST),
        "financier_tags": list(FINANCIER_TAGS),
        "access_grant_path": list(ACCESS_GRANT_PATH),
        "ai_providers": ["raga", "openrouter", "ollama", "openai_compatible"],
        "ai_auto_send": False,
        "company_setup_marker": COMPANY_SETUP_MARKER,
        "default_system_rules": DEFAULT_SYSTEM_RULES,
    }


def env_checklist() -> dict[str, Any]:
    """What ops must configure for the company path to work end-to-end.

    Neighbor deliveries need URL **and** signing secret (see integrations.deliver_one).
    """
    solo_url = bool(os.getenv("SOLO_PR_EVENTS_URL"))
    solo_secret = bool(os.getenv("SOLO_PR_WEBHOOK_SECRET"))
    lms_url = bool(os.getenv("LMS_END_ENROLLMENT_URL"))
    lms_secret = bool(os.getenv("LMS_END_WEBHOOK_SECRET"))
    lms_m2m = bool(os.getenv("LMS_M2M_TOKEN"))
    return {
        "DEFAULT_PROJECT_ID": bool(os.getenv("DEFAULT_PROJECT_ID")),
        "SIGNAL_EVENTS_URL": bool(os.getenv("SIGNAL_EVENTS_URL")),
        "SIGNAL_EVENTS_SECRET": bool(os.getenv("SIGNAL_EVENTS_SECRET")),
        "SOLO_PR_EVENTS_URL": solo_url,
        "SOLO_PR_WEBHOOK_SECRET": solo_secret,
        "SOLO_PR_READY": solo_url and solo_secret,
        "LMS_END_ENROLLMENT_URL": lms_url,
        "LMS_END_WEBHOOK_SECRET": lms_secret,
        "LMS_M2M_TOKEN": lms_m2m,
        "LMS_END_READY": lms_url and (lms_secret or lms_m2m),
        "RAG_API": bool(os.getenv("RAG_API_URL") and os.getenv("RAG_API_TOKEN")),
        "OPENROUTER_API_KEY": bool(os.getenv("OPENROUTER_API_KEY")),
        "OLLAMA_BASE_URL": bool(os.getenv("OLLAMA_BASE_URL")),
        "OPENAI_COMPAT_BASE_URL": bool(os.getenv("OPENAI_COMPAT_BASE_URL")),
        "allow_production_send": False,  # never flipped by this module
    }


def _project_ai_rule_query(project_id: str):
    return select(AiRule).where(
        AiRule.scope == "project",
        AiRule.project_id == project_id,
        AiRule.connection_id.is_(None),
    )


def apply_project_defaults(db, *, project_id: str, actor_telegram_id: int | None = None) -> dict[str, Any]:
    """Upsert project AiRule with company defaults. Idempotent.

    Serializes concurrent first-time creates by locking the Project row first
    (FOR UPDATE on a missing AiRule cannot serialize inserts).
    """
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
    if project is None:
        raise ValueError("project_not_found")

    row = db.scalar(_project_ai_rule_query(project_id).with_for_update())
    created = False
    if row:
        # Refresh only empty rules or our prior company fingerprint; keep custom text.
        if not (row.system_rules or "").strip() or has_company_setup_marker(row.system_rules):
            row.system_rules = DEFAULT_SYSTEM_RULES
        row.active = True
        row.revision += 1
        row.updated_at = now()
        if actor_telegram_id is not None:
            row.updated_by = actor_telegram_id
    else:
        created = True
        row = AiRule(
            id=uid(),
            scope="project",
            project_id=project_id,
            connection_id=None,
            provider="inherit",
            model="",
            system_rules=DEFAULT_SYSTEM_RULES,
            active=True,
            revision=1,
            updated_at=now(),
            updated_by=actor_telegram_id,
        )
        db.add(row)
        db.flush()
    return {
        "project_id": project_id,
        "ai_rule_id": row.id,
        "ai_rule_created": created,
        "ai_rule_revision": row.revision,
        "applied": has_company_setup_marker(row.system_rules),
        "consent_purposes": list(CONSENT_PURPOSES),
        "financier_tags": list(FINANCIER_TAGS),
        "signal_rules": list(SIGNAL_RULES_CHECKLIST),
        "access_grant_path": list(ACCESS_GRANT_PATH),
        "env": env_checklist(),
        "note": "Signal welcome/inquiry/access rules seed separately (signal seed_communications --enable).",
    }


def project_status(db, *, project_id: str) -> dict[str, Any]:
    row = db.scalar(
        _project_ai_rule_query(project_id).where(AiRule.active.is_(True))
    )
    applied = bool(row and has_company_setup_marker(row.system_rules))
    return {
        **blueprint(),
        "project_id": project_id,
        "applied": applied,
        "ai_rule_id": row.id if row else None,
        "ai_provider": row.provider if row else None,
        "has_project_ai_rule": bool(row),
        "env": env_checklist(),
    }
