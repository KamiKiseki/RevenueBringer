from __future__ import annotations

import os
from datetime import date, datetime, timezone
from urllib.parse import urlparse
from enum import Enum
from uuid import uuid4

from dotenv import load_dotenv
from sqlalchemy import Boolean, Date, DateTime, Enum as SqlEnum, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

load_dotenv()


def _normalize_database_url(raw: str | None) -> str:
    """
    Resolve DATABASE_URL for SQLAlchemy. No silent switch away from an explicit Postgres URL.
    """
    if not (raw or "").strip():
        return "sqlite:///hvac_leads.db"
    u = str(raw).strip()
    if u.startswith("sqlite:"):
        return u
    if u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql://", 1)
    try:
        parse_src = u.replace("postgresql+psycopg://", "postgresql://", 1)
        parsed = urlparse(parse_src)
        host = (parsed.hostname or "").lower()
        if host == "host":
            raise RuntimeError("DATABASE_URL host is a placeholder; set a real DATABASE_URL.")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Invalid DATABASE_URL: {exc}") from exc
    if u.startswith("postgresql://") and "postgresql+psycopg://" not in u:
        u = u.replace("postgresql://", "postgresql+psycopg://", 1)
    elif u.startswith("postgres://"):
        u = u.replace("postgres://", "postgresql+psycopg://", 1)
    return u


class Base(DeclarativeBase):
    pass


class LeadStatus(str, Enum):
    QUEUED = "queued"
    EMAILED = "emailed"
    PAID = "paid"
    DELIVERED = "delivered"
    ACTIVE_CLIENT = "active_client"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    status: Mapped[LeadStatus] = mapped_column(
        SqlEnum(LeadStatus),
        nullable=False,
        default=LeadStatus.QUEUED,
        index=True,
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    niche: Mapped[str] = mapped_column(String(80), nullable=False, default="HVAC")
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    street_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    street_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    service_agreement_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    screenshot_url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    conversation_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: uuid4().hex, unique=True, index=True
    )
    call_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    transcript_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    leads_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier_offer_triggered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AutomationSetting(Base):
    __tablename__ = "automation_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(String(400), nullable=False)


class AgreementStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    SIGNED = "signed"
    PAID = "paid"


class Agreement(Base):
    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    client_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    pandadoc_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    vapi_call_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    offer_kind: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    stripe_plan_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signing_status: Mapped[AgreementStatus] = mapped_column(
        SqlEnum(AgreementStatus),
        nullable=False,
        default=AgreementStatus.DRAFT,
        index=True,
    )
    stripe_transaction_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    stripe_checkout_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    signed_pdf_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    audit_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

class MessageStatus(str, Enum):
    QUEUED = "queued"
    SENT = "sent"
    OPENED = "opened"
    REPLIED = "replied"
    FAILED = "failed"


class MessageEvent(Base):
    __tablename__ = "message_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="email", index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="outbound")
    status: Mapped[MessageStatus] = mapped_column(
        SqlEnum(MessageStatus),
        nullable=False,
        default=MessageStatus.QUEUED,
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class FollowUpJob(Base):
    __tablename__ = "followup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class SuppressionEntry(Base):
    __tablename__ = "suppression_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="opt_out")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="manual")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="live", index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running", index=True)
    # Legacy column name — historically counted rows inserted after scrape + dedupe.
    leads_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Explicit scrape vs DB alignment (see automation.run_cycle).
    scraped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outreach_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    followups_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replies: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    report_date: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    email_to: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="generated", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class ClientReport(Base):
    __tablename__ = "client_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    client_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_niche: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    client_city: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    week_end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    leads_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    outreach_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    responses_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls_made: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    appointments_booked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_revenue: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class ContactSubmission(Base):
    """Public website contact form — same Postgres/SQLite as Reflex Command Center (rxconfig db_url)."""

    __tablename__ = "contact_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="public_site", index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class EmailSequence(Base):
    """AI-generated 3-step cold email sequence for Instantly-style bulk import."""

    __tablename__ = "email_sequences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    email_1_subject: Mapped[str] = mapped_column(String(500), nullable=False)
    email_1_body: Mapped[str] = mapped_column(Text, nullable=False)
    email_2_subject: Mapped[str] = mapped_column(String(500), nullable=False)
    email_2_body: Mapped[str] = mapped_column(Text, nullable=False)
    email_3_subject: Mapped[str] = mapped_column(String(500), nullable=False)
    email_3_body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


class SystemLog(Base):
    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="error", index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="core", index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown", index=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True
    )


DATABASE_URL = _normalize_database_url(os.getenv("DATABASE_URL"))

engine_kwargs = {"echo": False, "future": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, **engine_kwargs)


def _ensure_column(table: str, column: str, sql_type: str) -> None:
    # Only run this migration helper for SQLite.
    if not DATABASE_URL.startswith("sqlite"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing = {r[1] for r in rows}
        if column not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def init_db() -> None:
    global DATABASE_URL, engine
    # No silent SQLite fallback: misconfigured or unreachable DB must fail loudly.
    Base.metadata.create_all(engine)
    # Lightweight SQLite migrations for newly added fields.
    _ensure_column("leads", "location", "VARCHAR(120)")
    _ensure_column("leads", "street_address", "VARCHAR(255)")
    _ensure_column("leads", "street_name", "VARCHAR(120)")
    _ensure_column("leads", "service_agreement_text", "TEXT")
    _ensure_column("leads", "screenshot_url", "VARCHAR(800)")
    _ensure_column("leads", "conversation_history", "TEXT")
    _ensure_column("leads", "correlation_id", "VARCHAR(64)")
    _ensure_column("leads", "call_status", "VARCHAR(64)")
    _ensure_column("leads", "transcript_url", "VARCHAR(1000)")
    _ensure_column("leads", "leads_sent", "INTEGER DEFAULT 0")
    _ensure_column("leads", "tier_offer_triggered", "BOOLEAN DEFAULT 0")
    _ensure_column("agreements", "offer_kind", "VARCHAR(32)")
    _ensure_column("agreements", "stripe_plan_amount_cents", "INTEGER")
    _ensure_column("agreements", "vapi_call_id", "VARCHAR(255)")
    _ensure_column("agreements", "stripe_checkout_session_id", "VARCHAR(255)")
    _ensure_column("agreements", "stripe_checkout_url", "VARCHAR(1000)")
    _ensure_column("agreements", "audit_notes", "TEXT")
    _ensure_column("automation_runs", "scraped_count", "INTEGER DEFAULT 0")
    _ensure_column("automation_runs", "inserted_count", "INTEGER DEFAULT 0")
    # Hosted Postgres: lightweight ALTERs (create_all does not add columns to existing tables).
    if not DATABASE_URL.startswith("sqlite"):
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE automation_runs ADD COLUMN IF NOT EXISTS scraped_count INTEGER NOT NULL DEFAULT 0"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE automation_runs ADD COLUMN IF NOT EXISTS inserted_count INTEGER NOT NULL DEFAULT 0"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS leads_sent INTEGER NOT NULL DEFAULT 0"
                    )
                )
                conn.execute(
                    text(
                        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS tier_offer_triggered BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
                conn.execute(text("ALTER TABLE agreements ADD COLUMN IF NOT EXISTS offer_kind VARCHAR(32)"))
                conn.execute(
                    text(
                        "ALTER TABLE agreements ADD COLUMN IF NOT EXISTS stripe_plan_amount_cents INTEGER"
                    )
                )
                conn.execute(
                    text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS leads_sent INTEGER NOT NULL DEFAULT 0")
                )
                conn.execute(
                    text(
                        "ALTER TABLE leads ADD COLUMN IF NOT EXISTS tier_offer_triggered BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
        except Exception:
            pass
    if DATABASE_URL.startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE leads SET correlation_id = lower(hex(randomblob(16))) "
                    "WHERE correlation_id IS NULL OR correlation_id = ''"
                )
            )


def get_session() -> Session:
    return Session(engine)


def set_setting(key: str, value: str) -> None:
    with get_session() as session:
        item = session.get(AutomationSetting, key)
        if item is None:
            item = AutomationSetting(key=key, value=value)
        else:
            item.value = value
        session.add(item)
        session.commit()


def get_setting(key: str, default: str) -> str:
    with get_session() as session:
        item = session.get(AutomationSetting, key)
        return item.value if item else default


def log_system_event(
    *,
    source: str,
    action: str,
    detail: str,
    level: str = "error",
    correlation_id: str | None = None,
) -> None:
    """
    Persist an operational event for System Logs UI.
    Safe no-raise behavior so logging never crashes business logic.
    """
    try:
        with get_session() as session:
            session.add(
                SystemLog(
                    level=(level or "error")[:16],
                    source=(source or "core")[:64],
                    action=(action or "unknown")[:128],
                    detail=(detail or "")[:2000],
                    correlation_id=(correlation_id or None),
                )
            )
            session.commit()
    except Exception:
        pass
