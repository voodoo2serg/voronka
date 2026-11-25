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


def lms_public_base() -> str:
    return (os.getenv("LMS_PUBLIC_URL") or PORTAL_URLS["lms"]).rstrip("/")


def lms_open_course_href() -> str:
    """FUNC-L4: CRM → LMS portal handoff «открыть курс» (MyCourses after SSO)."""
    return lms_public_base() + "/open"


def signal_public_base() -> str:
    return (os.getenv("SIGNAL_PUBLIC_URL") or "https://signal.vodopetov.ru").rstrip("/")


def signal_href(
    *,
    view: str | None = None,
    doc: str | None = None,
    person: str | None = None,
    brand: str | None = None,
    set_id: str | None = None,
) -> str:
    """Deep-link into Signal studio without pasting UUID into a form.

    FUNC-L3: CRM opens CE (campaigns/регламенты) or message-sets with optional
    person/brand/set query so operators never copy Person UUID by hand.
    Signal may ignore unknown params until it reads them — view is always valid.
    """
    q: list[str] = []
    if view:
        q.append(f"view={view}")
    if doc:
        q.append(f"doc={doc}")
    if person:
        q.append(f"person={person}")
    if brand:
        q.append(f"brand={brand}")
    if set_id:
        q.append(f"set={set_id}")
    base = signal_public_base() + "/"
    return base + (("?" + "&".join(q)) if q else "")


def signal_media_library_url(*, brand: str | None = None, person: str | None = None) -> str:
    """CRM-A15 canonical Медиатека deep-link (Signal PR #57). SSO preserves return_to."""
    override = (os.getenv("SIGNAL_MEDIA_DEEP_LINK") or "").strip()
    if override and not brand and not person:
        return override
    return signal_href(view="media", brand=brand, person=person)


def portal_chrome(role: str | None = None) -> list[dict]:
    """Cross-links for CRM chrome. Zone ACL SoR is CMS; CRM shows links for staff with CRM access."""
    # Managers stay in CRM money/clients; still can open CMS matrix (read) and LMS RO when ready.
    items = [
        {"id": "cms", "label": "CMS", "url": PORTAL_URLS["cms"] + "/admin", "zones": ["owner", "editor", "manager"]},
        {"id": "crm", "label": "CRM", "url": PORTAL_URLS["crm"] + "/", "zones": ["owner", "editor", "manager"]},
        {"id": "voronka", "label": "Боты / Ops", "url": PORTAL_URLS["voronka"] + "/ops/", "zones": ["owner", "editor"]},
        {"id": "lms", "label": "Открыть курс", "url": lms_open_course_href(), "zones": ["owner", "editor", "manager"]},
    ]
    if role == "manager":
        return [i for i in items if role in i["zones"]]
    return items


def robokassa_status() -> dict:
    from app import payment_vault
    from app import robokassa_receipt

    payment_vault.ensure_runtime(force=True)
    login = bool(os.getenv("ROBOKASSA_LOGIN"))
    test = os.getenv("ROBOKASSA_TEST", "1") == "1"
    test_pw = bool(os.getenv("ROBOKASSA_TEST_PASSWORD1") and os.getenv("ROBOKASSA_TEST_PASSWORD2"))
    live_pw = bool(os.getenv("ROBOKASSA_PASSWORD1") and os.getenv("ROBOKASSA_PASSWORD2"))
    live_ready_flag = os.getenv("ROBOKASSA_LIVE_READY", "0") == "1"
    passwords_set = test_pw if test else live_pw
    # ready = credentials for current mode; live still requires LIVE_READY (never flipped by this slice).
    ready = bool(login and passwords_set and (test or live_ready_flag))
    receipt_meta = robokassa_receipt.receipt_env_configured()
    base = crm_public_base()
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
        "result_url": f"{base}/api/crm/robokassa/result",
        "fail_url": f"{base}/api/crm/robokassa/fail",
        "receipt_in_adapter": True,
        "receipt_tax": receipt_meta["tax"],
        "receipt_sno": receipt_meta["sno"],
        "fiscalization_checklist": [
            {
                "id": "shop_mode",
                "label": "Магазин Robokassa в нужном режиме (тест/бой), пароли #1/#2 заданы для режима",
                "done": bool(login and passwords_set),
            },
            {
                "id": "hash",
                "label": "Алгоритм хеша магазина совпадает с ROBOKASSA_HASH",
                "done": (os.getenv("ROBOKASSA_HASH", "sha256").lower() in robokassa_receipt.ALLOWED_HASH),
            },
            {
                "id": "result_url",
                "label": "ResultURL указывает на /api/crm/robokassa/result и отвечает OK{InvId}",
                "done": False,
            },
            {
                "id": "receipt_payload",
                "label": "Адаптер передаёт параметр Receipt (номенклатура) в запросе оплаты и в подписи",
                "done": True,
            },
            {
                "id": "fiscal_shop",
                "label": "В ЛК Robokassa включена фискализация (облако / робочеки / касса) для этого магазина",
                "done": False,
            },
            {
                "id": "tax_env",
                "label": "ROBOKASSA_RECEIPT_TAX (и при необходимости SNO) согласованы с СНО продавца",
                "done": bool(receipt_meta["tax_valid"] and receipt_meta["sno_valid"] and receipt_meta["tax_env_set"]),
            },
            {
                "id": "staging_pay",
                "label": "Staging-платёж (ROBOKASSA_TEST=1) с чеком проверен в ЛК Robokassa",
                "done": False,
            },
            {
                "id": "owner_approval",
                "label": "Письменное решение владельца на live-деньги (после закрытия чек-листа)",
                "done": False,
            },
            {
                "id": "live_gate",
                "label": "ROBOKASSA_LIVE_READY=1 только после фискализации, staging и owner approval — не flip из кода",
                "done": live_ready_flag and not test,
            },
        ],
        "secrets_exposed": False,
    }


def yookassa_skeleton() -> dict:
    from app import payment_vault

    payment_vault.ensure_runtime(force=True)
    shop = bool(os.getenv("YOOKASSA_SHOP_ID"))
    secret = bool(os.getenv("YOOKASSA_SECRET_KEY"))
    webhook = bool(os.getenv("YOOKASSA_WEBHOOK_SECRET"))
    enabled = os.getenv("YOOKASSA_ENABLED", "0") == "1"
    # Sandbox OK when shop+secret present; live money still requires explicit owner flip later.
    ready = bool(shop and secret)
    return {
        "provider": "yookassa",
        "adapter": "webhook_v1",
        "enabled": enabled,
        "ready": ready,
        "mode": "sandbox" if not enabled else "intent_live",
        "env_names": {
            "YOOKASSA_ENABLED": "1 = intent включения (live всё равно gated владельцем)",
            "YOOKASSA_SHOP_ID": "идентификатор магазина",
            "YOOKASSA_SECRET_KEY": "секретный ключ",
            "YOOKASSA_WEBHOOK_SECRET": "подпись webhook (опционально)",
        },
        "env_present": {"YOOKASSA_SHOP_ID": shop, "YOOKASSA_SECRET_KEY": secret, "YOOKASSA_WEBHOOK_SECRET": webhook},
        "webhook_url": f"{crm_public_base()}/api/crm/yookassa/webhook",
        "note": "ЮKassa webhook принимает уведомления; боевые деньги — только после решения владельца (Q11 sandbox).",
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


def sales_runbook() -> dict:
    """Honest sandbox vs live gates for CRM HQ (FUNC-L2 + Q11=C). Never a flip control.

    Q11=C: production mail may be live; Robokassa stays sandbox. Mass marketing still OFF.
    UI only reports — never sets ROBOKASSA_LIVE_READY or Signal mass-send flags.
    """
    rk = robokassa_status()
    mail = mail_status()
    test_mode = rk["mode"] == "test"
    live_ready = bool(rk["live_ready"])
    # Company defaults + Signal mass-marketing path are intentionally never flipped here.
    production_marketing_on = False
    # Q11=C: transactional/consented mail may use production SMTP (Mailu) when configured.
    production_mail_on = bool(mail.get("smtp_configured"))
    signal_url = signal_public_base()
    return {
        "policy": "Q11=C: production mail live; Robokassa sandbox — mass marketing still off",
        "can_enable_live_from_ui": False,
        "gates": {
            "robokassa_live": {
                "id": "robokassa_live",
                "on": live_ready and not test_mode,
                "flag": "ROBOKASSA_LIVE_READY",
                "mode": rk["mode"],
                "live_ready": live_ready,
                "summary": (
                    "Боевой режим Robokassa (LIVE_READY=1)"
                    if live_ready and not test_mode
                    else "Robokassa live OFF — sandbox/test; LIVE_READY не включён из UI (Q11=C)"
                ),
            },
            "production_mail": {
                "id": "production_mail",
                "on": production_mail_on,
                "flag": "SMTP_HOST / SMTP_FROM (Mailu)",
                "summary": (
                    "Production mail ON — SMTP/Mailu настроен; consented/transactional send path live (Q11=C)"
                    if production_mail_on
                    else "Production mail — SMTP ещё не настроен (SMTP_HOST/FROM)"
                ),
            },
            "production_marketing": {
                "id": "production_marketing",
                "on": production_marketing_on,
                "flag": "SIGNAL_ALLOW_PRODUCTION_SEND / allow_production_send",
                "summary": "Mass production marketing OFF — не путать с live transactional mail (Q11=C)",
            },
            "sandbox_payments": {
                "id": "sandbox_payments",
                "on": test_mode or rk["ready"],
                "summary": (
                    "Sandbox payments OK (ROBOKASSA_TEST=1 / тестовые пароли)"
                    if test_mode
                    else "Текущий режим Robokassa: live credentials path (флаг live всё равно gated)"
                ),
            },
        },
        "next_steps": [
            "Sandbox pay: заказ → Payment → AccessGrant → LMS / Signal course.access_granted.",
            "Mail live (Q11=C): transactional / consented письма через Mailu/SMTP — не массовый marketing flip.",
            "Курс ученика — LMS / OpenOlat; касса в LMS отсутствует намеренно.",
            "Live Robokassa — только после фискализации + staging + письменного решения владельца (не из этого UI).",
        ],
        "links": {
            "payments": PORTAL_URLS["crm"] + "/crm/#payments",
            "signal": signal_url + "/",
            "signal_sets": signal_href(view="sets"),
            "signal_campaigns": signal_href(view="campaigns"),
            "lms": lms_open_course_href(),
            "ops": voronka_public_base() + "/ops/",
            "today": PORTAL_URLS["crm"] + "/crm/#today",
            "webmail": MAILU_WEBMAIL_URL,
        },
        "secrets_exposed": False,
    }
