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
    person_id: Mapped[str | None] = mapped_column(ForeignKey("crm_persons.id"), nullable=True, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    person_role: Mapped[str] = mapped_column(String(30), default="lead")  # lead | client | alumni
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
    person_id: Mapped[str | None] = mapped_column(ForeignKey("crm_persons.id"), nullable=True)
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
    person_id: Mapped[str | None] = mapped_column(ForeignKey("crm_persons.id"), nullable=True)
    agreement_id: Mapped[str | None] = mapped_column(ForeignKey("crm_agreements.id"), nullable=True)
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
    asset_id: Mapped[str | None] = mapped_column(ForeignKey("assets.id"), nullable=True)
    profile_ids: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(30), default="draft")
    report: Mapped[dict] = mapped_column(JSON, default=dict)


class ProviderEvent(Base):
    """Auditable payment-provider notifications (payload redacted)."""
    __tablename__ = "crm_provider_events"
    __table_args__ = (UniqueConstraint("provider", "event_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    event_key: Mapped[str] = mapped_column(String(200))
    order_id: Mapped[str | None] = mapped_column(ForeignKey("crm_orders.id"), nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(ForeignKey("crm_invoices.id"), nullable=True)
    payload_redacted: Mapped[dict] = mapped_column(JSON, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(30), default="received")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EntitlementJob(Base):
    """Idempotent payment → AccessGrant / neighbor-event pipeline."""
    __tablename__ = "crm_entitlement_jobs"
    __table_args__ = (UniqueConstraint("payment_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("crm_payments.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"), index=True)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IntegrationOutbox(Base):
    """Signed outbound events to solo_pr / lms_end (no shared DB)."""
    __tablename__ = "crm_integration_outbox"
    __table_args__ = (UniqueConstraint("event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80))
    destination: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_error: Mapped[str] = mapped_column(String(200), default="")
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IntegrationInbox(Base):
    """Idempotent inbound events from solo_pr / lms_end."""
    __tablename__ = "crm_integration_inbox"
    __table_args__ = (UniqueConstraint("source", "event_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(40))
    event_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(60))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="applied")
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("crm_profiles.id"), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AiRule(Base):
    """AI sales-engine rules: global / project / connection override."""
    __tablename__ = "crm_ai_rules"
    __table_args__ = (
        UniqueConstraint("scope", "project_id", "connection_id"),
        CheckConstraint("scope IN ('global', 'project', 'connection')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(20))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(30), default="inherit")
    model: Mapped[str] = mapped_column(String(120), default="")
    system_rules: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)


class LlmReplyAudit(Base):
    """Minimal Voronka-only audit for llm_reply (no Phoenix). Draft stays local for inbox approve/edit/reject."""
    __tablename__ = "llm_reply_audits"
    __table_args__ = (
        CheckConstraint(
            "response_status IN ('ok', 'error', 'timeout', 'fallback', 'pending', 'approved', 'rejected', 'edited')"
        ),
        CheckConstraint(
            "mode IN ('draft_only', 'human_approval', 'auto_framed')",
            name="llm_reply_audits_mode_check",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), index=True)
    node_id: Mapped[str] = mapped_column(String(80))
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("runs.id"), index=True)
    identity_id: Mapped[str] = mapped_column(String(36), ForeignKey("identities.id"), index=True)
    contact_id: Mapped[str] = mapped_column(String(36), ForeignKey("contacts.id"), index=True)
    connection_id: Mapped[str] = mapped_column(String(36), ForeignKey("connections.id"))
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id"), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    rules_version: Mapped[str] = mapped_column(String(120), default="")
    mode: Mapped[str] = mapped_column(String(30), default="draft_only")
    response_status: Mapped[str] = mapped_column(String(30), default="pending")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("admins.telegram_id"), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("outbox.id"), nullable=True)
    draft_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


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


# --- Wave C: Person graph, consent, interactions, journeys, agreements, ledger, OIDC ---


class Person(Base):
    """Canonical natural person across channels (CRM-001). Not owned by Signal."""
    __tablename__ = "crm_persons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    lifecycle_stage: Mapped[str] = mapped_column(String(40), default="active")
    display_name: Mapped[str] = mapped_column(String(200), default="")
    merged_into_id: Mapped[str | None] = mapped_column(ForeignKey("crm_persons.id"), nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PersonLink(Base):
    __tablename__ = "crm_person_links"
    __table_args__ = (UniqueConstraint("service", "native_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    service: Mapped[str] = mapped_column(String(40))
    native_id: Mapped[str] = mapped_column(String(160))
    verified_by: Mapped[str] = mapped_column(String(40), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MergeReview(Base):
    __tablename__ = "crm_merge_reviews"
    __table_args__ = (CheckConstraint("state IN ('pending', 'applied', 'rejected', 'undone')"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"))
    target_person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"))
    state: Mapped[str] = mapped_column(String(30), default="pending")
    reason: Mapped[str] = mapped_column(String(500), default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContactPoint(Base):
    """Channel endpoint owned by a Person (CRM-005)."""
    __tablename__ = "crm_contact_points"
    __table_args__ = (UniqueConstraint("person_id", "channel", "address_hash"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    channel: Mapped[str] = mapped_column(String(30))
    raw_value: Mapped[str] = mapped_column(String(250), default="")
    normalized_value: Mapped[str] = mapped_column(String(250))
    address_hash: Mapped[str] = mapped_column(String(64), index=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    reachable: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    identity_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ContactVerification(Base):
    __tablename__ = "crm_contact_verifications"
    __table_args__ = (CheckConstraint("outcome IN ('verified', 'rejected', 'expired')"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_point_id: Mapped[str] = mapped_column(ForeignKey("crm_contact_points.id"), index=True)
    method: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[str] = mapped_column(String(500), default="")
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Consent(Base):
    """Messaging permission by contact point + purpose (CRM-003)."""
    __tablename__ = "crm_consents"
    __table_args__ = (
        UniqueConstraint("contact_point_id", "purpose"),
        CheckConstraint("status IN ('granted', 'denied', 'revoked', 'unknown')"),
        CheckConstraint("purpose IN ('marketing', 'transactional', 'service', 'analytics')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_point_id: Mapped[str] = mapped_column(ForeignKey("crm_contact_points.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))
    lawful_basis: Mapped[str] = mapped_column(String(40), default="consent")
    status: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[str] = mapped_column(String(500), default="")
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Interaction(Base):
    """Normalized touch journal entry (CRM-006)."""
    __tablename__ = "crm_interactions"
    __table_args__ = (
        UniqueConstraint("source_service", "external_event_key"),
        CheckConstraint("direction IN ('inbound', 'outbound', 'system')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    contact_point_id: Mapped[str | None] = mapped_column(ForeignKey("crm_contact_points.id"), nullable=True)
    communication_id: Mapped[str | None] = mapped_column(ForeignKey("crm_communications.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    direction: Mapped[str] = mapped_column(String(20), default="inbound")
    channel: Mapped[str] = mapped_column(String(30), default="")
    evidence_status: Mapped[str] = mapped_column(String(30), default="recorded")
    source_service: Mapped[str] = mapped_column(String(40), default="voronka")
    external_event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Communication(Base):
    __tablename__ = "crm_communications"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'accepted', 'delivered', 'failed', 'partial', 'cancelled')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    contact_point_id: Mapped[str | None] = mapped_column(ForeignKey("crm_contact_points.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("crm_campaigns.id"), nullable=True)
    journey_step_id: Mapped[str | None] = mapped_column(
        ForeignKey("crm_journey_steps.id", use_alter=True, name="fk_crm_communications_journey_step"),
        nullable=True,
    )
    outbox_id: Mapped[str | None] = mapped_column(ForeignKey("outbox.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(20), default="outbound")
    purpose: Mapped[str] = mapped_column(String(40), default="marketing")
    channel: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(30), default="queued")
    provider_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    body_preview: Mapped[str] = mapped_column(String(500), default="")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DeliveryEvent(Base):
    __tablename__ = "crm_delivery_events"
    __table_args__ = (
        UniqueConstraint("communication_id", "external_event_key"),
        CheckConstraint(
            "normalized_status IN ('queued', 'accepted', 'delivered', 'failed', 'read', 'unknown')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    communication_id: Mapped[str] = mapped_column(ForeignKey("crm_communications.id"), index=True)
    platform_status: Mapped[str] = mapped_column(String(80), default="")
    normalized_status: Mapped[str] = mapped_column(String(30))
    evidence_ref: Mapped[str] = mapped_column(String(200), default="")
    external_event_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Reply(Base):
    __tablename__ = "crm_replies"
    __table_args__ = (UniqueConstraint("communication_id", "provider_message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    communication_id: Mapped[str] = mapped_column(ForeignKey("crm_communications.id"))
    provider_message_id: Mapped[str] = mapped_column(String(160), default="")
    kind: Mapped[str] = mapped_column(String(40), default="message")
    body_preview: Mapped[str] = mapped_column(String(500), default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Journey(Base):
    """Warm-up / sequenced touches (CRM-007)."""
    __tablename__ = "crm_journeys"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'paused', 'completed', 'cancelled', 'opted_out')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    campaign_id: Mapped[str | None] = mapped_column(ForeignKey("crm_campaigns.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(40), default="warmup")
    status: Mapped[str] = mapped_column(String(30), default="active")
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class JourneyStep(Base):
    __tablename__ = "crm_journey_steps"
    __table_args__ = (
        UniqueConstraint("journey_id", "step_no"),
        CheckConstraint(
            "status IN ('pending', 'due', 'sent', 'skipped', 'failed', 'waiting_reply', 'done')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    journey_id: Mapped[str] = mapped_column(ForeignKey("crm_journeys.id"), index=True)
    step_no: Mapped[int] = mapped_column(Integer)
    condition: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    communication_id: Mapped[str | None] = mapped_column(ForeignKey("crm_communications.id"), nullable=True)
    notes: Mapped[str] = mapped_column(String(500), default="")


class Agreement(Base):
    """Legal agreement distinct from Deal.won (CRM-008)."""
    __tablename__ = "crm_agreements"
    __table_args__ = (
        CheckConstraint(
            "legal_status IN ('draft', 'sent', 'accepted', 'signed', 'void', 'expired')"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    deal_id: Mapped[str | None] = mapped_column(ForeignKey("crm_deals.id"), nullable=True)
    legal_status: Mapped[str] = mapped_column(String(30), default="draft")
    title: Mapped[str] = mapped_column(String(250), default="")
    offer_acceptance: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evidence: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OrderLine(Base):
    """Line items for order ledger (PAY-002)."""
    __tablename__ = "crm_order_lines"
    __table_args__ = (CheckConstraint("amount_minor >= 0"), CheckConstraint("quantity >= 1"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("crm_products.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3), default="RUB")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class Receivable(Base):
    __tablename__ = "crm_receivables"
    __table_args__ = (
        UniqueConstraint("order_id", "sequence"),
        CheckConstraint("due_minor >= 0"),
        CheckConstraint("allocated_minor >= 0"),
        CheckConstraint("state IN ('open', 'partial', 'paid', 'cancelled', 'written_off')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"), index=True)
    order_line_id: Mapped[str | None] = mapped_column(ForeignKey("crm_order_lines.id"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)
    due_minor: Mapped[int] = mapped_column(BigInteger)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="open")
    allocated_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PaymentAllocation(Base):
    __tablename__ = "crm_payment_allocations"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("state IN ('active', 'reversed')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("crm_payments.id"))
    order_id: Mapped[str] = mapped_column(ForeignKey("crm_orders.id"), index=True)
    receivable_id: Mapped[str | None] = mapped_column(ForeignKey("crm_receivables.id"), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Refund(Base):
    __tablename__ = "crm_refunds"
    __table_args__ = (
        CheckConstraint("amount_minor > 0"),
        CheckConstraint("status IN ('pending', 'confirmed', 'failed', 'cancelled')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey("crm_payments.id"), index=True)
    refund_payment_id: Mapped[str | None] = mapped_column(ForeignKey("crm_payments.id"), nullable=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="confirmed")
    reason: Mapped[str] = mapped_column(String(500), default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OidcLink(Base):
    """Issuer+sub mapping for staff/clients (AUTH-004). Never elevates ACL by email alone."""
    __tablename__ = "crm_oidc_links"
    __table_args__ = (
        UniqueConstraint("issuer", "subject"),
        CheckConstraint("provisioning_status IN ('linked', 'pending', 'revoked', 'conflict')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    issuer: Mapped[str] = mapped_column(String(250))
    subject: Mapped[str] = mapped_column(String(250))
    account_id: Mapped[str | None] = mapped_column(ForeignKey("crm_accounts.id"), nullable=True, index=True)
    contact_id: Mapped[str | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("crm_persons.id"), nullable=True, index=True)
    profile_id: Mapped[str | None] = mapped_column(ForeignKey("crm_profiles.id"), nullable=True)
    provisioning_status: Mapped[str] = mapped_column(String(30), default="linked")
    email_hint: Mapped[str] = mapped_column(String(250), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# --- CRM redesign: Branch, Course M2M, ActionChain, payment alerts ---


class Branch(Base):
    """Owner-defined slice inside a Project (one client, campaign, recurring meet, …)."""
    __tablename__ = "crm_branches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(800), default="")
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ProjectCourse(Base):
    """M2M: LMS course_id (Product.config.course_id / AccessGrant resource) ↔ Project."""
    __tablename__ = "crm_project_courses"
    __table_args__ = (UniqueConstraint("project_id", "course_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    course_id: Mapped[str] = mapped_column(String(200))
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionChain(Base):
    """Staff Action Constructor output — base layer replacing fixed funnel as source of truth."""
    __tablename__ = "crm_action_chains"
    __table_args__ = (
        CheckConstraint("state IN ('draft', 'published', 'archived')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    course_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(800), default="")
    state: Mapped[str] = mapped_column(String(30), default="draft")
    # Ordered nodes [{id, kind, title, config, media_asset_id?, from_address?}, …]
    nodes: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # Optional Funnel id when published into legacy runtime graph.
    funnel_id: Mapped[str | None] = mapped_column(ForeignKey("funnels.id"), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ActionChainDryRun(Base):
    """Audit of Constructor «Проверить» dry-runs (email + Telegram test)."""
    __tablename__ = "crm_action_chain_dry_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    chain_id: Mapped[str] = mapped_column(ForeignKey("crm_action_chains.id"), index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("admins.telegram_id"))
    email: Mapped[str] = mapped_column(String(250), default="")
    telegram: Mapped[str] = mapped_column(String(120), default="")
    report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StaffAlertRule(Base):
    """Notify owner on paid / not paid / amount threshold."""
    __tablename__ = "crm_staff_alert_rules"
    __table_args__ = (
        CheckConstraint("kind IN ('paid', 'not_paid', 'amount_threshold')"),
        CheckConstraint("channel IN ('telegram', 'email', 'both')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    amount_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class StaffAlertEvent(Base):
    """Deduped staff payment alerts already sent."""
    __tablename__ = "crm_staff_alert_events"
    __table_args__ = (UniqueConstraint("rule_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rule_id: Mapped[str] = mapped_column(ForeignKey("crm_staff_alert_rules.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(30), default="sent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PersonTimelineEvent(Base):
    """VR-452: staff-visible Person history row (correspondence + technical)."""
    __tablename__ = "crm_person_timeline_events"
    __table_args__ = (
        UniqueConstraint("source", "external_key", name="uq_crm_person_timeline_source_key"),
        CheckConstraint("category IN ('correspondence', 'technical')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("crm_persons.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    source: Mapped[str] = mapped_column(String(40), default="voronka")  # signal|voronka|lms|solo_pr|payment|staff
    channel: Mapped[str] = mapped_column(String(30), default="system")  # email|telegram|max|web|system
    category: Mapped[str] = mapped_column(String(20))  # correspondence | technical
    kind: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(300), default="")
    summary: Mapped[str] = mapped_column(String(800), default="")
    # Soft refs only — no secrets (outbox_id, payment_id, grant_id, consent_id, …).
    payload_ref: Mapped[dict] = mapped_column(JSON, default=dict)
    external_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConnectionVault(Base):
    """VR-454: project-scoped connection credentials (bots, mail, payments). Secrets Fernet-sealed."""
    __tablename__ = "crm_connection_vault"
    __table_args__ = (
        CheckConstraint(
            "type IN ('telegram_bot', 'telegram_channel', 'max_bot', 'email_smtp', 'robokassa', 'yookassa')",
            name="ck_crm_connection_vault_type",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    branch_id: Mapped[str | None] = mapped_column(ForeignKey("crm_branches.id"), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(200))
    # Non-secret fields only (username, from_address, shop_id, …).
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    # Fernet ciphertext of secret field map — never return via list API / encode().
    secrets_blob: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {field: "••••xxxx"} written at seal time — safe for UI.
    secret_masks: Mapped[dict] = mapped_column(JSON, default=dict)
    has_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Dual-read / Activate bridge to legacy Connection row.
    legacy_connection_id: Mapped[str | None] = mapped_column(ForeignKey("connections.id"), nullable=True)
    legacy_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    rotated_by: Mapped[int | None] = mapped_column(ForeignKey("admins.telegram_id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
