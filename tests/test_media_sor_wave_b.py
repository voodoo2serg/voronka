"""Media SoR Wave B: Signal/Registry asset_id → Voronka bank + CRM soft-ref."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from unittest.mock import patch

import pytest
from sqlalchemy import select

from test_operational import system  # noqa: F401 — fixture for crm
from test_crm import crm, contact  # noqa: F401 — crm fixture for m2m chain
from test_m2m_communications import _headers, m2m, _seed_identity  # noqa: F401
from app.db import Asset
from app.crm_models import Interaction
from app.signal_media import FetchedMedia


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def m2m_wave_b(m2m, monkeypatch):
    monkeypatch.setenv(
        "SIGNAL_M2M_SCOPES",
        "campaign:*,asset:read,asset:import,contact:segment:read,"
        "connection:hub:read,person:read,timeline:read,timeline:write",
    )
    return m2m


def test_import_persists_signal_asset_id(m2m_wave_b):
    digest = hashlib.sha256(PNG).hexdigest()
    payload = {
        "name": "pixel",
        "kind": "photo",
        "connection_ids": ["tg"],
        "content_base64": base64.b64encode(PNG).decode(),
        "sha256": digest,
        "signal_asset_id": "sig-asset-wave-b-1",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    r = m2m_wave_b.client.post(
        "/api/crm/m2m/projects/p/assets/import",
        content=body,
        headers=_headers(body, scopes="asset:import"),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["signal_asset_id"] == "sig-asset-wave-b-1"
    assert data["duplicate"] is False
    with m2m_wave_b.db() as db:
        asset = db.get(Asset, data["id"])
        assert asset is not None
        assert (asset.metadata_json or {}).get("signal_asset_id") == "sig-asset-wave-b-1"
        # Never store Telegram file_id on import
        assert asset.telegram_refs == {} or "file_id" not in json.dumps(asset.telegram_refs)


def test_ensure_from_signal_imports_bytes(m2m_wave_b):
    fetched = FetchedMedia(
        asset_id="reg-asset-99",
        data=PNG,
        content_type="image/png",
        source="media_registry",
        sha256=hashlib.sha256(PNG).hexdigest(),
        original_filename="dot.png",
        kind="photo",
    )
    payload = {
        "signal_asset_id": "reg-asset-99",
        "connection_ids": ["tg"],
        "name": "from-registry",
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    with patch("app.signal_media.fetch_asset_bytes", return_value=fetched):
        r = m2m_wave_b.client.post(
            "/api/crm/m2m/projects/p/assets/ensure-from-signal",
            content=body,
            headers=_headers(body, scopes="asset:import"),
        )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["signal_asset_id"] == "reg-asset-99"
    assert first["duplicate"] is False
    aid = first["id"]

    with patch("app.signal_media.fetch_asset_bytes", return_value=fetched):
        r2 = m2m_wave_b.client.post(
            "/api/crm/m2m/projects/p/assets/ensure-from-signal",
            content=body,
            headers=_headers(body, scopes="asset:import"),
        )
    assert r2.status_code == 200
    assert r2.json()["id"] == aid
    assert r2.json()["duplicate"] is True


def test_soft_attach_and_list(m2m_wave_b):
    person = contact(m2m_wave_b, name="Media Soft")
    person_id = person["person_id"]
    payload = {
        "signal_asset_id": "soft-asset-abc",
        "role": "attachment",
        "name": "lead magnet",
        "ensure_delivery": False,
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    with patch("app.signal_media.link_communication_asset", return_value={"id": "link-1", "created": True}):
        r = m2m_wave_b.client.post(
            f"/api/crm/m2m/projects/p/persons/{person_id}/media-refs",
            content=body,
            headers=_headers(body, scopes="timeline:write"),
        )
    assert r.status_code == 200, r.text
    assert r.json()["library"] is False
    assert r.json()["signal_asset_id"] == "soft-asset-abc"
    assert r.json()["voronka_asset_id"] is None

    empty = b""
    listed = m2m_wave_b.client.request(
        "GET",
        f"/api/crm/m2m/projects/p/persons/{person_id}/media-refs",
        content=empty,
        headers=_headers(empty, scopes="timeline:read"),
    )
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert any(i["signal_asset_id"] == "soft-asset-abc" for i in items)

    with m2m_wave_b.db() as db:
        rows = list(
            db.scalars(
                select(Interaction).where(
                    Interaction.person_id == person_id,
                    Interaction.kind == "media.soft_ref",
                )
            )
        )
        assert len(rows) == 1
        assert rows[0].payload.get("library") is False


def test_campaign_preflight_rewrites_signal_asset(m2m_wave_b):
    person = contact(m2m_wave_b, name="Camp Media")
    _seed_identity(m2m_wave_b, person["id"])
    fetched = FetchedMedia(
        asset_id="intent-asset-7",
        data=PNG,
        content_type="image/png",
        source="media_registry",
        sha256=hashlib.sha256(PNG).hexdigest(),
        kind="photo",
    )
    create = {
        "connection_id": "tg",
        "title": "Wave B",
        "text": "Hello {name}",
        "profile_ids": [person["id"]],
        "asset_id": "intent-asset-7",
        "external_idempotency_key": "wave-b-" + secrets.token_hex(4),
    }
    body = json.dumps(create, separators=(",", ":")).encode()
    r = m2m_wave_b.client.post(
        "/api/crm/m2m/projects/p/campaigns",
        content=body,
        headers=_headers(body, scopes="campaign:create"),
    )
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert r.json()["asset_id"] == "intent-asset-7"

    empty = b"{}"
    with patch("app.signal_media.fetch_asset_bytes", return_value=fetched), patch(
        "app.signal_media.media_fetch_configured", return_value=True
    ):
        pre = m2m_wave_b.client.post(
            f"/api/crm/m2m/campaigns/{cid}/preflight",
            content=empty,
            headers=_headers(empty, scopes="campaign:preflight"),
        )
    assert pre.status_code == 200, pre.text
    assert pre.json()["asset_rewritten"] is True
    assert pre.json()["asset_blocker"] is None
    voronka_id = pre.json()["asset_id"]
    assert voronka_id != "intent-asset-7"
    with m2m_wave_b.db() as db:
        asset = db.get(Asset, voronka_id)
        assert asset is not None
        assert (asset.metadata_json or {}).get("signal_asset_id") == "intent-asset-7"


def test_content_proxy_never_leaks_file_id(m2m_wave_b):
    fetched = FetchedMedia(
        asset_id="proxy-1",
        data=PNG,
        content_type="image/png",
        source="signal_internal",
        sha256=hashlib.sha256(PNG).hexdigest(),
        kind="photo",
    )
    empty = b""
    with patch("app.signal_media.fetch_asset_bytes", return_value=fetched):
        r = m2m_wave_b.client.request(
            "GET",
            "/api/crm/m2m/projects/p/media/signal/proxy-1/content",
            content=empty,
            headers=_headers(empty, scopes="asset:read"),
        )
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers.get("X-Media-Source") == "signal_internal"
    assert r.headers.get("X-Media-Asset-Id") == "proxy-1"
    assert "file_id" not in r.headers
    assert b"file_id" not in r.content
