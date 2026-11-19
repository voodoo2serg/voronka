"""PAY-101: Robokassa Receipt payload + signature (no live-ready flip)."""
from __future__ import annotations

import hashlib
import json
from urllib.parse import parse_qs, unquote, urlparse

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, order, product  # noqa: F401

from app import robokassa_receipt
from app.crm_models import Invoice


def test_build_receipt_full_and_partial(monkeypatch):
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAX", "vat20")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_SNO", "usn_income")
    monkeypatch.delenv("ROBOKASSA_RECEIPT_PAYMENT_METHOD", raising=False)
    monkeypatch.setenv("ROBOKASSA_RECEIPT_PAYMENT_OBJECT", "service")

    full = robokassa_receipt.build_receipt(
        amount_minor=100000,
        order_total_minor=100000,
        snapshot={"name": "Курс SIGNAL"},
    )
    assert full["sno"] == "usn_income"
    assert full["items"][0]["sum"] == 1000.0
    assert full["items"][0]["tax"] == "vat20"
    assert full["items"][0]["payment_method"] == "full_prepayment"
    assert full["items"][0]["payment_object"] == "service"
    assert full["items"][0]["name"] == "Курс SIGNAL"

    partial = robokassa_receipt.build_receipt(
        amount_minor=40000,
        order_total_minor=100000,
        snapshot={"name": "Курс SIGNAL"},
    )
    assert partial["items"][0]["sum"] == 400.0
    assert partial["items"][0]["payment_method"] == "prepayment"


def test_sign_payment_includes_url_encoded_receipt():
    receipt = {
        "items": [{
            "name": "Аудит",
            "quantity": 1,
            "sum": 1000.0,
            "payment_method": "full_prepayment",
            "payment_object": "service",
            "tax": "none",
        }]
    }
    raw, encoded = robokassa_receipt.encode_receipt(receipt)
    assert json.loads(raw) == receipt
    assert "%" in encoded or encoded == raw  # quote of ASCII JSON may equal raw
    sig = robokassa_receipt.sign_payment(
        merchant="merchant",
        out_sum="1000.00",
        inv_id="42",
        password1="test-first",
        receipt_encoded=encoded,
        algorithm="sha256",
    )
    expected = hashlib.sha256(
        f"merchant:1000.00:42:{encoded}:test-first".encode()
    ).hexdigest()
    assert sig == expected
    # Old formula without Receipt must not match.
    old = hashlib.sha256(b"merchant:1000.00:42:test-first").hexdigest()
    assert sig != old


def test_checkout_url_embeds_receipt_and_matching_signature(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_TEST", "1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD1", "test-first")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD2", "test-second")
    monkeypatch.setenv("ROBOKASSA_HASH", "sha256")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAX", "none")
    monkeypatch.delenv("ROBOKASSA_RECEIPT_SNO", raising=False)
    monkeypatch.delenv("ROBOKASSA_LIVE_READY", raising=False)

    o = order(crm, request_key="receipt-full-001")
    r = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["test"] is True
    assert body["receipt_in_adapter"] is True
    assert body["receipt"]["items"][0]["sum"] == 1000.0
    assert body["receipt"]["items"][0]["tax"] == "none"

    parsed = urlparse(body["url"])
    assert parsed.netloc == "auth.robokassa.ru"
    qs = parse_qs(parsed.query, keep_blank_values=True)
    assert qs["MerchantLogin"] == ["merchant"]
    assert qs["OutSum"] == ["1000.00"]
    assert "Receipt" in qs
    # Signature uses URL-encoded Receipt; parse_qs decodes — take raw fragment from query.
    raw_query = parsed.query
    marker = "Receipt="
    start = raw_query.index(marker) + len(marker)
    end = raw_query.find("&", start)
    receipt_encoded = raw_query[start:] if end < 0 else raw_query[start:end]
    decoded = json.loads(unquote(receipt_encoded))
    assert decoded == body["receipt"]
    with crm.db() as db:
        invoice = db.get(Invoice, body["invoice_id"])
        number = invoice.number
    assert qs["InvId"] == [number]
    expected = hashlib.sha256(
        f"merchant:1000.00:{number}:{receipt_encoded}:test-first".encode()
    ).hexdigest()
    assert qs["SignatureValue"][0].lower() == expected.lower()


def test_checkout_partial_amount_receipt_matches_outsum(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_TEST", "1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD1", "test-first")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD2", "test-second")
    monkeypatch.setenv("ROBOKASSA_HASH", "sha256")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAX", "vat10")

    item = product(crm, amount=200000)
    o = order(crm, item=item, request_key="receipt-partial-001")
    r = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa?amount_minor=50000")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["receipt"]["items"][0]["sum"] == 500.0
    assert body["receipt"]["items"][0]["payment_method"] == "prepayment"
    assert body["receipt"]["items"][0]["tax"] == "vat10"
    qs = parse_qs(urlparse(body["url"]).query)
    assert qs["OutSum"] == ["500.00"]


def test_live_checkout_still_gated_without_live_ready(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_TEST", "0")
    monkeypatch.setenv("ROBOKASSA_PASSWORD1", "first")
    monkeypatch.setenv("ROBOKASSA_PASSWORD2", "second")
    monkeypatch.setenv("ROBOKASSA_LIVE_READY", "0")
    o = order(crm, request_key="receipt-live-gate-001")
    r = crm.client.post(f"/api/crm/orders/{o['id']}/robokassa")
    assert r.status_code == 503
    assert "фискализации" in r.json()["detail"].lower() or "фискализац" in r.text.lower()


def test_payment_providers_checklist_receipt_honest(crm, monkeypatch):
    monkeypatch.setenv("ROBOKASSA_LOGIN", "merchant")
    monkeypatch.setenv("ROBOKASSA_TEST", "1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD1", "t1")
    monkeypatch.setenv("ROBOKASSA_TEST_PASSWORD2", "t2")
    monkeypatch.setenv("ROBOKASSA_RECEIPT_TAX", "none")
    monkeypatch.setenv("ROBOKASSA_LIVE_READY", "0")

    providers = crm.client.get("/api/crm/projects/p/payment-providers").json()
    rk = providers["robokassa"]
    assert rk["receipt_in_adapter"] is True
    assert rk["live_ready"] is False
    by_id = {item["id"]: item for item in rk["fiscalization_checklist"]}
    assert by_id["receipt_payload"]["done"] is True
    assert by_id["fiscal_shop"]["done"] is False
    assert by_id["staging_pay"]["done"] is False
    assert by_id["owner_approval"]["done"] is False
    assert by_id["live_gate"]["done"] is False
    assert by_id["tax_env"]["done"] is True
