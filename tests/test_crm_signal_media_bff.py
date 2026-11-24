"""B-02: CRM staff BFF for Signal media library (list/create/upload)."""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest

from test_operational import system  # noqa: F401
from test_crm import crm  # noqa: F401


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def crm_logged(crm):
    r = crm.client.post(
        "/api/crm/login",
        json={"login": "owner", "password": "owner-password-123"},
    )
    assert r.status_code == 200
    crm.client.headers["Authorization"] = "Bearer " + r.json()["token"]
    return crm


def test_list_signal_media_proxies_project_assets(crm_logged, monkeypatch):
    monkeypatch.setenv("SIGNAL_INTERNAL_MEDIA_BASE_URL", "http://signal.test/api/v1")
    monkeypatch.setenv("SIGNAL_INTERNAL_MEDIA_TOKEN", "tok")
    payload = {
        "voronka_project_id": "p",
        "items": [
            {
                "asset_id": "a1",
                "kind": "tg_post",
                "project_id": "p",
                "title": "Пост",
                "preview_url": None,
            }
        ],
    }
    with patch("app.signal_media.list_project_assets", return_value=payload) as mocked:
        r = crm_logged.client.get("/api/crm/projects/p/signal-media")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["items"][0]["asset_id"] == "a1"
    assert data["project_id"] == "p"
    assert "signal_open_url" in data
    mocked.assert_called_once()


def test_list_signal_media_requires_project_access(crm_logged):
    with patch(
        "app.signal_media.list_project_assets",
        return_value={"voronka_project_id": "secret", "items": []},
    ):
        # owner can access all projects in this fixture — use limited staff
        assert (
            crm_logged.client.post(
                "/api/crm/staff",
                json={"login": "media-lim", "password": "limited-password-12", "project_ids": ["secret"]},
            ).status_code
            == 200
        )
        login = crm_logged.client.post(
            "/api/crm/login",
            json={"login": "media-lim", "password": "limited-password-12"},
        )
        crm_logged.client.headers["Authorization"] = "Bearer " + login.json()["token"]
        r = crm_logged.client.get("/api/crm/projects/p/signal-media")
    assert r.status_code == 404


def test_create_meta_and_upload(crm_logged, monkeypatch):
    monkeypatch.setenv("SIGNAL_INTERNAL_MEDIA_BASE_URL", "http://signal.test/api/v1")
    monkeypatch.setenv("SIGNAL_INTERNAL_MEDIA_TOKEN", "tok")
    created = {
        "id": "meta-1",
        "content_kind": "poll",
        "voronka_project_id": "p",
        "title": "Опрос",
        "preview_url": None,
        "source": "signal_meta",
    }
    with patch("app.signal_media.create_meta_asset", return_value=created) as c:
        r = crm_logged.client.post(
            "/api/crm/projects/p/signal-media/create",
            json={"content_kind": "poll", "title": "Опрос", "body_text": "A/B"},
        )
    assert r.status_code == 201, r.text
    assert r.json()["asset_id"] == "meta-1"
    assert r.json()["kind"] == "poll"
    c.assert_called_once()

    uploaded = {
        "id": "img-1",
        "content_kind": "image",
        "voronka_project_id": "p",
        "title": "pic",
        "preview_url": "/api/v1/media/img-1/content?profile=thumb",
        "source": "signal_transitional",
    }
    with patch("app.signal_media.upload_project_asset", return_value=uploaded) as u:
        r2 = crm_logged.client.post(
            "/api/crm/projects/p/signal-media",
            data={"content_kind": "image", "title": "pic"},
            files={"file": ("x.png", PNG, "image/png")},
        )
    assert r2.status_code == 201, r2.text
    assert r2.json()["asset_id"] == "img-1"
    u.assert_called_once()


def test_meta_kinds_endpoint(crm_logged):
    r = crm_logged.client.get("/api/crm/projects/p/signal-media/meta")
    assert r.status_code == 200
    body = r.json()
    assert "poll" in body["content_kinds"]
    assert body["content_kind_labels"]["tg_post"]
    assert "signal_open_url" in body


def test_frontend_media_library_markers():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    constants = (root / "web/app/crm/constants.ts").read_text(encoding="utf-8")
    assert "['media', 'Медиатека']" in constants
    ctor = (root / "web/app/crm/sections/ActionConstructorSection.tsx").read_text(encoding="utf-8")
    assert "Из медиатеки" in ctor
    assert "Открыть в Signal" in ctor
    assert "onOpenMediaLibrary" in ctor
    section = (root / "web/app/crm/sections/MediaLibrarySection.tsx").read_text(encoding="utf-8")
    assert "signal-media" in section
    assert "Открыть в Signal" in section
    ws = (root / "web/app/crm/Workspace.tsx").read_text(encoding="utf-8")
    assert "MediaLibrarySection" in ws
    assert "#media" in ws
