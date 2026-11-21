"""FUNC-L1: CRM «Работа сегодня» sales contour aggregator.

One operator view: lead → touch → pay → access → course → mail,
with deep-links to Person / Signal / LMS when data exists.
Q6=B: never flips live Robokassa or production marketing.
"""
from __future__ import annotations

from sqlalchemy import select

from app import portals
from app.crm_models import (
    AccessGrant,
    Activity,
    Communication,
    Deal,
    Interaction,
    Order,
    Payment,
    Profile,
    WorkItem,
)
from app.crm_service import assigned_profile_filter, balance, manager_scope

CONTOUR = ("lead", "touch", "pay", "access", "course", "mail")

LABELS = {
    "lead": "Лид",
    "touch": "Касание",
    "pay": "Оплата",
    "access": "Доступ",
    "course": "Курс",
    "mail": "Письмо",
}


def _stage(done: bool, *, summary: str, href: str | None = None) -> dict:
    return {
        "done": bool(done),
        "summary": summary,
        "href": href,
    }


def work_today_for_project(db, admin, pid: str, *, limit: int = 40) -> dict:
    """Ranked operator queue with six-stage contour per profile."""
    profile_filter = assigned_profile_filter(admin)
    profiles = list(db.scalars(select(Profile).where(Profile.project_id == pid, profile_filter)))
    profile_by_id = {p.id: p for p in profiles}
    profile_ids = list(profile_by_id)

    deals: list[Deal] = []
    tasks: list[WorkItem] = []
    orders: list[Order] = []
    if profile_ids:
        if manager_scope(admin):
            deals = list(
                db.scalars(
                    select(Deal)
                    .join(Profile, Profile.id == Deal.profile_id)
                    .where(
                        Deal.project_id == pid,
                        (Deal.owner_id == admin.telegram_id) | (Profile.owner_id == admin.telegram_id),
                    )
                )
            )
        else:
            deals = list(db.scalars(select(Deal).where(Deal.project_id == pid)))
        tasks = list(
            db.scalars(select(WorkItem).where(WorkItem.profile_id.in_(profile_ids), WorkItem.state == "open"))
        )
        if manager_scope(admin):
            orders = list(
                db.scalars(
                    select(Order)
                    .join(Profile, Profile.id == Order.profile_id)
                    .where(Order.project_id == pid, Profile.owner_id == admin.telegram_id)
                )
            )
        else:
            orders = list(db.scalars(select(Order).where(Order.project_id == pid)))

    open_deals = [d for d in deals if d.stage not in ("won", "lost")]
    deals_by_profile: dict[str, list[Deal]] = {}
    for d in deals:
        deals_by_profile.setdefault(d.profile_id, []).append(d)
    tasks_by_profile: dict[str, list[WorkItem]] = {}
    for t in tasks:
        tasks_by_profile.setdefault(t.profile_id, []).append(t)
    orders_by_profile: dict[str, list[Order]] = {}
    for o in orders:
        orders_by_profile.setdefault(o.profile_id, []).append(o)

    order_ids = [o.id for o in orders]
    paid_order_ids: set[str] = set()
    grant_order_ids: set[str] = set()
    if order_ids:
        for pay in db.scalars(select(Payment).where(Payment.order_id.in_(order_ids), Payment.kind == "payment")):
            paid_order_ids.add(pay.order_id)
        for g in db.scalars(select(AccessGrant).where(AccessGrant.order_id.in_(order_ids))):
            grant_order_ids.add(g.order_id)

    activity_profiles: set[str] = set()
    if profile_ids:
        for aid in db.scalars(select(Activity.profile_id).where(Activity.profile_id.in_(profile_ids)).distinct()):
            activity_profiles.add(aid)

    person_ids = [p.person_id for p in profiles if p.person_id]
    mail_person_ids: set[str] = set()
    touch_person_ids: set[str] = set()
    if person_ids:
        for pid_row in db.scalars(
            select(Communication.person_id)
            .where(Communication.person_id.in_(person_ids), Communication.project_id == pid)
            .distinct()
        ):
            mail_person_ids.add(pid_row)
        for ix in db.scalars(
            select(Interaction).where(Interaction.person_id.in_(person_ids), Interaction.project_id == pid)
        ):
            touch_person_ids.add(ix.person_id)
            # Signal CE writeback / outbound mail evidence.
            if ix.communication_id or (ix.source_service or "") == "signal":
                mail_person_ids.add(ix.person_id)

    # Queue: open deals, open tasks, unpaid/partial orders, or paid-without-grant.
    queue_ids: set[str] = set()
    for d in open_deals:
        queue_ids.add(d.profile_id)
    for t in tasks:
        queue_ids.add(t.profile_id)
    for o in orders:
        bal = balance(db, o)
        if bal["balance_minor"] > 0 or o.id in paid_order_ids or o.id in grant_order_ids:
            queue_ids.add(o.profile_id)

    signal_base = portals.signal_public_base()
    lms_home = portals.lms_open_course_href()
    crm_contact = portals.PORTAL_URLS["crm"] + "/crm/"

    items: list[dict] = []
    for profile_id in queue_ids:
        profile = profile_by_id.get(profile_id)
        if not profile:
            continue
        p_deals = deals_by_profile.get(profile_id, [])
        open_p_deals = [d for d in p_deals if d.stage not in ("won", "lost")]
        primary_deal = open_p_deals[0] if open_p_deals else (p_deals[0] if p_deals else None)
        p_tasks = tasks_by_profile.get(profile_id, [])
        p_orders = orders_by_profile.get(profile_id, [])
        person_id = profile.person_id

        has_lead = bool(p_deals) or bool(profile)
        has_touch = (
            profile_id in activity_profiles
            or bool(p_tasks)
            or (person_id in touch_person_ids if person_id else False)
        )
        paid_any = any(o.id in paid_order_ids for o in p_orders)
        unpaid_any = any(balance(db, o)["balance_minor"] > 0 for o in p_orders)
        has_pay = paid_any
        has_access = any(o.id in grant_order_ids for o in p_orders)
        has_course = any(bool(o.academic_complete) for o in p_orders)
        has_mail = bool(person_id and person_id in mail_person_ids)

        signal_ce = portals.signal_href(view="campaigns", person=person_id) if person_id else portals.signal_href(view="campaigns")
        signal_sets = portals.signal_href(view="sets", person=person_id) if person_id else portals.signal_href(view="sets")
        person_href = f"{crm_contact}#person={profile_id}"
        lms_href = lms_home if has_access or has_course else None
        mail_href = signal_ce if has_mail or person_id else None

        statuses = {
            "lead": _stage(
                has_lead,
                summary=(
                    f"Сделка · {primary_deal.stage}"
                    if primary_deal
                    else "Клиент в CRM"
                ),
                href=person_href,
            ),
            "touch": _stage(
                has_touch,
                summary=(
                    p_tasks[0].title
                    if p_tasks
                    else ("Есть касание / Interaction" if has_touch else "Нет касания")
                ),
                href=person_href if has_touch else None,
            ),
            "pay": _stage(
                has_pay,
                summary=(
                    "Оплачено"
                    if paid_any
                    else ("Ожидает оплаты" if unpaid_any or (primary_deal and primary_deal.stage == "payment") else "Нет оплаты")
                ),
                href=person_href if (has_pay or unpaid_any or p_orders) else None,
            ),
            "access": _stage(
                has_access,
                summary="AccessGrant есть" if has_access else "Нет AccessGrant",
                href=lms_href if has_access else person_href if p_orders else None,
            ),
            "course": _stage(
                has_course,
                summary="Курс отмечен (academic)" if has_course else ("LMS после grant" if has_access else "Нет курса"),
                href=lms_href,
            ),
            "mail": _stage(
                has_mail,
                summary="Письмо / CE в истории" if has_mail else ("Person → SIGNAL" if person_id else "Нет Person"),
                href=mail_href,
            ),
        }

        # Next incomplete stage for operator attention.
        attention = "mail"
        for key in CONTOUR:
            if not statuses[key]["done"]:
                attention = key
                break

        items.append(
            {
                "profile_id": profile_id,
                "person_id": person_id,
                "name": profile.name,
                "deal_id": primary_deal.id if primary_deal else None,
                "deal_title": primary_deal.title if primary_deal else None,
                "deal_stage": primary_deal.stage if primary_deal else None,
                "open_task_title": p_tasks[0].title if p_tasks else None,
                "attention": attention,
                "statuses": statuses,
                "links": {
                    "person": person_href,
                    "signal_ce": signal_ce if person_id else None,
                    "signal_sets": signal_sets if person_id else None,
                    "lms": lms_href,
                },
            }
        )

    # Sort: earliest incomplete stage first, then name.
    rank = {k: i for i, k in enumerate(CONTOUR)}

    def _sort_key(row: dict):
        return (rank.get(row["attention"], 99), row["name"].lower())

    items.sort(key=_sort_key)
    items = items[: max(1, min(limit, 100))]

    return {
        "project_id": pid,
        "contour": list(CONTOUR),
        "labels": LABELS,
        "items": items,
        "counts": {
            "queue": len(items),
            "open_deals": len(open_deals),
            "open_tasks": len(tasks),
        },
        "links": {
            "signal": signal_base + "/",
            "signal_sets": portals.signal_href(view="sets"),
            "signal_campaigns": portals.signal_href(view="campaigns"),
            "lms": lms_home,
            "mailu": portals.MAILU_WEBMAIL_URL,
            "today": portals.PORTAL_URLS["crm"] + "/crm/#today",
        },
        "honesty": {
            "can_enable_live_from_ui": False,
            "policy": "Q11=C: production mail live; Robokassa sandbox — mass marketing off",
        },
        "secrets_exposed": False,
    }
