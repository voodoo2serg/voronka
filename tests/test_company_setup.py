"""Thin company setup: consent taxonomy + AiRule + Signal/AccessGrant checklist."""
from __future__ import annotations

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app import company_setup


def test_blueprint_lists_five_scenario_contracts():
    bp = company_setup.blueprint()
    assert bp["ai_auto_send"] is False
    assert "transactional" in bp["consent_purposes"]
    assert "marketing" in bp["consent_purposes"]
    roles = {r["role"] for r in bp["signal_rules"]}
    assert {"welcome", "inquiry", "access_grant"} <= roles
    assert any(r["event_type"] == "course.access_granted" for r in bp["signal_rules"])
    assert "sku:follow_up" in bp["financier_tags"]
    assert any("AccessGrant" in step for step in bp["access_grant_path"])
    assert "openai_compatible" in bp["ai_providers"]


def test_company_setup_apply_idempotent(crm):
    first = crm.client.post("/api/crm/projects/p/company-setup")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["project_id"] == "p"
    assert body["ai_rule_created"] is True
    assert body["ai_rule_id"]
    assert body["env"]["allow_production_send"] is False

    status = crm.client.get("/api/crm/projects/p/company-setup")
    assert status.status_code == 200
    assert status.json()["applied"] is True
    assert status.json()["ai_rule_id"] == body["ai_rule_id"]

    second = crm.client.post("/api/crm/projects/p/company-setup")
    assert second.status_code == 200
    assert second.json()["ai_rule_created"] is False
    assert second.json()["ai_rule_id"] == body["ai_rule_id"]
    assert second.json()["ai_rule_revision"] >= body["ai_rule_revision"]


def test_blueprint_endpoint(crm):
    r = crm.client.get("/api/crm/company-setup/blueprint")
    assert r.status_code == 200
    assert r.json()["consent_purposes"] == list(company_setup.CONSENT_PURPOSES)
