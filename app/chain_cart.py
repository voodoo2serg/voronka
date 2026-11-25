"""CART-04: fetch Solo-PR CMS chain cart over inbound HMAC (Voronka → CMS).

CMS contract (CART-03): GET/POST /api/integrations/carts with
X-Voronka-Timestamp + X-Voronka-Signature over body (GET → empty body).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.integrations import format_signature, sign_payload
from app.db import now
from app.portals import PORTAL_URLS

# Cart asset kind (Signal/CMS) → Action Constructor node kind.
CART_KIND_TO_NODE: dict[str, str] = {
    "tg_post": "telegram",
    "tg_circle": "telegram",
    "tg_cover": "telegram",
    "vk_video": "contact",
    "email_body": "email",
    "poll": "survey",
    "script": "contact",
    "image": "contact",
    "file": "contact",
}

CART_KIND_LABELS: dict[str, str] = {
    "tg_post": "Пост Telegram",
    "tg_circle": "Кружочек",
    "tg_cover": "Обложка TG",
    "vk_video": "VK video",
    "email_body": "Письмо",
    "poll": "Опрос",
    "script": "Скрипт",
    "image": "Изображение",
    "file": "Файл",
}


def cms_base_url() -> str:
    return (os.getenv("SOLO_PR_CMS_URL") or PORTAL_URLS["cms"]).rstrip("/")


def cms_cart_api_url() -> str:
    override = (os.getenv("CHAIN_CART_API_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return cms_base_url() + "/api/integrations/carts"


def cms_hmac_secret() -> str:
    return (os.getenv("SOLO_PR_WEBHOOK_SECRET") or "").strip()


def map_cart_kind_to_node(kind: str) -> str | None:
    key = str(kind or "").strip().lower()
    return CART_KIND_TO_NODE.get(key)


def enrich_cart_payload(cart: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize CMS cart for Constructor UI (items + mapped node kinds)."""
    if not cart or not isinstance(cart, dict):
        return {
            "id": None,
            "cart_id": None,
            "project_id": None,
            "items": [],
            "kinds": [],
            "node_kinds": [],
            "empty": True,
        }
    raw_items = cart.get("items") if isinstance(cart.get("items"), list) else []
    items: list[dict[str, Any]] = []
    kinds: list[str] = []
    node_kinds: list[str] = []
    seen_kinds: set[str] = set()
    seen_nodes: set[str] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        asset_id = str(raw.get("asset_id") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        if not asset_id or not kind:
            continue
        node_kind = map_cart_kind_to_node(kind)
        title = str(raw.get("title") or "").strip() or CART_KIND_LABELS.get(kind, kind)
        items.append(
            {
                "asset_id": asset_id[:80],
                "kind": kind,
                "title": title[:200],
                "node_kind": node_kind,
                "kind_label": CART_KIND_LABELS.get(kind, kind),
            }
        )
        if kind not in seen_kinds:
            seen_kinds.add(kind)
            kinds.append(kind)
        if node_kind and node_kind not in seen_nodes:
            seen_nodes.add(node_kind)
            node_kinds.append(node_kind)
    cart_id = str(cart.get("cart_id") or cart.get("id") or "").strip() or None
    project_id = str(cart.get("project_id") or "").strip() or None
    return {
        "id": cart_id,
        "cart_id": cart_id,
        "project_id": project_id,
        "items": items,
        "kinds": kinds or list(cart.get("kinds") or []),
        "node_kinds": node_kinds,
        "empty": len(items) == 0,
        "created_at": cart.get("created_at"),
        "updated_at": cart.get("updated_at"),
        # Soft protocol (VR-513): never advertise CMS cart/admin hop to staff UI.
        "cms_cart_url": None,
    }


def _signed_headers(body: bytes) -> dict[str, str]:
    secret = cms_hmac_secret()
    if not secret:
        raise RuntimeError("SOLO_PR_WEBHOOK_SECRET not configured")
    ts = str(int(now().timestamp()))
    digest = sign_payload(secret, body, ts)
    sig = format_signature(digest)
    return {
        "Content-Type": "application/json",
        "X-Voronka-Timestamp": ts,
        "X-Voronka-Signature": sig,
        "X-Solo-Timestamp": ts,
        "X-Solo-Signature": sig,
        "Authorization": f"Bearer {secret}",
    }


def fetch_cms_cart(
    *,
    cart_id: str | None = None,
    project_id: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Call CMS integrations cart API. Raises httpx.HTTPStatusError on non-2xx."""
    cid = str(cart_id or "").strip()
    pid = str(project_id or "").strip()
    if not cid and not pid:
        raise ValueError("cart_id or project_id required")

    url = cms_cart_api_url()
    # Prefer GET with empty body (HMAC over ""); POST body as fallback path for proxies.
    params: dict[str, str] = {}
    if cid:
        params["cart_id"] = cid
    else:
        params["project_id"] = pid
    # GET HMAC is over empty body (CMS route.ts: readCart(req, '')).
    headers = _signed_headers(b"")
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, params=params, headers=headers)
        if response.status_code == 404:
            return enrich_cart_payload(None) | {
                "not_found": True,
                "project_id": pid or None,
                "cart_id": cid or None,
                "cms_cart_url": None,
            }
        response.raise_for_status()
        data = response.json()
    cart = data.get("cart") if isinstance(data, dict) else None
    return enrich_cart_payload(cart if isinstance(cart, dict) else None)


def fetch_cms_cart_post(
    *,
    cart_id: str | None = None,
    project_id: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST variant (HMAC over JSON body) — used by tests / alternate consumers."""
    cid = str(cart_id or "").strip()
    pid = str(project_id or "").strip()
    if not cid and not pid:
        raise ValueError("cart_id or project_id required")
    payload: dict[str, str] = {}
    if cid:
        payload["cart_id"] = cid
    if pid:
        payload["project_id"] = pid
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    headers = _signed_headers(body)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(cms_cart_api_url(), content=body, headers=headers)
        if response.status_code == 404:
            return enrich_cart_payload(None) | {
                "not_found": True,
                "project_id": pid or None,
                "cart_id": cid or None,
            }
        response.raise_for_status()
        data = response.json()
    cart = data.get("cart") if isinstance(data, dict) else None
    return enrich_cart_payload(cart if isinstance(cart, dict) else None)
