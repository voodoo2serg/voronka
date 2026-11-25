"""Action Constructor API: CRUD, dry-run «Проверить», publish → Funnel runtime."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.crm_deps import Body, Session, crm_actor, encode, must
from app.crm_models import ActionChain, ActionChainDryRun, Branch
from app.crm_service import audit, project_access, uid
from app.db import Funnel, Version, now
from app.graph import validate_graph
from app.portals import signal_media_library_url, signal_public_base
from app.projects_domain import (
    base_chain_summary,
    chain_to_funnel_graph,
    get_base_action_chain,
    promote_action_chain_as_base,
    validate_action_nodes,
)

router = APIRouter()

FROM_IDENTITIES = [
    {"id": "serg", "address": "serg@vodopetov.ru", "label": "serg@"},
    {"id": "raga", "address": "raga@vodopetov.ru", "label": "raga@"},
    {"id": "info", "address": "info@solo-pr.ru", "label": "info@solo-pr"},
]

# Canonical CRM-A15 (see internal/crm-a15-signal-media-deeplink.md / Signal PR #57).
SIGNAL_MEDIA_CANONICAL = "https://signal.vodopetov.ru/?view=media"


class ChainBody(Body):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=800)
    branch_id: str | None = None
    course_id: str | None = Field(default=None, max_length=200)
    nodes: list[dict[str, Any]] = Field(default_factory=list)


class ChainPatch(Body):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=800)
    branch_id: str | None = None
    course_id: str | None = Field(default=None, max_length=200)
    nodes: list[dict[str, Any]] | None = None
    revision: int


class DryRunBody(Body):
    email: str = Field(default="", max_length=250)
    telegram: str = Field(default="", max_length=120)


@router.get("/constructor/meta")
def constructor_meta(project_id: str | None = None, admin=Depends(crm_actor)):
    """Static Constructor helpers: node kinds, From / vault pickers, Signal media SSO deep-link."""
    media_url = signal_media_library_url() or SIGNAL_MEDIA_CANONICAL
    from_list = list(FROM_IDENTITIES)
    extra = (os.getenv("CRM_MAIL_FROM_IDENTITIES") or "").strip()
    if extra:
        for part in extra.split(","):
            addr = part.strip()
            if addr and "@" in addr:
                from_list.append({"id": addr, "address": addr, "label": addr.split("@")[0] + "@"})
    vault_email: list[dict] = []
    vault_payment: list[dict] = []
    vault_messaging: list[dict] = []
    if project_id:
        from app import connection_vault as cv

        with Session() as db:
            project_access(db, admin, project_id)
            vault_email = cv.list_for_picker(db, project_id, types=["email_smtp"])
            vault_payment = cv.list_for_picker(db, project_id, types=["robokassa", "yookassa"])
            vault_messaging = cv.list_for_picker(
                db, project_id, types=["telegram_bot", "max_bot", "telegram_channel"]
            )
            # Enrich From list from vault email rows (address in meta).
            for item in vault_email:
                addr = str((item.get("meta") or {}).get("from_address") or "").strip()
                if addr and not any(f.get("address") == addr for f in from_list):
                    from_list.append({
                        "id": f"vault:{item['id']}",
                        "address": addr,
                        "label": item.get("label") or addr.split("@")[0] + "@",
                        "vault_id": item["id"],
                    })
    return {
        "node_kinds": [
            {"id": "contact", "label": "Контакт"},
            {"id": "survey", "label": "Опрос"},
            {"id": "email", "label": "Письмо"},
            {"id": "telegram", "label": "Telegram"},
            {"id": "payment", "label": "Оплата"},
            {"id": "code", "label": "Код / доступ"},
            {"id": "wait", "label": "Пауза"},
            {"id": "finish", "label": "Финиш"},
        ],
        "from_identities": from_list,
        "vault_email": vault_email,
        "vault_payment": vault_payment,
        "vault_messaging": vault_messaging,
        "media_library_url": media_url,
        "signal_base": signal_public_base(),
        "payment_providers": [
            {"id": "robokassa", "label": "Robokassa"},
            {"id": "yookassa", "label": "ЮKassa"},
        ],
        "tariff_modes": [
            {"id": "fixed", "label": "Фиксированный тариф"},
            {"id": "open", "label": "Открытая сумма"},
        ],
        "client_receives_label": "Клиент получает",
        "secrets_exposed": False,
        # CART-04: cart asset kind → node kind (Constructor «+» from CMS cart).
        "cart_kind_map": {
            "tg_post": "telegram",
            "tg_circle": "telegram",
            "tg_cover": "telegram",
            "vk_video": "contact",
            "email_body": "email",
            "poll": "survey",
            "script": "contact",
            "image": "contact",
            "file": "contact",
        },
    }


@router.get("/constructor/cart")
def constructor_cart(
    project_id: str | None = None,
    cart_id: str | None = None,
    admin=Depends(crm_actor),
):
    """CART-04 BFF: staff session → CMS chain cart via Voronka HMAC (no secrets in browser)."""
    from app import chain_cart as cc

    pid = (project_id or "").strip()
    cid = (cart_id or "").strip()
    if not pid and not cid:
        raise HTTPException(422, "Укажите project_id или cart_id")
    if pid:
        with Session() as db:
            project_access(db, admin, pid)
    if not cc.cms_hmac_secret():
        empty = cc.enrich_cart_payload(None)
        empty.update(
            {
                "configured": False,
                "error": "cms_hmac_unconfigured",
                "project_id": pid or None,
                "cart_id": cid or None,
                "cms_cart_url": (
                    f"{cc.cms_base_url()}/#/admin/cart/{pid}" if pid else f"{cc.cms_base_url()}/#/admin/cart"
                ),
            }
        )
        return empty
    try:
        cart = cc.fetch_cms_cart(cart_id=cid or None, project_id=None if cid else pid or None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # httpx / network
        # Soft-fail: Constructor still opens without cart (CRM-R2: cart not staff UX).
        empty = cc.enrich_cart_payload(None)
        empty.update(
            {
                "configured": True,
                "error": type(exc).__name__[:80],
                "project_id": pid or None,
                "cart_id": cid or None,
                "cms_cart_url": (
                    f"{cc.cms_base_url()}/#/admin/cart/{pid}" if pid else f"{cc.cms_base_url()}/#/admin/cart"
                ),
            }
        )
        return empty
    cart["configured"] = True
    cart["error"] = None
    cart_pid = str(cart.get("project_id") or "").strip()
    if pid and cart_pid and cart_pid != pid:
        raise HTTPException(403, "Корзина принадлежит другому проекту")
    # When deep-link only has cart_id, still enforce staff project access on the cart's project.
    if cart_pid and not pid:
        with Session() as db:
            project_access(db, admin, cart_pid)
    if pid and not cart_pid:
        cart["project_id"] = pid
        cart["cms_cart_url"] = f"{cc.cms_base_url()}/#/admin/cart/{pid}"
    return cart


def _normalize_branch_query(branch_id: str | None) -> tuple[bool, str | None]:
    """Return (filter_applied, branch_id|None). filter_applied=False → no branch filter."""
    if branch_id is None:
        return False, None
    bid = branch_id.strip()
    if bid == "" or bid.lower() in {"none", "null", "project"}:
        return True, None
    return True, bid


@router.get("/projects/{pid}/action-chains")
def list_chains(
    pid: str,
    branch_id: str | None = None,
    base_only: bool = False,
    admin=Depends(crm_actor),
):
    with Session() as db:
        project_access(db, admin, pid)
        filtered, bid = _normalize_branch_query(branch_id)
        q = select(ActionChain).where(ActionChain.project_id == pid)
        if filtered:
            if bid:
                q = q.where(ActionChain.branch_id == bid)
            else:
                q = q.where(ActionChain.branch_id.is_(None))
        if base_only:
            q = q.where(ActionChain.is_base.is_(True))
        rows = list(
            db.scalars(
                q.order_by(ActionChain.is_base.desc(), ActionChain.updated_at.desc(), ActionChain.id)
            )
        )
        # Summary: scoped base when branch filtered; else project-level base.
        summary_bid = bid if filtered else None
        base = get_base_action_chain(db, pid, summary_bid)
        return {
            "items": [encode(r) for r in rows],
            "base_chain": base_chain_summary(base),
        }


@router.get("/projects/{pid}/base-action-chain")
def get_project_base_chain(pid: str, branch_id: str | None = None, admin=Depends(crm_actor)):
    """CRM-R5: base ActionChain for Project (default) or Branch — not fixed funnel template."""
    with Session() as db:
        project_access(db, admin, pid)
        bid = (branch_id or "").strip() or None
        if bid and bid.lower() in {"none", "null", "project"}:
            bid = None
        if bid:
            br = must(db.get(Branch, bid))
            if br.project_id != pid:
                raise HTTPException(422, "Ветка другого проекта")
        row = get_base_action_chain(db, pid, bid)
        if not row:
            return {"base_chain": None, "project_id": pid, "branch_id": bid}
        return {"base_chain": encode(row), "summary": base_chain_summary(row)}


@router.post("/projects/{pid}/action-chains")
def create_chain(pid: str, body: ChainBody, admin=Depends(crm_actor)):
    try:
        with Session.begin() as db:
            project_access(db, admin, pid)
            branch_id = (body.branch_id or "").strip() or None
            if branch_id:
                br = must(db.get(Branch, branch_id))
                if br.project_id != pid:
                    raise HTTPException(422, "Ветка другого проекта")
            try:
                nodes = validate_action_nodes(body.nodes)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            row = ActionChain(
                id=uid(),
                project_id=pid,
                branch_id=branch_id,
                course_id=(body.course_id or "").strip() or None,
                name=body.name.strip(),
                description=(body.description or "").strip(),
                state="draft",
                is_base=False,
                nodes=nodes,
                revision=1,
                actor_id=admin.telegram_id,
                created_at=now(),
                updated_at=now(),
            )
            db.add(row)
            db.flush()
            # CRM-R5: Сохранить → base chain for Project (or Branch if selected).
            promote_action_chain_as_base(db, row)
            audit(
                db,
                admin,
                "action_chain.created",
                project_id=pid,
                chain_id=row.id,
                is_base=True,
                branch_id=row.branch_id,
            )
            return encode(row)
    except IntegrityError as exc:
        raise HTTPException(409, "Базовая цепочка уже меняется — повторите сохранение") from exc


@router.get("/action-chains/{cid}")
def get_chain(cid: str, admin=Depends(crm_actor)):
    with Session() as db:
        row = must(db.get(ActionChain, cid))
        project_access(db, admin, row.project_id)
        return encode(row)


@router.patch("/action-chains/{cid}")
def patch_chain(cid: str, body: ChainPatch, admin=Depends(crm_actor)):
    try:
        with Session.begin() as db:
            row = must(db.scalar(select(ActionChain).where(ActionChain.id == cid).with_for_update()))
            project_access(db, admin, row.project_id)
            if row.revision != body.revision:
                raise HTTPException(409, "Цепочка изменена — обновите страницу")
            if body.name is not None:
                row.name = body.name.strip()
            if body.description is not None:
                row.description = body.description.strip()
            if body.branch_id is not None:
                if body.branch_id == "":
                    row.branch_id = None
                else:
                    br = must(db.get(Branch, body.branch_id))
                    if br.project_id != row.project_id:
                        raise HTTPException(422, "Ветка другого проекта")
                    row.branch_id = br.id
            if body.course_id is not None:
                row.course_id = body.course_id.strip() or None
            if body.nodes is not None:
                try:
                    row.nodes = validate_action_nodes(body.nodes)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            row.revision += 1
            row.updated_at = now()
            row.actor_id = admin.telegram_id
            if row.state == "published":
                row.state = "draft"  # edits unpublish until explicit publish
                row.published_at = None
            # CRM-R5: every Save promotes this chain as Project/Branch base.
            promote_action_chain_as_base(db, row)
            audit(
                db,
                admin,
                "action_chain.updated",
                project_id=row.project_id,
                chain_id=cid,
                is_base=True,
                branch_id=row.branch_id,
            )
            return encode(row)
    except IntegrityError as exc:
        raise HTTPException(409, "Базовая цепочка уже меняется — повторите сохранение") from exc


@router.post("/action-chains/{cid}/publish")
def publish_chain(cid: str, admin=Depends(crm_actor)):
    """Persist → Funnel draft/publish so ActionChain becomes runtime base layer."""
    with Session.begin() as db:
        row = must(db.scalar(select(ActionChain).where(ActionChain.id == cid).with_for_update()))
        project_access(db, admin, row.project_id)
        nodes = row.nodes if isinstance(row.nodes, list) else []
        if not nodes:
            raise HTTPException(422, "Добавьте хотя бы один шаг")
        try:
            graph = chain_to_funnel_graph(nodes)
            validate_graph(graph)
        except ValueError as exc:
            raise HTTPException(422, f"Граф: {exc}") from exc
        if row.funnel_id:
            funnel = must(db.scalar(select(Funnel).where(Funnel.id == row.funnel_id).with_for_update()))
            funnel.draft = graph
            funnel.name = row.name
            funnel.project_id = row.project_id
            funnel.revision += 1
        else:
            funnel = Funnel(
                id=uid(),
                name=row.name,
                project_id=row.project_id,
                connection_ids=[],
                draft=graph,
                revision=1,
            )
            db.add(funnel)
            db.flush()
            row.funnel_id = funnel.id
        version = Version(id=uid(), funnel_id=funnel.id, graph=graph, created_at=now())
        db.add(version)
        db.flush()
        funnel.published_version_id = version.id
        row.state = "published"
        row.published_at = now()
        row.updated_at = now()
        row.revision += 1
        audit(db, admin, "action_chain.published", project_id=row.project_id, chain_id=cid, funnel_id=funnel.id)
        return encode(row)


def _reason_ru(code: str, detail: str | None = None) -> str:
    """Staff-facing fail text for Constructor «Проверить»."""
    known = {
        "smtp_or_address_missing": "Нет SMTP: добавьте ящик Mailu в Подключениях или задайте SMTP_HOST/FROM",
        "bot_or_chat_missing": "Нет бота Telegram в Подключениях или не указан получатель",
        "vault_inactive": "Подключение выключено — включите его в Подключениях",
        "vault_secret_missing": "В подключении нет секрета — откройте Подключения и сохраните пароль/токен",
        "recipient_missing": "Укажите адрес получателя",
        "smtp_port_invalid": "Некорректный SMTP порт в Подключениях",
        "starttls_failed": "SMTP не поднял STARTTLS — пароль не отправляем по открытому каналу",
    }
    base = known.get(code) or code
    if detail:
        return f"{base}: {detail}"[:280]
    return base[:280]


def _parse_smtp_port(raw: str | int | None, *, default: int = 587) -> tuple[int | None, str | None]:
    """Return (port, error_code). Non-numeric / out-of-range → structured fail, not 500."""
    if raw is None or str(raw).strip() == "":
        return default, None
    text = str(raw).strip()
    try:
        port = int(text)
    except (TypeError, ValueError):
        return None, "smtp_port_invalid"
    if port < 1 or port > 65535:
        return None, "smtp_port_invalid"
    return port, None


def _active_vault(db, project_id: str, vault_type: str):
    from app.crm_models import ConnectionVault

    return list(
        db.scalars(
            select(ConnectionVault)
            .where(
                ConnectionVault.project_id == project_id,
                ConnectionVault.type == vault_type,
                ConnectionVault.active.is_(True),
            )
            .order_by(ConnectionVault.updated_at.desc(), ConnectionVault.id)
        )
    )


def _pick_email_vault(db, project_id: str, node: dict | None):
    """Resolve Mailu/SMTP vault for a node (from_vault_id → from_address → first active)."""
    from app.crm_models import ConnectionVault

    cfg = (node or {}).get("config") if isinstance(node, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    vault_id = str(cfg.get("from_vault_id") or cfg.get("vault_id") or "").strip()
    from_addr = str((node or {}).get("from_address") or "").strip() if node else ""
    rows = _active_vault(db, project_id, "email_smtp")
    if vault_id:
        row = db.get(ConnectionVault, vault_id)
        if row and row.project_id == project_id and row.type == "email_smtp":
            if not row.active:
                return None, "vault_inactive"
            return row, None
        return None, "smtp_or_address_missing"
    if from_addr:
        for row in rows:
            meta = row.meta if isinstance(row.meta, dict) else {}
            if str(meta.get("from_address") or "").strip().lower() == from_addr.lower():
                return row, None
    if rows:
        return rows[0], None
    return None, None


def _pick_telegram_vault(db, project_id: str, node: dict | None):
    from app.crm_models import ConnectionVault

    cfg = (node or {}).get("config") if isinstance(node, dict) else {}
    if not isinstance(cfg, dict):
        cfg = {}
    vault_id = str(cfg.get("vault_id") or "").strip()
    rows = _active_vault(db, project_id, "telegram_bot")
    if vault_id:
        row = db.get(ConnectionVault, vault_id)
        if row and row.project_id == project_id and row.type == "telegram_bot":
            if not row.active:
                return None, "vault_inactive"
            return row, None
        return None, "bot_or_chat_missing"
    if rows:
        return rows[0], None
    return None, None


def _email_transport(db, project_id: str, node: dict | None) -> dict[str, Any]:
    """Build SMTP transport from vault (Mailu) with env SMTP fallback."""
    from app import connection_vault as cv
    from app import mailu_client

    row, err = _pick_email_vault(db, project_id, node)
    if err:
        return {"ok": False, "reason": err}
    if row is not None:
        meta = row.meta if isinstance(row.meta, dict) else {}
        secrets = cv.reveal_secrets(row)
        host = str(meta.get("smtp_host") or "").strip()
        port_raw = meta.get("smtp_port")
        user = str(meta.get("smtp_user") or meta.get("from_address") or "").strip()
        password = str(secrets.get("smtp_password") or "").strip()
        frm = str(
            (node or {}).get("from_address")
            or meta.get("from_address")
            or user
            or ""
        ).strip()
        if not host:
            host, default_port = mailu_client.smtp_defaults()
            if port_raw is None or str(port_raw).strip() == "":
                port_raw = default_port
        if not host or not frm:
            return {"ok": False, "reason": "smtp_or_address_missing"}
        if not password:
            return {"ok": False, "reason": "vault_secret_missing", "vault_id": row.id}
        port, port_err = _parse_smtp_port(port_raw)
        if port_err:
            return {"ok": False, "reason": port_err, "vault_id": row.id}
        return {
            "ok": True,
            "host": host,
            "port": port,
            "user": user or frm,
            "password": password,
            "from": frm,
            "vault_id": row.id,
            "source": "vault",
            "require_starttls": True,
        }
    # Legacy env SMTP (pre-vault / dual-read).
    host = (os.getenv("SMTP_HOST") or os.getenv("MAILU_SMTP_HOST") or "").strip()
    frm = str((node or {}).get("from_address") or os.getenv("SMTP_FROM") or "").strip()
    user = (os.getenv("SMTP_USER") or "").strip()
    password = (os.getenv("SMTP_PASSWORD") or "").strip()
    port, port_err = _parse_smtp_port(os.getenv("SMTP_PORT") or os.getenv("MAILU_SMTP_PORT") or "587")
    if port_err:
        return {"ok": False, "reason": port_err}
    if not host or not frm:
        return {"ok": False, "reason": "smtp_or_address_missing"}
    return {
        "ok": True,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from": frm,
        "vault_id": None,
        "source": "env",
        "require_starttls": bool(user and password),
    }


def _telegram_token(db, project_id: str, node: dict | None) -> dict[str, Any]:
    from app import connection_vault as cv

    row, err = _pick_telegram_vault(db, project_id, node)
    if err:
        return {"ok": False, "reason": err}
    if row is not None:
        secrets = cv.reveal_secrets(row)
        token = str(secrets.get("bot_token") or "").strip()
        if not token:
            return {"ok": False, "reason": "vault_secret_missing", "vault_id": row.id}
        return {"ok": True, "token": token, "vault_id": row.id, "source": "vault"}
    token = (os.getenv("ADMIN_BOT_TOKEN") or os.getenv("TG_MAIN_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "reason": "bot_or_chat_missing"}
    return {"ok": True, "token": token, "vault_id": None, "source": "env"}


def _dry_run_email(
    to_addr: str,
    subject: str,
    body: str,
    transport: dict[str, Any],
) -> dict:
    preview = body[:500]
    if not (to_addr or "").strip():
        return {
            "ok": False,
            "channel": "email",
            "reason": "recipient_missing",
            "message": _reason_ru("recipient_missing"),
            "preview": preview,
        }
    if not transport.get("ok"):
        code = str(transport.get("reason") or "smtp_or_address_missing")
        return {
            "ok": False,
            "channel": "email",
            "reason": code,
            "message": _reason_ru(code),
            "preview": preview,
            "vault_id": transport.get("vault_id"),
        }
    frm = str(transport["from"])
    host = str(transport["host"])
    port = int(transport["port"])
    user = str(transport.get("user") or "")
    password = str(transport.get("password") or "")
    require_tls = bool(transport.get("require_starttls") or (user and password))
    msg = EmailMessage()
    msg["Subject"] = f"[Проверка] {subject}"
    msg["From"] = frm
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
                smtp.ehlo()
            except smtplib.SMTPException as tls_exc:
                if require_tls:
                    detail = str(tls_exc)[:160]
                    return {
                        "ok": False,
                        "channel": "email",
                        "reason": "starttls_failed",
                        "message": _reason_ru("starttls_failed", detail),
                        "preview": preview,
                        "vault_id": transport.get("vault_id"),
                        "source": transport.get("source"),
                    }
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return {
            "ok": True,
            "channel": "email",
            "to": to_addr,
            "from": frm,
            "message": f"Письмо отправлено на {to_addr}",
            "vault_id": transport.get("vault_id"),
            "source": transport.get("source"),
        }
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)[:200]
        return {
            "ok": False,
            "channel": "email",
            "reason": detail,
            "message": f"SMTP ошибка: {detail}",
            "preview": preview,
            "vault_id": transport.get("vault_id"),
            "source": transport.get("source"),
        }


def _dry_run_telegram(to: str, text: str, token_info: dict[str, Any]) -> dict:
    preview = text[:500]
    chat_raw = (to or "").strip()
    if not chat_raw:
        return {
            "ok": False,
            "channel": "telegram",
            "reason": "recipient_missing",
            "message": _reason_ru("recipient_missing"),
            "preview": preview,
        }
    if not token_info.get("ok"):
        code = str(token_info.get("reason") or "bot_or_chat_missing")
        return {
            "ok": False,
            "channel": "telegram",
            "reason": code,
            "message": _reason_ru(code),
            "preview": preview,
            "vault_id": token_info.get("vault_id"),
        }
    token = str(token_info["token"])
    chat = chat_raw.lstrip("@")
    # Numeric chat id preferred; username-only may fail without prior dialog — still attempt.
    try:
        import httpx

        payload: dict[str, Any] = {"text": f"[Проверка CRM]\n{text}"[:3500]}
        if chat.isdigit() or (chat.startswith("-") and chat[1:].isdigit()):
            payload["chat_id"] = int(chat)
        else:
            payload["chat_id"] = chat if chat.startswith("@") else f"@{chat}"
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
        if r.is_success:
            return {
                "ok": True,
                "channel": "telegram",
                "status": r.status_code,
                "to": str(payload["chat_id"]),
                "message": f"Telegram доставлено → {payload['chat_id']}",
                "vault_id": token_info.get("vault_id"),
                "source": token_info.get("source"),
            }
        detail = ""
        try:
            detail = str((r.json() or {}).get("description") or "")[:160]
        except Exception:  # noqa: BLE001
            detail = (r.text or "")[:160]
        reason = detail or f"http_{r.status_code}"
        return {
            "ok": False,
            "channel": "telegram",
            "status": r.status_code,
            "reason": reason,
            "message": f"Telegram не принял: {reason}",
            "to": str(payload["chat_id"]),
            "preview": preview,
            "vault_id": token_info.get("vault_id"),
            "source": token_info.get("source"),
        }
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)[:200]
        return {
            "ok": False,
            "channel": "telegram",
            "reason": detail,
            "message": f"Telegram ошибка: {detail}",
            "preview": preview,
            "vault_id": token_info.get("vault_id"),
            "source": token_info.get("source"),
        }


def _summarize_dry_run(steps: list[dict], *, email: str, telegram: str) -> dict[str, Any]:
    send_channels = {"email", "telegram"}
    deliveries = [
        d
        for step in steps
        for d in (step.get("deliveries") or [])
        if isinstance(d, dict) and d.get("channel") in send_channels
    ]
    ok_n = sum(1 for d in deliveries if d.get("ok"))
    fail_n = sum(1 for d in deliveries if not d.get("ok"))
    if not deliveries:
        status = "fail"
        ok = False
        message = "Нет шагов для отправки email/Telegram — добавьте письмо или Telegram на холсте"
    elif fail_n == 0:
        status = "ok"
        ok = True
        parts = []
        if email:
            parts.append(f"email → {email}")
        if telegram:
            parts.append(f"Telegram → {telegram}")
        message = "Доставлено: " + (" · ".join(parts) if parts else f"{ok_n} канал(ов)")
    elif ok_n == 0:
        status = "fail"
        ok = False
        first = next((d.get("message") or d.get("reason") for d in deliveries if not d.get("ok")), "ошибка")
        message = f"Не доставлено: {first}"
    else:
        status = "partial"
        ok = False
        message = f"Частично: ок {ok_n}, ошибок {fail_n}"
    return {
        "ok": ok,
        "status": status,
        "status_message": message,
        "deliveries_ok": ok_n,
        "deliveries_fail": fail_n,
    }


@router.post("/action-chains/{cid}/dry-run")
def dry_run_chain(cid: str, body: DryRunBody, admin=Depends(crm_actor)):
    """CRM-R4: really deliver test email/TG via vault Mailu SMTP + vault bot (env fallback)."""
    email = (body.email or "").strip()
    telegram = (body.telegram or "").strip()
    if not email and not telegram:
        raise HTTPException(422, "Укажите email и/или Telegram для проверки")
    with Session.begin() as db:
        row = must(db.get(ActionChain, cid))
        project_access(db, admin, row.project_id)
        nodes = row.nodes if isinstance(row.nodes, list) else []
        steps: list[dict[str, Any]] = []
        email_attempted = False
        telegram_attempted = False
        for node in nodes:
            kind = node.get("kind")
            cfg = node.get("config") or {}
            if not isinstance(cfg, dict):
                cfg = {}
            title = node.get("title") or kind
            text = str(cfg.get("body") or cfg.get("text") or cfg.get("question") or title)
            step: dict[str, Any] = {"id": node.get("id"), "kind": kind, "title": title, "deliveries": []}
            if kind in {"email", "contact"} and email:
                transport = _email_transport(db, row.project_id, node)
                step["deliveries"].append(_dry_run_email(email, str(title), text, transport))
                email_attempted = True
            if kind in {"telegram", "contact"} and telegram:
                token_info = _telegram_token(db, row.project_id, node)
                step["deliveries"].append(_dry_run_telegram(telegram, text, token_info))
                telegram_attempted = True
            if kind == "payment":
                step["deliveries"].append(
                    {
                        "ok": True,
                        "channel": "payment_preview",
                        "provider": cfg.get("provider") or "robokassa",
                        "mode": cfg.get("tariff_mode") or "fixed",
                        "note": "Dry-run: ссылка на оплату не создавалась",
                        "message": "Превью оплаты (ссылка не создавалась)",
                    }
                )
            if kind in {"survey", "code", "wait", "finish"}:
                step["deliveries"].append(
                    {
                        "ok": True,
                        "channel": "local",
                        "note": f"Шаг «{kind}» — без отправки",
                        "message": f"Шаг «{kind}» — без отправки",
                    }
                )
            steps.append(step)
        # Always attempt delivery to entered addresses (even if canvas has no matching kind).
        if email and not email_attempted:
            transport = _email_transport(db, row.project_id, None)
            steps.append(
                {
                    "id": "probe-email",
                    "kind": "email",
                    "title": "Проверка email",
                    "deliveries": [
                        _dry_run_email(
                            email,
                            "Проверка цепочки",
                            "Тестовое письмо из CRM «Проверить».",
                            transport,
                        )
                    ],
                }
            )
        if telegram and not telegram_attempted:
            token_info = _telegram_token(db, row.project_id, None)
            steps.append(
                {
                    "id": "probe-telegram",
                    "kind": "telegram",
                    "title": "Проверка Telegram",
                    "deliveries": [
                        _dry_run_telegram(
                            telegram,
                            "Тестовое сообщение из CRM «Проверить».",
                            token_info,
                        )
                    ],
                }
            )
        summary = _summarize_dry_run(steps, email=email, telegram=telegram)
        report = {
            "steps": steps,
            "email": email,
            "telegram": telegram,
            "deliveries_ok": summary["deliveries_ok"],
            "deliveries_fail": summary["deliveries_fail"],
            "status": summary["status"],
            "status_message": summary["status_message"],
        }
        audit_row = ActionChainDryRun(
            id=uid(),
            chain_id=cid,
            actor_id=admin.telegram_id,
            email=email,
            telegram=telegram,
            report=report,
            created_at=now(),
        )
        db.add(audit_row)
        audit(
            db,
            admin,
            "action_chain.dry_run",
            project_id=row.project_id,
            chain_id=cid,
            ok=summary["ok"],
            status=summary["status"],
        )
        return {
            "id": audit_row.id,
            "ok": summary["ok"],
            "status": summary["status"],
            "status_message": summary["status_message"],
            "report": report,
        }
