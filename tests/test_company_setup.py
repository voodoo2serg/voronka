"""Thin company setup: consent taxonomy + AiRule + Signal/AccessGrant checklist."""
from __future__ import annotations

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app import company_setup
from app.crm_models import AiRule
from app.crm_service import uid
from app.db import now


def test_blueprint_lists_five_scenario_contracts():
    bp = company_setup.blueprint()
    assert bp["ai_auto_send"] is False
    assert bp["company_setup_marker"] == company_setup.COMPANY_SETUP_MARKER
    assert company_setup.COMPANY_SETUP_MARKER in bp["default_system_rules"]
    assert "transactional" in bp["consent_purposes"]
    assert "marketing" in bp["consent_purposes"]
    roles = {r["role"] for r in bp["signal_rules"]}
    assert {"welcome", "inquiry", "access_grant"} <= roles
    assert any(r["event_type"] == "course.access_granted" for r in bp["signal_rules"])
    assert "sku:follow_up" in bp["financier_tags"]
    assert any("AccessGrant" in step for step in bp["access_grant_path"])
    assert "openai_compatible" in bp["ai_providers"]


def test_env_checklist_includes_neighbor_secrets():
    env = company_setup.env_checklist()
    for key in (
        "SOLO_PR_EVENTS_URL",
        "SOLO_PR_WEBHOOK_SECRET",
        "SOLO_PR_READY",
        "LMS_END_ENROLLMENT_URL",
        "LMS_END_WEBHOOK_SECRET",
        "LMS_M2M_TOKEN",
        "LMS_END_READY",
    ):
        assert key in env
    assert env["allow_production_send"] is False


def test_company_setup_apply_idempotent(crm):
    first = crm.client.post("/api/crm/projects/p/company-setup")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["project_id"] == "p"
    assert body["ai_rule_created"] is True
    assert body["ai_rule_id"]
    assert body["applied"] is True
    assert body["env"]["allow_production_send"] is False
    assert "SOLO_PR_WEBHOOK_SECRET" in body["env"]
    assert "LMS_END_WEBHOOK_SECRET" in body["env"]

    status = crm.client.get("/api/crm/projects/p/company-setup")
    assert status.status_code == 200
    assert status.json()["applied"] is True
    assert status.json()["ai_rule_id"] == body["ai_rule_id"]

    second = crm.client.post("/api/crm/projects/p/company-setup")
    assert second.status_code == 200
    assert second.json()["ai_rule_created"] is False
    assert second.json()["ai_rule_id"] == body["ai_rule_id"]
    assert second.json()["ai_rule_revision"] >= body["ai_rule_revision"]
    assert second.json()["applied"] is True


def test_custom_project_rule_is_not_company_applied(crm):
    with crm.db.begin() as db:
        db.add(
            AiRule(
                id=uid(),
                scope="project",
                project_id="p",
                connection_id=None,
                provider="ollama",
                model="llama3",
                system_rules="Кастомные правила без маркера компании",
                active=True,
                revision=1,
                updated_at=now(),
            )
        )
    status = crm.client.get("/api/crm/projects/p/company-setup")
    assert status.status_code == 200
    body = status.json()
    assert body["has_project_ai_rule"] is True
    assert body["applied"] is False

    applied = crm.client.post("/api/crm/projects/p/company-setup")
    assert applied.status_code == 200
    # Custom text preserved → still not company-applied until rules carry the marker.
    assert applied.json()["ai_rule_created"] is False
    assert applied.json()["applied"] is False
    again = crm.client.get("/api/crm/projects/p/company-setup")
    assert again.json()["applied"] is False


def test_blueprint_endpoint(crm):
    r = crm.client.get("/api/crm/company-setup/blueprint")
    assert r.status_code == 200
    assert r.json()["consent_purposes"] == list(company_setup.CONSENT_PURPOSES)
    assert "SOLO_PR_WEBHOOK_SECRET" in r.json()["env"]
