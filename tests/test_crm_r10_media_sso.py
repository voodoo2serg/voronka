"""CRM-R10: Signal media SSO deep-link + return_to encoding + overlay pick markers."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.portals import crm_constructor_square_url, signal_media_library_url

ROOT = Path(__file__).resolve().parents[1]


def test_return_to_fully_encoded_keeps_node_and_section():
    """Codex P1 (signal#61): unencoded &node=/&section= would truncate as Signal params."""
    pid = "11111111-1111-1111-1111-111111111111"
    node = "sq-1"
    return_to = crm_constructor_square_url(project_id=pid, node_id=node)
    assert "section=chains" in return_to
    assert f"node={node}" in return_to
    assert "#today" not in return_to  # query, not hash (Signal appends asset_id)

    url = signal_media_library_url(project=pid, return_to=return_to)
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs.get("view") == ["media"]
    assert qs.get("project") == [pid]
    # Raw sibling params must NOT appear — only inside encoded return_to.
    assert "node" not in qs
    assert "section" not in qs
    raw_return = qs.get("return_to", [""])[0]
    assert raw_return
    # parse_qs already decodes once; ensure node/section survived.
    assert f"node={node}" in raw_return
    assert "section=chains" in raw_return
    # Wire form must percent-encode & before the query string joins.
    assert "%26node%3D" in url or "%26node=" in url.lower() or "return_to=" in url
    assert "&node=" not in url.split("return_to=")[0]  # no bare node before return_to value
    # Entire return_to value is one query param (no bare &node= after return_to=).
    after = url.split("return_to=", 1)[1]
    # must be encoded, not raw https://… (otherwise &node= truncates)
    assert not after.startswith("https://") and not after.startswith("http://")
    assert after.startswith("https%3A") or after.startswith("http%3A")
    encoded_value = after.split("&")[0]
    decoded = unquote(encoded_value)
    assert "node=sq-1" in decoded
    assert "section=chains" in decoded
    assert "%26node%3D" in encoded_value or "%26node=" in encoded_value.lower()


def test_constructor_meta_media_url_encodes_return_to(crm):
    login = crm.client.post(
        "/api/crm/login", json={"login": "owner", "password": "owner-password-123"}
    )
    assert login.status_code == 200, login.text
    crm.client.headers["Authorization"] = "Bearer " + login.json()["token"]
    me = crm.client.get("/api/crm/me").json()
    projects = me.get("projects") or []
    assert projects
    pid = projects[0]["id"]
    r = crm.client.get(f"/api/crm/constructor/meta?project_id={pid}")
    assert r.status_code == 200, r.text
    media = r.json()["media_library_url"]
    assert "view=media" in media
    assert "return_to=" in media
    assert media.split("return_to=", 1)[1].startswith("https%3A") or media.split("return_to=", 1)[
        1
    ].startswith("http%3A")
    assert "&node=" not in media  # node only inside encoded return_to when present
    assert "section%3Dchains" in media or "section%253Dchains" in media


def test_signal_media_bff_open_url_encodes_return_to(crm):
    login = crm.client.post(
        "/api/crm/login", json={"login": "owner", "password": "owner-password-123"}
    )
    assert login.status_code == 200, login.text
    crm.client.headers["Authorization"] = "Bearer " + login.json()["token"]
    me = crm.client.get("/api/crm/me").json()
    pid = me["projects"][0]["id"]
    r = crm.client.get(f"/api/crm/projects/{pid}/signal-media/meta")
    assert r.status_code == 200, r.text
    open_url = r.json()["signal_open_url"]
    assert "return_to=" in open_url
    assert open_url.split("return_to=", 1)[1].startswith("https%3A") or open_url.split(
        "return_to=", 1
    )[1].startswith("http%3A")


def test_frontend_r10_markers():
    portals = (ROOT / "web/app/crm/portals.ts").read_text(encoding="utf-8")
    assert "encodeURIComponent(opts.returnTo)" in portals
    assert "section', 'chains'" in portals or 'section", "chains"' in portals or "section', 'chains'" in portals
    assert "crmConstructorSquareHref" in portals
    ctor = (ROOT / "web/app/crm/sections/ActionConstructorSection.tsx").read_text(encoding="utf-8")
    assert "openMediaOverlay" in ctor
    assert "openSignalMedia" in ctor
    assert "pendingAssetId" in ctor
    assert "media_asset_id" in ctor
    assert "square-media-library" in ctor
    assert "square-media-sso" in ctor
    assert "onOpenMediaLibrary" in ctor
    ws = (ROOT / "web/app/crm/Workspace.tsx").read_text(encoding="utf-8")
    assert "pickMode" in ws
    assert "mediaPickNodeId" in ws
    assert "ctorPendingAssetId" in ws
    assert "applyLocationSearch" in ws
    assert "asset_id" in ws
    media = (ROOT / "web/app/crm/sections/MediaLibrarySection.tsx").read_text(encoding="utf-8")
    assert "returnNodeId" in media
    assert "crmConstructorSquareHref" in media
