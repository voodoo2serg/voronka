"""Action Constructor API: CRUD, dry-run «Проверить», publish → Funnel runtime."""
from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must
from app.crm_models import ActionChain, ActionChainDryRun, Branch
from app.crm_service import audit, project_access, uid
from app.db import Funnel, Version, now
from app.graph import validate_graph
from app.portals import signal_media_library_url, signal_public_base
from app.projects_domain import chain_to_funnel_graph, validate_action_nodes

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
    if project_id:
        from app import connection_vault as cv

        with Session() as db:
            project_access(db, admin, project_id)
            vault_email = cv.list_for_picker(db, project_id, types=["email_smtp"])
            vault_payment = cv.list_for_picker(db, project_id, types=["robokassa", "yookassa"])
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
    }


@router.get("/projects/{pid}/action-chains")
def list_chains(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        rows = list(
            db.scalars(
                select(ActionChain)
                .where(ActionChain.project_id == pid)
                .order_by(ActionChain.updated_at.desc(), ActionChain.id)
            )
        )
        return {"items": [encode(r) for r in rows]}


@router.post("/projects/{pid}/action-chains")
def create_chain(pid: str, body: ChainBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        if body.branch_id:
            br = must(db.get(Branch, body.branch_id))
            if br.project_id != pid:
                raise HTTPException(422, "Ветка другого проекта")
        try:
            nodes = validate_action_nodes(body.nodes)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = ActionChain(
            id=uid(),
            project_id=pid,
            branch_id=body.branch_id,
            course_id=(body.course_id or "").strip() or None,
            name=body.name.strip(),
            description=(body.description or "").strip(),
            state="draft",
            nodes=nodes,
            revision=1,
            actor_id=admin.telegram_id,
            created_at=now(),
            updated_at=now(),
        )
        db.add(row)
        audit(db, admin, "action_chain.created", project_id=pid, chain_id=row.id)
        return encode(row)


@router.get("/action-chains/{cid}")
def get_chain(cid: str, admin=Depends(crm_actor)):
    with Session() as db:
        row = must(db.get(ActionChain, cid))
        project_access(db, admin, row.project_id)
        return encode(row)


@router.patch("/action-chains/{cid}")
def patch_chain(cid: str, body: ChainPatch, admin=Depends(crm_actor)):
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
        audit(db, admin, "action_chain.updated", project_id=row.project_id, chain_id=cid)
        return encode(row)


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


def _dry_run_email(to_addr: str, subject: str, body: str, from_addr: str | None) -> dict:
    host = os.getenv("SMTP_HOST")
    frm = from_addr or os.getenv("SMTP_FROM") or ""
    if not host or not frm or not to_addr:
        return {"ok": False, "channel": "email", "reason": "smtp_or_address_missing", "preview": body[:500]}
    msg = EmailMessage()
    msg["Subject"] = f"[Проверка] {subject}"
    msg["From"] = frm
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT") or "587"), timeout=20) as smtp:
            if os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"):
                smtp.starttls()
                smtp.login(os.getenv("SMTP_USER") or "", os.getenv("SMTP_PASSWORD") or "")
            smtp.send_message(msg)
        return {"ok": True, "channel": "email", "to": to_addr, "from": frm}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "channel": "email", "reason": str(exc)[:200], "preview": body[:500]}


def _dry_run_telegram(to: str, text: str) -> dict:
    token = os.getenv("ADMIN_BOT_TOKEN") or os.getenv("TG_MAIN_TOKEN")
    chat = (to or "").strip().lstrip("@")
    if not token or not chat:
        return {"ok": False, "channel": "telegram", "reason": "bot_or_chat_missing", "preview": text[:500]}
    # Numeric chat id preferred; username-only may fail without prior dialog — still attempt.
    try:
        import httpx

        payload: dict[str, Any] = {"text": f"[Проверка CRM]\n{text}"[:3500]}
        if chat.isdigit() or (chat.startswith("-") and chat[1:].isdigit()):
            payload["chat_id"] = int(chat)
        else:
            payload["chat_id"] = chat if chat.startswith("@") else f"@{chat}"
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=15)
        return {"ok": r.is_success, "channel": "telegram", "status": r.status_code, "to": str(payload["chat_id"])}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "channel": "telegram", "reason": str(exc)[:200], "preview": text[:500]}


@router.post("/action-chains/{cid}/dry-run")
def dry_run_chain(cid: str, body: DryRunBody, admin=Depends(crm_actor)):
    email = (body.email or "").strip()
    telegram = (body.telegram or "").strip()
    if not email and not telegram:
        raise HTTPException(422, "Укажите email и/или Telegram для проверки")
    with Session.begin() as db:
        row = must(db.get(ActionChain, cid))
        project_access(db, admin, row.project_id)
        nodes = row.nodes if isinstance(row.nodes, list) else []
        steps = []
        for node in nodes:
            kind = node.get("kind")
            cfg = node.get("config") or {}
            title = node.get("title") or kind
            text = str(cfg.get("body") or cfg.get("text") or cfg.get("question") or title)
            step: dict[str, Any] = {"id": node.get("id"), "kind": kind, "title": title, "deliveries": []}
            if kind in {"email", "contact"} and email:
                step["deliveries"].append(
                    _dry_run_email(email, str(title), text, node.get("from_address"))
                )
            if kind in {"telegram", "contact"} and telegram:
                step["deliveries"].append(_dry_run_telegram(telegram, text))
            if kind == "payment":
                step["deliveries"].append(
                    {
                        "ok": True,
                        "channel": "payment_preview",
                        "provider": cfg.get("provider") or "robokassa",
                        "mode": cfg.get("tariff_mode") or "fixed",
                        "note": "Dry-run: ссылка на оплату не создавалась",
                    }
                )
            if kind in {"survey", "code", "wait", "finish"}:
                step["deliveries"].append({"ok": True, "channel": "local", "note": f"Шаг «{kind}» — без отправки"})
            steps.append(step)
        report = {"steps": steps, "email": email, "telegram": telegram}
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
        audit(db, admin, "action_chain.dry_run", project_id=row.project_id, chain_id=cid)
        return {"id": audit_row.id, "report": report}
