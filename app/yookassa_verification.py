from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import os

import httpx
from fastapi import HTTPException


async def verified_payment(payment_id: str) -> dict:
    if not payment_id or len(payment_id) > 100:
        raise HTTPException(400, "Invalid payment id")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            response = await client.get(
                "https://api.yookassa.ru/v3/payments/" + quote(payment_id, safe=""),
                auth=(os.environ["YOOKASSA_SHOP_ID"], os.environ["YOOKASSA_SECRET_KEY"]),
            )
        response.raise_for_status()
        payment = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Payment verification unavailable") from exc
    if not isinstance(payment, dict) or payment.get("id") != payment_id:
        raise HTTPException(502, "Invalid provider response")
    recipient = payment.get("recipient") or {}
    if recipient.get("account_id") != os.environ["YOOKASSA_SHOP_ID"]:
        raise HTTPException(403, "Payment shop mismatch")
    return payment


def amount_minor(payment: dict) -> int:
    amount = payment.get("amount") or {}
    if amount.get("currency") != "RUB":
        raise HTTPException(409, "Payment currency mismatch")
    try:
        value = Decimal(str(amount.get("value"))) * 100
        if not value.is_finite() or value <= 0 or value != value.to_integral_value():
            raise ValueError("Invalid amount")
        return int(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise HTTPException(409, "Invalid payment amount") from exc
