"""FUNC-L2: honest sales runbook gates (sandbox vs live) — no live flip."""
from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm  # noqa: F401
from app import portals


def test_sales_runbook_reports_live_off_by_default(monkeypatch):
    monkeypatch.setenv("ROBOKASSA_TEST", "1")
    monkeypatch.delenv("ROBOKASSA_LIVE_READY", raising=False)
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD1", "t1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD2", "t2")
    book = portals.sales_runbook()
    assert book["can_enable_live_from_ui"] is False
    assert book["gates"]["robokassa_live"]["on"] is False
    assert book["gates"]["production_marketing"]["on"] is False
    assert book["gates"]["sandbox_payments"]["on"] is True
    assert "signal" in book["links"]
    assert "lms" in book["links"]
    assert book["secrets_exposed"] is False


def test_sales_runbook_endpoint(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_TEST", "1")
    monkeypatch.delenv("ROBOKASSA_LIVE_READY", raising=False)
    r = crm.client.get("/api/crm/sales-runbook")
    assert r.status_code == 200
    body = r.json()
    assert body["can_enable_live_from_ui"] is False
    assert body["gates"]["robokassa_live"]["on"] is False
    assert body["gates"]["production_marketing"]["on"] is False
    assert "Q6" in body["policy"] or "sandbox" in body["policy"].lower()
    assert body["links"]["signal"]
    assert body["links"]["lms"]
