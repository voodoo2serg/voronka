"""Additive CRM schema. Existing funnel tables and identifiers stay intact."""
from datetime import datetime
from sqlalchemy import String, Integer, BigInteger, Boolean, DateTime, JSON, ForeignKey, Text, UniqueConstraint, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base, now


class Account(Base):
    __tablename__ = "crm_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"), unique=True)
    login: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(250))
    email: Mapped[str] = mapped_column(String(250), default="")
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Membership(Base):
    __tablename__ = "crm_memberships"
    __table_args__ = (UniqueConstraint("admin_id", "project_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))


class Profile(Base):
    __tablename__ = "crm_profiles"
    __table_args__ = (UniqueConstraint("contact_id", "project_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(250), default="")
    phone: Mapped[str] = mapped_column(String(80), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    source: Mapped[str] = mapped_column(String(200), default="manual")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    stopped: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Product(Base):
    __tablename__ = "crm_products"
    __table_args__ = (CheckConstraint("price_minor >= 0"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(Text, default="")
    price_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class Deal(Base):
    __tablename__ = "crm_deals"
    __table_args__ = (CheckConstraint("amount_minor >= 0"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("crm_products.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(250))
    stage: Mapped[str] = mapped_column(String(40), default="new")
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    next_action: Mapped[str] = mapped_column(String(500), default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Activity(Base):
    __tablename__ = "crm_activities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"))
    kind: Mapped[str] = mapped_column(String(40), default="note")
    channel: Mapped[str] = mapped_column(String(30), default="manual")
    outcome: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WorkItem(Base):
    __tablename__ = "crm_work_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"), index=True)
    deal_id: Mapped[str | None] = mapped_column(ForeignKey("crm_deals.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(40), default="task")
    state: Mapped[str] = mapped_column(String(30), default="open")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")


class ImportBatch(Base):
    __tablename__ = "crm_import_batches"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict] = mapped_column(JSON)
    rows: Mapped[list | None] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="preview")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Promo(Base):
    __tablename__ = "crm_promos"
    __table_args__ = (UniqueConstraint("project_id", "code"), CheckConstraint("percent > 0 AND percent <= 100"), CheckConstraint("max_uses > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    code: Mapped[str] = mapped_column(String(60))
    percent: Mapped[int] = mapped_column(Integer)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Order(Base):
    __tablename__ = "crm_orders"
    __table_args__ = (UniqueConstraint("project_id", "request_key"), CheckConstraint("total_minor >= 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("crm_products.id"))
    request_key: Mapped[str] = mapped_column(String(100))
    total_minor: Mapped[int] = mapped_column(BigInteger)
    snapshot: Mapped[dict] = mapped_column(JSON)
    installments: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(30), default="open")
    academic_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Payment(Base):
    __tablename__ = "crm_payments"
    __table_args__ = (UniqueConstraint("provider", "external_id"), CheckConstraint("amount_minor > 0"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(160))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(20), default="payment")
    evidence: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invoice(Base):
    __tablename__ = "crm_invoices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"))
    number: Mapped[str] = mapped_column(String(16), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    test: Mapped[bool] = mapped_column(Boolean)
    state: Mapped[str] = mapped_column(String(30), default="pending")


class AccessGrant(Base):
    __tablename__ = "crm_access_grants"
    __table_args__ = (UniqueConstraint("order_id", "resource", "capability"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"))
    resource: Mapped[str] = mapped_column(String(200))
    capability: Mapped[str] = mapped_column(String(40))
    min_paid_percent: Mapped[int] = mapped_column(Integer, default=100)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Campaign(Base):
    __tablename__ = "crm_campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    title: Mapped[str] = mapped_column(String(200))
    text: Mapped[str] = mapped_column(Text)
    profile_ids: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="draft")
    report: Mapped[dict] = mapped_column(JSON, default=dict)


class CampaignRecipient(Base):
    __tablename__ = "crm_campaign_recipients"
    __table_args__ = (UniqueConstraint("campaign_id", "profile_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("crm_campaigns.id"))
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"))
    outbox_id: Mapped[str] = mapped_column(ForeignKey("outbox.id"))


class Recovery(Base):
    __tablename__ = "crm_recovery"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("crm_accounts.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    purpose: Mapped[str] = mapped_column(String(30))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class OutreachBatch(Base):
    """Warm-up invite batches. Never routes through Campaign/Outbox/available_identity."""
    __tablename__ = "crm_outreach_batches"
    __table_args__ = (
        CheckConstraint("daily_cap >= 1 AND daily_cap <= 50"),
        CheckConstraint("mode IN ('dry_run', 'live')"),
        CheckConstraint("transport IN ('manual', 'pymax')"),
        CheckConstraint("state IN ('draft', 'prepared', 'paused', 'cancelled', 'completed')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    invite_text: Mapped[str] = mapped_column(Text)
    entry_token: Mapped[str] = mapped_column(ForeignKey("entries.token"))
    entry_connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    daily_cap: Mapped[int] = mapped_column(Integer, default=30)
    mode: Mapped[str] = mapped_column(String(20), default="dry_run")
    transport: Mapped[str] = mapped_column(String(20), default="manual")
    state: Mapped[str] = mapped_column(String(30), default="draft")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OutreachRecipient(Base):
    __tablename__ = "crm_outreach_recipients"
    __table_args__ = (
        UniqueConstraint("batch_id", "profile_id"),
        CheckConstraint(
            "state IN ('pending', 'prepared', 'invited', 'started', 'skipped', 'failed', 'stopped')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("crm_outreach_batches.id"), index=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("crm_profiles.id"), index=True)
    phone: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(30), default="pending")
    invite_text: Mapped[str] = mapped_column(Text, default="")
    bot_link: Mapped[str] = mapped_column(String(500), default="")
    entry_token: Mapped[str] = mapped_column(String(40), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    outcome_note: Mapped[str] = mapped_column(Text, default="")
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
