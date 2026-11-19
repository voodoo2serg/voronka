"""Robokassa fiscal Receipt (54-FZ) for payment initiation.

Docs: https://docs.robokassa.ru/ru/fiscalization
Signature with Receipt: MerchantLogin:OutSum:InvId:Receipt:Password#1
where Receipt is URL-encoded JSON (quote, not '+'-space).

Checkout GET URLs must double-encode Receipt on the wire (%257B…): signature
uses the single-encoded form; one query decode must recover that form
(see https://docs.robokassa.ru/ru/saving request examples).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any
from urllib.parse import quote, urlencode

ALLOWED_TAX = frozenset({
    "none", "vat0", "vat10", "vat110", "vat20", "vat22", "vat120", "vat122",
    "vat5", "vat7", "vat105", "vat107",
})
ALLOWED_SNO = frozenset({
    "osn", "usn_income", "usn_income_outcome", "esn", "patent",
})
ALLOWED_PAYMENT_METHOD = frozenset({
    "full_prepayment", "prepayment", "advance", "full_payment",
    "partial_payment", "credit", "credit_payment",
})
ALLOWED_PAYMENT_OBJECT = frozenset({
    "commodity", "excise", "job", "service", "gambling_bet", "gambling_prize",
    "lottery", "lottery_prize", "intellectual_activity", "payment",
    "agent_commission", "composite", "resort_fee", "another", "property_right",
    "non-operating_gain", "insurance_premium", "sales_tax", "tovar_mark",
})
ALLOWED_HASH = frozenset({"md5", "sha256", "sha512"})

_NAME_CLEAN = re.compile(r"[\x00-\x1f\x7f<>]")


def money_major(amount_minor: int) -> str:
    """Rubles with two decimals for OutSum / Receipt item sum."""
    if amount_minor < 0:
        raise ValueError("amount_minor must be non-negative")
    return f"{amount_minor // 100}.{amount_minor % 100:02d}"


def money_major_number(amount_minor: int) -> float:
    """Numeric major units for Receipt JSON (up to 2 decimal places)."""
    return round(amount_minor / 100, 2)


def receipt_tax() -> str:
    tax = (os.getenv("ROBOKASSA_RECEIPT_TAX") or "none").strip().lower()
    if tax not in ALLOWED_TAX:
        raise ValueError(f"Unsupported ROBOKASSA_RECEIPT_TAX={tax!r}")
    return tax


def receipt_sno() -> str | None:
    raw = (os.getenv("ROBOKASSA_RECEIPT_SNO") or "").strip().lower()
    if not raw:
        return None
    if raw not in ALLOWED_SNO:
        raise ValueError(f"Unsupported ROBOKASSA_RECEIPT_SNO={raw!r}")
    return raw


def receipt_payment_object() -> str:
    value = (os.getenv("ROBOKASSA_RECEIPT_PAYMENT_OBJECT") or "service").strip().lower()
    if value not in ALLOWED_PAYMENT_OBJECT:
        raise ValueError(f"Unsupported ROBOKASSA_RECEIPT_PAYMENT_OBJECT={value!r}")
    return value


def receipt_payment_method(*, amount_minor: int, order_total_minor: int) -> str:
    """Default full_prepayment for full order; prepayment for partial invoices."""
    configured = (os.getenv("ROBOKASSA_RECEIPT_PAYMENT_METHOD") or "").strip().lower()
    if configured:
        if configured not in ALLOWED_PAYMENT_METHOD:
            raise ValueError(f"Unsupported ROBOKASSA_RECEIPT_PAYMENT_METHOD={configured!r}")
        return configured
    if amount_minor < order_total_minor:
        return "prepayment"
    return "full_prepayment"


def item_name(snapshot: dict | None, *, fallback: str = "Оплата") -> str:
    raw = ""
    if isinstance(snapshot, dict):
        raw = str(snapshot.get("name") or "").strip()
    name = _NAME_CLEAN.sub(" ", raw).strip() or fallback
    return name[:128]


def build_receipt(
    *,
    amount_minor: int,
    order_total_minor: int,
    snapshot: dict | None,
) -> dict[str, Any]:
    """Build Receipt JSON object; item sum must equal OutSum (this invoice amount)."""
    if amount_minor <= 0:
        raise ValueError("Receipt amount_minor must be positive")
    tax = receipt_tax()
    method = receipt_payment_method(amount_minor=amount_minor, order_total_minor=order_total_minor)
    obj = receipt_payment_object()
    receipt: dict[str, Any] = {
        "items": [
            {
                "name": item_name(snapshot),
                "quantity": 1,
                "sum": money_major_number(amount_minor),
                "payment_method": method,
                "payment_object": obj,
                "tax": tax,
            }
        ]
    }
    sno = receipt_sno()
    if sno:
        receipt["sno"] = sno
    return receipt


def encode_receipt(receipt: dict[str, Any]) -> tuple[str, str]:
    """Return (compact JSON, URL-encoded JSON for signature and query)."""
    raw = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    encoded = quote(raw, safe="")
    return raw, encoded


def sign_payment(
    *,
    merchant: str,
    out_sum: str,
    inv_id: str,
    password1: str,
    receipt_encoded: str,
    algorithm: str = "sha256",
) -> str:
    algo = algorithm.lower()
    if algo not in ALLOWED_HASH:
        raise ValueError(f"Unknown Robokassa hash algorithm: {algo}")
    payload = f"{merchant}:{out_sum}:{inv_id}:{receipt_encoded}:{password1}"
    return hashlib.new(algo, payload.encode("utf-8")).hexdigest()


def payment_query(
    *,
    merchant: str,
    out_sum: str,
    inv_id: str,
    password1: str,
    description: str,
    is_test: bool,
    receipt: dict[str, Any],
    algorithm: str | None = None,
) -> dict[str, Any]:
    """Build signed payment fields + checkout URL (GET; small single-item Receipt)."""
    algo = (algorithm or os.getenv("ROBOKASSA_HASH", "sha256")).lower()
    if algo not in ALLOWED_HASH:
        raise ValueError(f"Unknown Robokassa hash algorithm: {algo}")
    _raw, receipt_encoded = encode_receipt(receipt)
    signature = sign_payment(
        merchant=merchant,
        out_sum=out_sum,
        inv_id=inv_id,
        password1=password1,
        receipt_encoded=receipt_encoded,
        algorithm=algo,
    )
    base = {
        "MerchantLogin": merchant,
        "OutSum": out_sum,
        "InvId": inv_id,
        "Description": (description or "Оплата")[:100],
        "SignatureValue": signature,
        "IsTest": int(bool(is_test)),
    }
    # Signature uses single-encoded Receipt (%7B...). On the wire Robokassa expects
    # double-encoding (%257B...) so one query decode yields the signed value
    # (official examples: docs.robokassa.ru/ru/saving).
    query = urlencode({**base, "Receipt": receipt_encoded})
    return {
        "url": "https://auth.robokassa.ru/Merchant/Index.aspx?" + query,
        # Form/POST field value = single-encoded (browser/form encodes again on submit).
        "params": {**base, "Receipt": receipt_encoded},
        "receipt": receipt,
        "receipt_encoded": receipt_encoded,
        "signature_algorithm": algo,
    }


def receipt_env_configured() -> dict[str, Any]:
    """Honesty flags for CRM fiscal checklist (no secrets)."""
    tax = (os.getenv("ROBOKASSA_RECEIPT_TAX") or "none").strip().lower()
    sno = (os.getenv("ROBOKASSA_RECEIPT_SNO") or "").strip().lower()
    tax_ok = tax in ALLOWED_TAX
    sno_ok = (not sno) or sno in ALLOWED_SNO
    return {
        "receipt_in_adapter": True,
        "tax": tax if tax_ok else None,
        "tax_valid": tax_ok,
        "sno": sno or None,
        "sno_valid": sno_ok,
        "tax_env_set": bool(os.getenv("ROBOKASSA_RECEIPT_TAX")),
        "sno_env_set": bool(os.getenv("ROBOKASSA_RECEIPT_SNO")),
    }
