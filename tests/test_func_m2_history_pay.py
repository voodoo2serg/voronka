"""FUNC-M2: sandbox payment writes Person Interaction visible on contact History."""
from __future__ import annotations

from sqlalchemy import select

from test_operational import system  # noqa: F401
from test_crm import crm, contact, product, order  # noqa: F401
from app.crm_models import Interaction


def test_sandbox_payment_appears_in_contact_history(crm):
    person = contact(crm, name="Pay Hist", email="payhist@example.ru")
    item = product(crm, amount=10000, name="Course", kind="course")
    o = order(crm, person, item, request_key="func-m2-pay-1")
    pay = crm.client.post(
        f"/api/crm/orders/{o['id']}/payments",
        json={
            "amount_minor": 10000,
            "external_id": "sandbox-func-m2-pay-1",
            "evidence": "sandbox manual",
            "kind": "payment",
        },
    )
    assert pay.status_code == 200, pay.text

    detail = crm.client.get(f"/api/crm/contacts/{person['id']}").json()
    assert detail.get("person_id"), "Person must exist for History"
    kinds = {ix["kind"] for ix in detail.get("person_interactions") or []}
    assert "deal.paid" in kinds
    pay_ix = next(ix for ix in detail["person_interactions"] if ix["kind"] == "deal.paid")
    assert pay_ix["channel"] == "payment"
    assert pay_ix["payload"].get("stage") == "pay"
    assert pay_ix["payload"].get("sandbox") is True

    with crm.db() as db:
        rows = list(
            db.scalars(
                select(Interaction).where(
                    Interaction.person_id == detail["person_id"],
                    Interaction.kind == "deal.paid",
                )
            )
        )
        assert len(rows) == 1
