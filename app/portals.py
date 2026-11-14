"""Staff portal hostnames and CRM chrome helpers (crm.solo-pr.ru aware)."""
from __future__ import annotations

import os


PORTAL_HOSTS = {
    "public": "solo-pr.ru",
    "cms": "cms.solo-pr.ru",
    "crm": "crm.solo-pr.ru",
    "voronka": "voronka.solo-pr.ru",
    "lms": "lms.solo-pr.ru",
}

PORTAL_URLS = {
    "public": "https://solo-pr.ru",
    "cms": "https://cms.solo-pr.ru",
    "crm": "https://crm.solo-pr.ru",
    "voronka": "https://voronka.solo-pr.ru",
    "lms": "https://lms.solo-pr.ru",
}

MAILU_WEBMAIL_URL = os.getenv("MAILU_WEBMAIL_URL", "https://mail.vodopetov.ru/webmail/")


def normalize_host(host: str | None) -> str:
    return (
        str(host or "")
        .split(":")[0]
        .strip()
        .lower()
        .rstrip(".")
    )


def is_crm_host(host: str | None) -> bool:
    h = normalize_host(host)
    if not h:
        return False
    return h == PORTAL_HOSTS["crm"] or h.startswith("crm.")


def is_voronka_ops_host(host: str | None) -> bool:
    h = normalize_host(host)
    if not h:
        return False
    return h == PORTAL_HOSTS["voronka"] or h.startswith("voronka.")


def crm_public_base() -> str:
    return (os.getenv("CRM_PUBLIC_URL") or PORTAL_URLS["crm"]).rstrip("/")


def voronka_public_base() -> str:
    return (os.getenv("VORONKA_PUBLIC_URL") or PORTAL_URLS["voronka"]).rstrip("/")


def portal_chrome(role: str | None = None) -> list[dict]:
    """Cross-links for CRM chrome. Zone ACL SoR is CMS; CRM shows links for staff with CRM access."""
    # Managers stay in CRM money/clients; still can open CMS matrix (read) and LMS RO when ready.
    items = [
        {"id": "cms", "label": "CMS", "url": PORTAL_URLS["cms"] + "/admin", "zones": ["owner", "editor", "manager"]},
        {"id": "crm", "label": "CRM", "url": PORTAL_URLS["crm"] + "/", "zones": ["owner", "editor", "manager"]},
        {"id": "voronka", "label": "Voronka", "url": PORTAL_URLS["voronka"] + "/ops/", "zones": ["owner", "editor"]},
        {"id": "lms", "label": "LMS", "url": PORTAL_URLS["lms"] + "/", "zones": ["owner", "editor", "manager"]},
    ]
    if role == "manager":
        return [i for i in items if role in i["zones"]]
    return items


def robokassa_status() -> dict:
    login = bool(os.getenv("ROBOKASSA_LOGIN"))
    test = os.getenv("ROBOKASSA_TEST", "1") == "1"
    test_pw = bool(os.getenv("ROBOKASSA_TEST_PASSWORD1") and os.getenv("ROBOKASSA_TEST_PASSWORD2"))
    live_pw = bool(os.getenv("ROBOKASSA_PASSWORD1") and os.getenv("ROBOKASSA_PASSWORD2"))
    live_ready_flag = os.getenv("ROBOKASSA_LIVE_READY", "0") == "1"
    passwords_set = test_pw if test else live_pw
    ready = bool(login and passwords_set and (test or live_ready_flag))
    return {
        "provider": "robokassa",
        "login_set": login,
        "mode": "test" if test else "live",
        "test_passwords_set": test_pw,
        "live_passwords_set": live_pw,
        "passwords_set": passwords_set,
        "hash": os.getenv("ROBOKASSA_HASH", "sha256"),
        "live_ready": live_ready_flag,
        "ready": ready,
        "result_url": f"{crm_public_base()}/api/crm/robokassa/result",
        "receipt_in_adapter": False,
        "fiscalization_checklist": [
            {"id": "shop_mode", "label": "Магазин Robokassa в нужном режиме (тест/бой), пароли #1/#2 совпадают", "done": ready},
            {"id": "hash", "label": "Алгоритм хеша магазина совпадает с ROBOKASSA_HASH", "done": bool(os.getenv("ROBOKASSA_HASH", "sha256"))},
            {"id": "result_url", "label": "ResultURL указывает на /api/crm/robokassa/result и отвечает OK{InvId}", "done": False},
            {"id": "fiscal", "label": "Фискализация решена в схеме магазина (адаптер не передаёт Receipt)", "done": False},
            {"id": "live_gate", "label": "ROBOKASSA_LIVE_READY=1 только после фискализации и staging-платежа", "done": live_ready_flag and not test},
        ],
        "secrets_exposed": False,
    }


def yookassa_skeleton() -> dict:
    shop = bool(os.getenv("YOOKASSA_SHOP_ID"))
    secret = bool(os.getenv("YOOKASSA_SECRET_KEY"))
    webhook = bool(os.getenv("YOOKASSA_WEBHOOK_SECRET"))
    enabled = os.getenv("YOOKASSA_ENABLED", "0") == "1"
    return {
        "provider": "yookassa",
        "adapter": "not_implemented",
        "enabled": enabled,
        "env_names": {
            "YOOKASSA_ENABLED": "1 чтобы показать intent включения (adapter всё равно отсутствует)",
            "YOOKASSA_SHOP_ID": "идентификатор магазина",
            "YOOKASSA_SECRET_KEY": "секретный ключ",
            "YOOKASSA_WEBHOOK_SECRET": "подпись webhook",
        },
        "env_present": {"YOOKASSA_SHOP_ID": shop, "YOOKASSA_SECRET_KEY": secret, "YOOKASSA_WEBHOOK_SECRET": webhook},
        "webhook_url": f"{crm_public_base()}/api/crm/yookassa/webhook",
        "note": "UI-каркас этапа 2: adapter YooKassa не реализован; платежи идут через Robokassa.",
        "secrets_exposed": False,
    }


def mail_status() -> dict:
    host = bool(os.getenv("SMTP_HOST"))
    frm = bool(os.getenv("SMTP_FROM"))
    return {
        "webmail_url": MAILU_WEBMAIL_URL,
        "smtp_host_set": host,
        "smtp_from_set": frm,
        "smtp_configured": host and frm,
        "domain_note": "Mailu сейчас на vodopetov.ru; solo-pr.ru как Mailu-домен — отдельный ops-шаг, не блокер UI.",
        "secrets_exposed": False,
    }
