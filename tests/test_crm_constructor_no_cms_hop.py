"""VR-507: Full square constructor IA vs live tip cart hop.

Live tip (564f0b3) turned empty-cart «+» into <a href=cms…/#/admin/cart>, limited
«+» to cart kinds, and kept sender in the drawer. Canon remodel CRM-R2/R3:

- ZERO staff hops to CMS cart
- Fixed «Клиент получает»; + RIGHT = full meta.node_kinds → new square
- + TOP = text/details OR Signal media (in-CRM / SSO); BOTTOM = vault From/bot
- Проверить = dry-run; Сохранить = Project/Branch base chain
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CTOR = ROOT / "web" / "app" / "crm" / "sections" / "ActionConstructorSection.tsx"
WS = ROOT / "web" / "app" / "crm" / "Workspace.tsx"
PORTALS = ROOT / "web" / "app" / "crm" / "portals.ts"
CSS = ROOT / "web" / "app" / "globals.css"


def _texts() -> tuple[str, str, str, str]:
    return (
        CTOR.read_text(encoding="utf-8"),
        WS.read_text(encoding="utf-8"),
        PORTALS.read_text(encoding="utf-8"),
        CSS.read_text(encoding="utf-8"),
    )


def test_zero_cms_cart_staff_hops():
    ctor, ws, portals, css = _texts()
    for blob, label in ((ctor, "ctor"), (ws, "ws"), (portals, "portals")):
        assert "Набрать корзину в CMS" not in blob, label
        assert "Корзина CMS" not in blob, label
        assert "Шаги из корзины" not in blob, label
        assert "admin/cart" not in blob, label
    assert "cmsChainCartHref" not in ctor
    assert "cmsChainCartHref" not in portals
    assert "cms_cart_url" not in ctor
    assert "constructor/cart" not in ctor
    assert "addFromCartItem" not in ctor
    assert "href={cartUrl}" not in ctor
    assert "constructor-plus-cta" not in ctor
    assert "constructor-plus-cta" not in css
    assert "constructor-cart-rail" not in css
    assert "constructor-cart-link" not in css
    assert "constructor-cart-link" not in ctor
    assert "data-cart-route" not in ctor
    assert "cartId=" not in ws
    assert "setCartId" not in ws
    # Soft protocol: no staff CMS hop CTAs from constructor / no-project surfaces
    assert "CMS → выдать staff" not in ws
    assert "Открыть CMS" not in ws
    assert "cmsMatrix.cms_url" not in ws
    assert "constructor-vault-channels" in ctor
    assert "constructor-soft-empty" in ctor
    assert "сначала шаги" in ctor.lower() or "Сначала соберите" in ctor


def test_bff_never_returns_cms_cart_admin_url():
    """VR-513: chain_cart / constructor cart BFF must not emit #/admin/cart."""
    chain = (ROOT / "app" / "chain_cart.py").read_text(encoding="utf-8")
    bff = (ROOT / "app" / "crm_routers" / "action_chains.py").read_text(encoding="utf-8")
    assert "#/admin/cart" not in chain
    assert "#/admin/cart" not in bff
    assert 'cms_cart_url": None' in chain or "cms_cart_url': None" in chain
    assert 'cms_cart_url"] = None' in bff or 'cms_cart_url": None' in bff


def test_square_ia_right_top_bottom():
    ctor, _ws, _portals, css = _texts()
    # Fixed start + horizontal squares
    assert "Клиент получает" in ctor
    assert "Старт цепочки" in ctor
    assert "constructor-flow-squares" in ctor
    assert "constructor-flow-squares" in css
    # + RIGHT → full node_kinds menu (start square)
    assert 'menuKey="flow-start"' in ctor
    assert 'testId="constructor-plus-start"' in ctor
    assert "Добавить шаг справа" in ctor
    assert 'aria-label="Типы шагов"' in ctor
    assert "addNodeKind" in ctor
    assert "constructor/meta" in ctor
    assert "nodeKinds" in ctor
    # + TOP → text / media / action details
    assert "Содержимое шага" in ctor
    assert "Текст / детали" in ctor
    assert "Медиатека" in ctor
    assert "Детали действия" in ctor
    assert "openMediaOverlay" in ctor
    assert "openSignalMedia" in ctor
    assert 'data-testid="square-media-library"' in ctor
    # Empty-state media must not crash / hop to CMS
    assert "openMediaBrowse" in ctor
    assert "onClick={openMedia}" not in ctor
    # BOTTOM vault sender on the square
    assert "constructor-square-sender" in ctor
    assert "constructor-square-sender" in css
    assert "FROM" in ctor
    assert "БОТ" in ctor or "Telegram-бот" in ctor


def test_proverit_and_save_base_chain_ui():
    ctor, _ws, _p, _c = _texts()
    assert "Проверить" in ctor
    assert "dry-run" in ctor
    assert "Сохранить" in ctor
    assert "базовая цепочка" in ctor
    assert "branch_id" in ctor
    assert "is_base" in ctor
    assert "onOpenMediaLibrary" in ctor


def test_meta_node_kinds_drive_plus_menu(crm):
    """Runtime: meta exposes full node_kinds; constructor + must use that list."""
    login = crm.client.post(
        "/api/crm/login", json={"login": "owner", "password": "owner-password-123"}
    )
    assert login.status_code == 200
    crm.client.headers["Authorization"] = "Bearer " + login.json()["token"]
    meta = crm.client.get("/api/crm/constructor/meta?project_id=p").json()
    kinds = {str(k["id"]) for k in (meta.get("node_kinds") or []) if k.get("id")}
    for required in ("contact", "survey", "email", "telegram", "payment", "code", "wait", "finish"):
        assert required in kinds, f"missing node_kind {required} in {sorted(kinds)}"
    ctor = CTOR.read_text(encoding="utf-8")
    assert "addNodeKind" in ctor
    assert "Типы шагов" in ctor


def test_workspace_media_overlay_keeps_constructor():
    ws = WS.read_text(encoding="utf-8")
    assert "mediaOverlay" in ws
    assert "MediaLibrarySection" in ws
    assert "onOpenMediaLibrary" in ws
    assert "ActionConstructorSection" in ws
