"""CRM/ops static must not stick in browser cache (stale + → CMS cart hop)."""

from app.main import _portal_cache_control


def test_portal_html_shell_is_no_store():
    assert _portal_cache_control("/crm/") == "no-store, must-revalidate"
    assert _portal_cache_control("/crm/index.html") == "no-store, must-revalidate"
    assert _portal_cache_control("/ops/") == "no-store, must-revalidate"


def test_portal_hashed_chunks_are_immutable():
    assert (
        _portal_cache_control("/crm/_next/static/chunks/2v0l84osl6yry.js")
        == "public, max-age=31536000, immutable"
    )


def test_non_portal_paths_untouched():
    assert _portal_cache_control("/api/crm/health") is None
    assert _portal_cache_control("/health") is None
