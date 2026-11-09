"""CRM catalog, orders, payments, Robokassa and access grants."""
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select

from app.crm_deps import Body, Session, crm_actor, encode, must, staff_owner, robokassa_ready
from app.crm_models import Product, Promo, Order, Payment, Invoice, AccessGrant, Profile
from app.crm_service import (
    uid, KINDS, project_access, get_profile, audit, balance, money_minor, manager_scope,
)
from app.db import now as db_now

router = APIRouter()


class ProductBody(Body):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(max_length=40)
    description: str = Field(default="", max_length=10000)
    price_minor: int = Field(default=0, ge=0, le=10**12)
    active: bool = True
    config: dict = Field(default_factory=dict)


@router.get("/projects/{pid}/products")
def products(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        return [encode(x) for x in db.scalars(select(Product).where(Product.project_id == pid))]


@router.post("/projects/{pid}/products")
def add_product(pid: str, body: ProductBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    if body.kind not in KINDS:
        raise HTTPException(422, "Неизвестный вид продукта")
    with Session.begin() as db:
        project_access(db, admin, pid)
        row = Product(id=uid(), project_id=pid, **body.model_dump())
        db.add(row); db.flush()
        audit(db, admin, "product.created", product_id=row.id)
        return encode(row)


@router.put("/products/{product_id}")
def update_product(product_id: str, body: ProductBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    if body.kind not in KINDS:
        raise HTTPException(422, "Неизвестный вид продукта")
    with Session.begin() as db:
        row = must(db.get(Product, product_id)); project_access(db, admin, row.project_id)
        for key, value in body.model_dump().items(): setattr(row, key, value)
        audit(db, admin, "product.updated", product_id=row.id)
        return encode(row)


@router.post("/projects/{pid}/catalog-template")
def catalog_template(pid: str, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        for kind, title in KINDS.items():
            if not db.scalar(select(Product).where(Product.project_id == pid, Product.kind == kind)):
                db.add(Product(id=uid(), project_id=pid, kind=kind, name=title, price_minor=0, active=False,
                               description="Стоимость и условия согласуются перед началом работы"))
        audit(db, admin, "catalog.template", project_id=pid)
    return {"ok": True}


class PromoBody(Body):
    code: str = Field(default="", max_length=60)
    percent: int = Field(ge=1, le=100)
    max_uses: int = Field(default=1, ge=1, le=100000)


@router.post("/projects/{pid}/promos")
def add_promo(pid: str, body: PromoBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        project_access(db, admin, pid)
        code = body.code.strip().upper() or secrets.token_hex(5).upper()
        if db.scalar(select(Promo).where(Promo.project_id == pid, Promo.code == code)):
            raise HTTPException(409, "Код уже существует")
        row = Promo(id=uid(), project_id=pid, code=code, percent=body.percent, max_uses=body.max_uses)
        db.add(row); db.flush()
        audit(db, admin, "promo.created", promo_id=row.id)
        return encode(row)


@router.get("/projects/{pid}/promos")
def promos(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        return [encode(x) for x in db.scalars(select(Promo).where(Promo.project_id == pid))]


class Installment(Body):
    due_at: datetime
    amount_minor: int = Field(gt=0, le=10**12)


class OrderBody(Body):
    profile_id: str
    product_id: str
    request_key: str = Field(min_length=8, max_length=100)
    promo_code: str = Field(default="", max_length=60)
    installments: list[Installment] = Field(default_factory=list, max_length=36)
    agreed_price_minor: int | None = Field(default=None, ge=0, le=10**12)
    price_reason: str = Field(default="", max_length=1000)


@router.post("/projects/{pid}/orders")
def add_order(pid: str, body: OrderBody, admin=Depends(crm_actor)):
    with Session.begin() as db:
        project_access(db, admin, pid)
        prior = db.scalar(select(Order).where(Order.project_id == pid, Order.request_key == body.request_key))
        if prior:
            if prior.profile_id != body.profile_id or prior.product_id != body.product_id:
                raise HTTPException(409, "Ключ уже использован для другого заказа")
            get_profile(db, admin, prior.profile_id)
            return {**encode(prior), **balance(db, prior)}
        if get_profile(db, admin, body.profile_id).project_id != pid:
            raise HTTPException(404, "Клиент недоступен")
        product = must(db.get(Product, body.product_id))
        if product.project_id != pid or not product.active:
            raise HTTPException(422, "Продукт не продаётся")
        total, discount = product.price_minor, 0
        if body.agreed_price_minor is not None:
            staff_owner(admin)
            if len(body.price_reason.strip()) < 5:
                raise HTTPException(422, "Укажите основание индивидуальной цены")
            total = body.agreed_price_minor
        base_price = total
        if body.promo_code:
            promo = db.scalar(select(Promo).where(Promo.project_id == pid, Promo.code == body.promo_code.strip().upper()).with_for_update())
            if not promo or not promo.active or promo.reserved >= promo.max_uses:
                raise HTTPException(409, "Промокод недоступен")
            discount = total * promo.percent // 100
            promo.reserved += 1
        total -= discount
        if body.installments and sum(x.amount_minor for x in body.installments) != total:
            raise HTTPException(422, "Сумма взносов должна совпадать с итогом заказа")
        if any(x.due_at.tzinfo is None for x in body.installments):
            raise HTTPException(422, "Укажите часовой пояс взноса")
        row = Order(id=uid(), project_id=pid, profile_id=body.profile_id, product_id=product.id, request_key=body.request_key,
                    total_minor=total, installments=[x.model_dump(mode="json") for x in sorted(body.installments, key=lambda x:x.due_at)],
                    snapshot={"name": product.name, "kind": product.kind, "price_minor": base_price, "catalog_price_minor": product.price_minor, "price_reason": body.price_reason, "currency": product.currency,
                              "discount_minor": discount, "promo": body.promo_code.strip().upper(), "config": product.config})
        db.add(row); db.flush()
        audit(db, admin, "order.created", order_id=row.id)
        return {**encode(row), **balance(db, row)}


@router.get("/projects/{pid}/orders")
def orders(pid: str, admin=Depends(crm_actor)):
    with Session() as db:
        project_access(db, admin, pid)
        query = select(Order).where(Order.project_id == pid)
        if manager_scope(admin):
            query = query.join(Profile, Profile.id == Order.profile_id).where(Profile.owner_id == admin.telegram_id)
        return [{**encode(o), **balance(db, o)} for o in db.scalars(query.order_by(Order.created_at.desc()))]


@router.post("/orders/{oid}/cancel")
def cancel_order(oid: str, admin=Depends(crm_actor)):
    with Session.begin() as db:
        row = must(db.scalar(select(Order).where(Order.id == oid).with_for_update()))
        project_access(db, admin, row.project_id)
        get_profile(db, admin, row.profile_id)
        if row.state == "cancelled": return {"ok": True}
        if balance(db, row)["paid_minor"]:
            raise HTTPException(409, "У оплаченного заказа сначала оформите возврат")
        row.state = "cancelled"
        if row.snapshot.get("promo"):
            promo = db.scalar(select(Promo).where(Promo.project_id == row.project_id, Promo.code == row.snapshot["promo"]).with_for_update())
            if promo: promo.reserved = max(promo.reserved - 1, 0)
        audit(db, admin, "order.cancelled", order_id=oid)
        return {"ok": True}


class MoneyBody(Body):
    amount_minor: int = Field(gt=0, le=10**12)
    external_id: str = Field(min_length=8, max_length=160)
    kind: Literal["payment", "refund"] = "payment"
    evidence: str = Field(min_length=5, max_length=2000)


@router.post("/orders/{oid}/payments")
def record_money(oid: str, body: MoneyBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        order = must(db.scalar(select(Order).where(Order.id == oid).with_for_update()))
        project_access(db, admin, order.project_id)
        prior = db.scalar(select(Payment).where(Payment.provider == "manual", Payment.external_id == body.external_id))
        if prior:
            if prior.order_id != oid or prior.amount_minor != body.amount_minor or prior.kind != body.kind:
                raise HTTPException(409, "Ключ операции уже использован")
        else:
            if body.kind == "refund" and body.amount_minor > balance(db, order)["paid_minor"]:
                raise HTTPException(422, "Возврат больше подтверждённой оплаты")
            db.add(Payment(id=uid(), order_id=oid, provider="manual", **body.model_dump()))
            db.flush()
            audit(db, admin, "money.recorded", order_id=oid, amount_minor=body.amount_minor, payment_kind=body.kind)
        return balance(db, order)


class GrantBody(Body):
    resource: str = Field(min_length=1, max_length=200)
    capability: Literal["course.enter", "video.play", "document.view", "document.download", "archive.view", "live.join", "certificate.issue"]
    min_paid_percent: int = Field(default=100, ge=0, le=100)
    expires_at: datetime | None = None


@router.post("/orders/{oid}/grants")
def grant_access(oid: str, body: GrantBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        order = must(db.get(Order, oid)); project_access(db, admin, order.project_id)
        prior = db.scalar(select(AccessGrant).where(AccessGrant.order_id == oid, AccessGrant.resource == body.resource, AccessGrant.capability == body.capability))
        if prior:
            raise HTTPException(409, "Такое назначение уже существует")
        if body.expires_at and body.expires_at.tzinfo is None:
            raise HTTPException(422, "Укажите часовой пояс")
        row = AccessGrant(id=uid(), order_id=oid, **body.model_dump())
        db.add(row); db.flush(); audit(db, admin, "grant.created", grant_id=row.id)
        return encode(row)


@router.get("/orders/{oid}/grants")
def list_grants(oid: str, admin=Depends(crm_actor)):
    """List CRM AccessGrant rows for an order (policy only — LMS enforcement is separate)."""
    with Session() as db:
        order = must(db.get(Order, oid)); project_access(db, admin, order.project_id)
        get_profile(db, admin, order.profile_id)
        return [encode(g) for g in db.scalars(select(AccessGrant).where(AccessGrant.order_id == oid))]


class AcademicBody(Body):
    complete: bool
    evidence: str = Field(min_length=5, max_length=2000)


@router.post("/orders/{oid}/academic")
def academic(oid: str, body: AcademicBody, admin=Depends(crm_actor)):
    staff_owner(admin)
    with Session.begin() as db:
        order = must(db.get(Order, oid)); project_access(db, admin, order.project_id)
        order.academic_complete = body.complete
        audit(db, admin, "academic.confirmed", order_id=oid, evidence=body.evidence, complete=body.complete)
    return {"ok": True}


@router.get("/orders/{oid}/access")
def access(oid: str, resource: str, capability: str, admin=Depends(crm_actor)):
    with Session() as db:
        order = must(db.get(Order, oid)); project_access(db, admin, order.project_id)
        get_profile(db, admin, order.profile_id)
        grant = db.scalar(select(AccessGrant).where(AccessGrant.order_id == oid, AccessGrant.resource == resource, AccessGrant.capability == capability))
        financial = balance(db, order)
        reasons = []
        if not grant: reasons.append("no_grant")
        if order.state != "open": reasons.append("order_inactive")
        if grant and grant.expires_at and grant.expires_at.replace(tzinfo=timezone.utc) <= db_now():
            reasons.append("expired")
        if grant and financial["paid_minor"] * 100 < order.total_minor * grant.min_paid_percent: reasons.append("payment_required")
        if capability == "certificate.issue":
            if not financial["fully_paid"]: reasons.append("full_payment_required")
            if not order.academic_complete: reasons.append("academic_completion_required")
        return {"allow": not reasons, "reasons": reasons, "grant": encode(grant) if grant else None, **financial}


@router.post("/orders/{oid}/robokassa")
def checkout(oid: str, amount_minor: int | None = None, admin=Depends(crm_actor)):
    test = os.getenv("ROBOKASSA_TEST", "1") == "1"
    merchant = os.getenv("ROBOKASSA_LOGIN")
    password = os.getenv("ROBOKASSA_TEST_PASSWORD1" if test else "ROBOKASSA_PASSWORD1")
    if not merchant or not password:
        raise HTTPException(503, "Robokassa не настроена")
    with Session.begin() as db:
        order = must(db.scalar(select(Order).where(Order.id == oid).with_for_update()))
        project_access(db, admin, order.project_id)
        get_profile(db, admin, order.profile_id)
        if order.state != "open":
            raise HTTPException(409, "Заказ закрыт")
        outstanding = balance(db, order)["balance_minor"]
        amount = outstanding if amount_minor is None else amount_minor
        if amount <= 0 or amount > outstanding:
            raise HTTPException(422, "Сумма должна быть в пределах остатка")
        if not test and os.getenv("ROBOKASSA_LIVE_READY") != "1":
            raise HTTPException(503, "Рабочая оплата отключена до настройки фискализации магазина")
        number = str(secrets.randbelow(10**14 - 1) + 1)
        row = Invoice(id=uid(), order_id=oid, number=number, amount_minor=amount, test=test)
        db.add(row)
        value = f"{amount // 100}.{amount % 100:02d}"
        algorithm = os.getenv("ROBOKASSA_HASH", "sha256").lower()
        if algorithm not in {"md5", "sha256", "sha512"}:
            raise HTTPException(503, "Неизвестный алгоритм Robokassa")
        signature = hashlib.new(algorithm, f"{merchant}:{value}:{number}:{password}".encode()).hexdigest()
        params = {"MerchantLogin": merchant, "OutSum": value, "InvId": number, "SignatureValue": signature,
                  "Description": order.snapshot["name"][:100], "IsTest": int(test)}
        return {"url": "https://auth.robokassa.ru/Merchant/Index.aspx?" + urlencode(params), "invoice_id": row.id, "test": test}


@router.api_route("/robokassa/result", methods=["GET", "POST"])
async def robokassa_result(request: Request):
    params = request.query_params if request.method == "GET" else await request.form()
    if any(len(params.getlist(k)) != 1 for k in ("OutSum", "InvId", "SignatureValue")):
        raise HTTPException(400, "Неоднозначные параметры")
    value, number, signature = (str(params[k]) for k in ("OutSum", "InvId", "SignatureValue"))
    if any(k.lower().startswith("shp_") for k in params):
        raise HTTPException(400, "Пользовательские параметры не поддержаны этим адаптером")
    algorithm = os.getenv("ROBOKASSA_HASH", "sha256").lower()
    if algorithm not in {"md5", "sha256", "sha512"}:
        raise HTTPException(503, "Неизвестный алгоритм Robokassa")
    amount = money_minor(value)
    with Session.begin() as db:
        invoice = must(db.scalar(select(Invoice).where(Invoice.number == number).with_for_update()))
        password = os.getenv("ROBOKASSA_TEST_PASSWORD2" if invoice.test else "ROBOKASSA_PASSWORD2")
        if not password:
            raise HTTPException(503, "Robokassa не настроена для режима счёта")
        expected = hashlib.new(algorithm, f"{value}:{number}:{password}".encode()).hexdigest()
        if not hmac.compare_digest(expected.lower(), signature.lower()):
            raise HTTPException(403, "Неверная подпись")
        if invoice.amount_minor != amount:
            raise HTTPException(400, "Сумма не совпадает")
        if invoice.state != "paid":
            if not invoice.test:
                db.add(Payment(id=uid(), order_id=invoice.order_id, provider="robokassa", external_id=number,
                               amount_minor=amount, evidence="verified ResultURL"))
            invoice.state = "paid"
    return Response("OK" + number, media_type="text/plain")
