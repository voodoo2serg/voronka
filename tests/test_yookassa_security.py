import asyncio
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from test_crm import contact, order, product
from app import payment_vault
from app.crm_models import Invoice, Payment, EntitlementJob
from app.yookassa_verification import amount_minor, verified_payment

pytest_plugins = ("test_operational", "test_crm")


@pytest.mark.parametrize("state,enabled,test_mode,expected", [
    ("waiting_for_capture", "1", False, False),
    ("succeeded", "0", False, False),
    ("succeeded", "1", True, False),
    ("succeeded", "1", False, True),
])
def test_only_verified_live_success_changes_money(crm, monkeypatch, tmp_path, state, enabled, test_mode, expected):
    monkeypatch.setenv("CRM_PAYMENT_SECRETS_PATH", str(tmp_path / "payment.env"))
    payment_vault.apply_updates({"YOOKASSA_SHOP_ID": "shop", "YOOKASSA_SECRET_KEY": "secret",
                                 "YOOKASSA_WEBHOOK_SECRET": "", "YOOKASSA_ENABLED": enabled})
    purchase = order(crm, person=contact(crm, name="Verified buyer"), item=product(crm, amount=20000))
    with crm.db.begin() as db:
        db.add(Invoice(id="verified-invoice", order_id=purchase["id"], number="verified-1",
                       amount_minor=20000, test=test_mode, state="pending"))
    async def verify(payment_id):
        return {"id": payment_id, "status": state, "paid": state == "succeeded", "test": test_mode,
                "amount": {"value": "200.00", "currency": "RUB"},
                "metadata": {"invoice_id": "verified-invoice"}}
    monkeypatch.setattr("app.yookassa_verification.verified_payment", verify)
    body = {"event": "payment." + state, "object": {"id": "verified-payment", "status": "succeeded"}}
    for _ in range(2):
        response = crm.client.post("/api/crm/yookassa/webhook", json=body)
        assert response.status_code == 200, response.text
    with crm.db() as db:
        assert (db.get(Invoice, "verified-invoice").state == "paid") is expected
        assert len(list(db.scalars(select(Payment).where(Payment.provider == "yookassa")))) == int(expected)
        assert len(list(db.scalars(select(EntitlementJob)))) == int(expected)


@pytest.mark.parametrize("value,currency", [("NaN", "RUB"), ("Infinity", "RUB"), ("-1", "RUB"), ("1.001", "RUB"), ("1", "USD")])
def test_reject_invalid_amounts(value, currency):
    with pytest.raises(HTTPException):
        amount_minor({"amount": {"value": value, "currency": currency}})


def test_provider_identity_checked(monkeypatch):
    import httpx
    import app.yookassa_verification as module
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "our-shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "secret")
    client_type = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "id": "payment", "recipient": {"account_id": "another-shop"}}))
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
    with pytest.raises(HTTPException) as error:
        asyncio.run(verified_payment("payment"))
    assert error.value.status_code == 403


def test_privacy_request_is_authenticated_durable_and_idempotent(crm, monkeypatch):
    import json
    import time
    from app.integrations import sign_payload
    from app.crm_models import IntegrationInbox
    secret = "site-only-privacy-key"
    monkeypatch.setenv("SOLO_PR_WEBHOOK_SECRET", secret)
    body = {"event_id": "subject.delete:user-one", "type": "subject.delete", "source": "solo_pr",
            "data": {"user_id": "user-one", "email": "user@example.com"}}
    raw = json.dumps(body).encode()
    url = "/api/crm/integrations/solo_pr/privacy"
    assert crm.client.post(url, content=raw).status_code == 401
    timestamp = str(int(time.time()))
    headers = {"Content-Type": "application/json", "X-Solo-Timestamp": timestamp,
               "X-Solo-Signature": sign_payload(secret, raw, timestamp)}
    first = crm.client.post(url, content=raw, headers=headers)
    assert first.status_code == 202, first.text
    assert crm.client.post(url, content=raw, headers=headers).json()["duplicate"]
    with crm.db() as db:
        requests = list(db.scalars(select(IntegrationInbox).where(IntegrationInbox.kind == "subject.delete")))
        assert len(requests) == 1 and requests[0].state == "pending_review"
