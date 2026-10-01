import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, BigInteger, Boolean, DateTime, JSON, ForeignKey, UniqueConstraint, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

def now():
    return datetime.now(timezone.utc)

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

class Admin(Base):
    __tablename__ = "admins"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    connection_ids: Mapped[list] = mapped_column(JSON, default=list)

class Connection(Base):
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    platform: Mapped[str] = mapped_column(String(20))
    token_env: Mapped[str] = mapped_column(String(80))
    secret_env: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Destination(Base):
    __tablename__ = "destinations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    name: Mapped[str] = mapped_column(String(160))
    external_chat_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(20)) # channel/group/community
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Funnel(Base):
    __tablename__ = "funnels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    connection_ids: Mapped[list] = mapped_column(JSON)
    draft: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    published_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

class Version(Base):
    __tablename__ = "versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    funnel_id: Mapped[str] = mapped_column(ForeignKey("funnels.id"))
    graph: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Entry(Base):
    __tablename__ = "entries"
    token: Mapped[str] = mapped_column(String(40), primary_key=True)
    funnel_id: Mapped[str] = mapped_column(ForeignKey("funnels.id"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    attribution: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    stage: Mapped[str] = mapped_column(String(80), default="Новый")
    score: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Identity(Base):
    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("connection_id", "external_user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    external_user_id: Mapped[str] = mapped_column(String(80))
    chat_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200), default="")

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_id: Mapped[str] = mapped_column(ForeignKey("identities.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("versions.id"))
    entry_token: Mapped[str] = mapped_column(ForeignKey("entries.token"))
    current: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="active")
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class Inbox(Base):
    __tablename__ = "inbox"
    __table_args__ = (UniqueConstraint("connection_id", "external_event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    external_event_id: Mapped[str] = mapped_column(String(160))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(20))
    mime: Mapped[str] = mapped_column(String(120), default="")
    local_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    connection_ids: Mapped[list] = mapped_column(JSON, default=list)
    telegram_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    identity_id: Mapped[str | None] = mapped_column(ForeignKey("identities.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    chat_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    external_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    position: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String(80), nullable=True)

class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    contact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Entitlement(Base):
    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("contact_id", "product_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    product_key: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("contacts.id"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
