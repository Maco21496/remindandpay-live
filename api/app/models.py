from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Numeric, Enum, Time, JSON, ForeignKey, Text, Boolean, UniqueConstraint, Index, text
from sqlalchemy.orm import relationship
from datetime import datetime, timedelta, time
import secrets
from .database import Base
from sqlalchemy.sql import func
from sqlalchemy import PrimaryKeyConstraint


# Reusable enums
TERMS_ENUM = Enum("net_30", "net_60", "month_following", "custom", name="terms_type")
CONTACT_CHANNEL_ENUM = Enum("sms", "email", "none", name="contact_channel")
REMINDER_CHANNEL_ENUM = Enum("email", "sms", name="reminder_channel")
INVOICE_KIND_ENUM = Enum("invoice", "credit_note", name="invoice_kind")
PAYMENT_KIND_ENUM = Enum("payment", "refund", name="payment_kind")
TIME_FORMAT_ENUM = Enum("24h", "12h", name="time_format")
TEMPLATE_TAG_ENUM = Enum("gentle", "firm", "aggressive", "custom", name="template_tag")

class AppSettings(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # one row per user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    date_locale     = Column(String(10),                        nullable=False, default="en-GB")
    time_format     = Column(Enum("24h", "12h", name="timefmt"),nullable=False, default="24h")
    default_country = Column(String(2),                         nullable=False, default="GB")
    # UI currency for display/formatting (ISO code)
    currency        = Column(String(10),                        nullable=False, default="GBP")

    timezone          = Column(String(64),  nullable=False, default="Europe/London")
    default_send_time = Column(Time,        nullable=False, default=time(14, 0))  # 14:00

    org_address    = Column(Text, nullable=True)
    org_logo_url   = Column(String(255), nullable=True)
    # Branding / theme
    theme          = Column(String(20), nullable=True)     # e.g. 'teal', 'indigo', 'custom'
    brand_color    = Column(String(7),  nullable=True)     # '#RRGGBB' for custom primary

    user = relationship("User")

class AccountSmsSettings(Base):
    __tablename__ = "account_sms_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True, index=True)

    enabled = Column(Boolean, nullable=False, default=False)
    chasing_delivery_mode = Column(String(10), nullable=False, default="email")  # email|sms|both

    twilio_phone_number = Column(String(30), nullable=True)
    twilio_phone_sid = Column(String(64), nullable=True)

    forwarding_enabled = Column(Boolean, nullable=False, default=False)
    forward_to_phone = Column(String(30), nullable=True)

    bundle_size = Column(Integer, nullable=False, default=1000)
    credits_balance = Column(Integer, nullable=False, default=0)
    free_credits = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User")

class SmsPricingSettings(Base):
    __tablename__ = "sms_pricing_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sms_starting_credits = Column(Integer, nullable=False, default=1000)
    sms_monthly_number_cost = Column(Integer, nullable=False, default=100)
    sms_send_cost = Column(Integer, nullable=False, default=5)
    sms_forward_cost = Column(Integer, nullable=False, default=5)
    sms_suspend_after_days = Column(Integer, nullable=False, default=14)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

class SmsCreditLedger(Base):
    __tablename__ = "sms_credit_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    entry_type = Column(Enum("credit", "debit", name="sms_ledger_entry_type"), nullable=False)
    amount = Column(Integer, nullable=False)
    reason = Column(String(120), nullable=False)
    reference_id = Column(String(64), nullable=True)
    metadata = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User")

class Customer(Base):
    __tablename__ = "customers"

    __table_args__ = (
        Index("ix_customers_user", "user_id"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)
    name             = Column(String(200), nullable=False)
    email            = Column(String(200), nullable=True)
    phone            = Column(String(50),  nullable=True)
    billing_line1    = Column(String(255), nullable=True)
    billing_line2    = Column(String(255), nullable=True)
    billing_city     = Column(String(120), nullable=True)
    billing_region   = Column(String(120), nullable=True)   # County/State/Province
    billing_postcode = Column(String(32),  nullable=True)
    billing_country  = Column(String(2),   nullable=False, default="GB")
    terms_type       = Column(TERMS_ENUM, nullable=False, default="net_30")
    terms_days       = Column(Integer, nullable=True)  # only for 'custom'
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)

    # chasing preferences
    preferred_channel     = Column(CONTACT_CHANNEL_ENUM, nullable=False, default="sms")  # sms|email|none
    opted_out             = Column(Boolean, nullable=False, default=False)

    # Keep the existing column name (still used elsewhere in your code)
    reminder_sequence_id  = Column(Integer, ForeignKey("chasing_plans.id"), nullable=True)

    # Relationships
    user      = relationship("User", back_populates="customers")
    invoices  = relationship("Invoice", back_populates="customer")
    payments  = relationship("Payment", back_populates="customer")

    # IMPORTANT: make the join target explicit so SQLAlchemy **always** hits `chasing_plans`
    reminder_sequence = relationship(
        "ChasingPlan",
        back_populates="customers",
        lazy="joined",
        primaryjoin="ChasingPlan.id == Customer.reminder_sequence_id",
        foreign_keys=[reminder_sequence_id],
    )

class Invoice(Base):
    __tablename__ = "invoices"

    __table_args__ = (
        # Enforce one invoice_number per customer
        UniqueConstraint("customer_id", "invoice_number", name="uq_invoices_customer_invoice_number"),
        # Helpful composite index (MySQL uses it for searches/joins)
        Index("ix_invoices_customer_invoice_number", "customer_id", "invoice_number"),
        # Scope by owner
        Index("ix_invoices_user", "user_id"),
    )

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False)   # <-- added
    kind             = Column(INVOICE_KIND_ENUM, nullable=False, default="invoice")
    customer_id      = Column(Integer, ForeignKey("customers.id"), nullable=False)
    invoice_number   = Column(String(64), nullable=False)
    amount_due       = Column(Numeric(12,2), nullable=False)
    currency         = Column(String(10), nullable=False, default="GBP")
    issue_date       = Column(DateTime, nullable=False, default=datetime.utcnow)
    terms_type       = Column(TERMS_ENUM, nullable=False, default="net_30")
    terms_days       = Column(Integer, nullable=True)
    due_date         = Column(DateTime, nullable=True)
    status           = Column(Enum("open","chasing","paid","partial","disputed","written_off", name="invoice_status"), nullable=False, default="chasing")
    paid_amount      = Column(Numeric(12,2), nullable=False, default=0)
    paid_at          = Column(DateTime, nullable=True)
    paid_method      = Column(Enum("card","bank","cash","other", name="paid_method"), nullable=True)
    reconciliation_ref = Column(String(20), nullable=True)
    external_txn_id  = Column(String(128), nullable=True)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=False, default=datetime.utcnow)

    # chasing state
    next_reminder_at = Column(DateTime, nullable=True)
    last_reminder_at = Column(DateTime, nullable=True)
    reminder_step    = Column(Integer, nullable=False, default=0)
    payment_link_url = Column(String(255), nullable=True)

    # Relationships
    user = relationship("User", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")

    allocations = relationship(
        "PaymentAllocation",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    payments = relationship(
        "Payment",
        secondary="payment_allocations",
        primaryjoin="Invoice.id==PaymentAllocation.invoice_id",
        secondaryjoin="Payment.id==PaymentAllocation.payment_id",
        viewonly=True,
    )

class Payment(Base):
    __tablename__ = "payments"
    id            = Column(Integer, primary_key=True)
    kind          = Column(PAYMENT_KIND_ENUM, nullable=False, default="payment")
    customer_id   = Column(Integer, ForeignKey("customers.id"), nullable=True)
    amount        = Column(Numeric(12,2), nullable=False)
    method        = Column(String(20), nullable=False)
    external_txn_id = Column(String(128), nullable=True)
    received_at   = Column(DateTime, nullable=False, default=datetime.utcnow)
    source        = Column(String(30), nullable=False)
    note          = Column(Text, nullable=True)

    # Relationships
    customer = relationship("Customer", back_populates="payments")

    allocations = relationship(
        "PaymentAllocation",
        back_populates="payment",
        cascade="all, delete-orphan",
    )
    invoices = relationship(
        "Invoice",
        secondary="payment_allocations",
        primaryjoin="Payment.id==PaymentAllocation.payment_id",
        secondaryjoin="Invoice.id==PaymentAllocation.invoice_id",
        viewonly=True,
    )

class ReminderEvent(Base):
    __tablename__ = "reminder_events"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    invoice_id   = Column(Integer, ForeignKey("invoices.id"), nullable=False)
    channel      = Column(REMINDER_CHANNEL_ENUM, nullable=False)  # email|sms
    template     = Column(String(100), nullable=False)
    sent_at      = Column(DateTime, nullable=False, default=datetime.utcnow)
    meta         = Column(Text, nullable=True)

class ChasingPlan(Base):
    __tablename__ = "chasing_plans"

    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name    = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_chasing_plans_user_name"),
        Index("idx_chasing_plans_user", "user_id"),
    )

    user = relationship("User")

    triggers = relationship(
        "ChasingTrigger",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="ChasingTrigger.order_index",
    )

    # Pin the reverse side to the same join so there's no ambiguity
    customers = relationship(
        "Customer",
        back_populates="reminder_sequence",
        primaryjoin="ChasingPlan.id == Customer.reminder_sequence_id",
        foreign_keys="Customer.reminder_sequence_id",
    )

class ChasingTrigger(Base):
    __tablename__ = "chasing_triggers"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # IMPORTANT: keep this column name EXACTLY as sequence_id for now,
    # because that's what exists in MySQL.
    sequence_id  = Column(Integer, ForeignKey("chasing_plans.id"), nullable=False)

    offset_days  = Column(Integer, nullable=False)               # send when invoice is X days overdue
    channel      = Column(REMINDER_CHANNEL_ENUM, nullable=False) # 'email' | 'sms'
    template_key = Column(String(64), nullable=False)
    order_index  = Column(Integer, nullable=False)               # 1..N based on sort

    # link back up to the plan
    plan = relationship(
        "ChasingPlan",
        back_populates="triggers"
    )

class InvoiceUploadPreset(Base):
    __tablename__ = "invoice_upload_presets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, nullable=False)
    mapping_json = Column(Text, nullable=False)
    header = Column(Boolean, nullable=False, default=True)
    delimiter = Column(String(5), nullable=False, default=",")
    date_format = Column(String(64), nullable=True)
    assign_mode = Column(Enum("per_row","single", name="upload_assign_mode"), nullable=False, default="per_row")
    default_customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    create_missing_customers = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    id = Column(Integer, primary_key=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False, index=True)
    invoice_id = Column(Integer, ForeignKey("invoices.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    payment = relationship("Payment", back_populates="allocations")
    invoice = relationship("Invoice", back_populates="allocations")

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    mobile_phone = Column(String(30))
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Optional convenience relationships
    customers = relationship("Customer", back_populates="user")
    invoices  = relationship("Invoice", back_populates="user")

class VerificationToken(Base):
    __tablename__ = "verification_tokens"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token      = Column(String(128), unique=True, nullable=False, index=True)
    purpose    = Column(String(32), nullable=False, default="verify_email")
    expires_at = Column(DateTime, nullable=False)
    used_at    = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User")

    @staticmethod
    def new(user_id: int, hours: int = 24) -> "VerificationToken":
        t = VerificationToken(
            user_id=user_id,
            token=secrets.token_urlsafe(32),
            purpose="verify_email",
            expires_at=datetime.utcnow() + timedelta(hours=hours),
        )
        return t

# models.py (or wherever ReminderRule is defined)

class ReminderRule(Base):
    __tablename__ = "reminder_rules"

    id                   = Column(Integer, primary_key=True, autoincrement=True)
    user_id              = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name                 = Column(String(100), nullable=False)

    # include 'chasing' to match the DB enum
    reminder_type        = Column(
        Enum("sequence", "statements", "chasing", name="reminder_type"),
        nullable=False,
        default="sequence",
    )

    reminder_sequence_id = Column(Integer, ForeignKey("chasing_plans.id"), nullable=True)

    reminder_frequency   = Column(
        Enum("daily", "weekly", "monthly", name="reminder_frequency"),
        nullable=False,
        default="weekly",
    )

    # You can keep String(8) ("14:00") since your helper normalises time,
    # even though the DB column is TIME. Works fine with your code.
    reminder_time        = Column(String(8), nullable=False, default="14:00")

    reminder_timezone    = Column(String(64), nullable=True)
    reminder_weekdays    = Column(String(64), nullable=True)  # stored as comma-str for MySQL SET
    reminder_month_days  = Column(Text, nullable=True)        # JSON in MySQL; we keep as Text

    reminder_invoice_filter = Column(
        Enum("all", "due", "overdue", name="reminder_invoice_filter"),
        nullable=False,
        default="all",
    )
    reminder_enabled     = Column(Boolean, nullable=False, default=True)

    # global rule flag
    is_global            = Column(Boolean, nullable=False, default=False)

    reminder_next_run_utc = Column(DateTime, nullable=True)
    reminder_last_run_utc = Column(DateTime, nullable=True)

    schedule             = Column(String(100), nullable=True)
    escalate             = Column(Boolean, nullable=True)

    created_at           = Column(DateTime, nullable=False, default=datetime.utcnow)

class StatementRun(Base):
    __tablename__ = "statement_runs"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    rule_id          = Column(Integer, ForeignKey("reminder_rules.id"), nullable=False, index=True)

    run_scheduled_at = Column(DateTime, nullable=False, index=True)
    run_started_at   = Column(DateTime, nullable=True)
    run_finished_at  = Column(DateTime, nullable=True)

    # Use String to avoid ENUM/value mismatches; DB already enforces allowed values
    status           = Column(String(20), nullable=False, default="queued")

    total_customers  = Column(Integer, nullable=False, default=0)
    jobs_enqueued    = Column(Integer, nullable=False, default=0)
    jobs_succeeded   = Column(Integer, nullable=False, default=0)
    jobs_failed      = Column(Integer, nullable=False, default=0)

    error_text       = Column(Text, nullable=True)

    # Match DB default CURRENT_TIMESTAMP
    created_at       = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    # keep your constraints / indexes
    __table_args__ = (
        UniqueConstraint("rule_id", "run_scheduled_at", name="uq_statement_run"),
        Index("ix_statement_runs_user_status", "user_id", "status"),
    )

    rule = relationship("ReminderRule", backref="statement_runs")


class EmailOutbox(Base):
    __tablename__ = "email_outbox"

    id               = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id          = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    customer_id      = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    invoice_id       = Column(Integer, ForeignKey("invoices.id"), nullable=True)  # present in DB
    channel          = Column(Enum("email","sms", name="outbox_channel"), nullable=False, default="email")
    template         = Column(String(100), nullable=False)        # DB is varchar(100)
    to_email         = Column(String(255), nullable=False)
    subject          = Column(String(255), nullable=False)
    body             = Column(Text, nullable=False)
    payload_json     = Column(JSON, nullable=True)                # DB is JSON (not Text)
    rule_id          = Column(Integer, ForeignKey("reminder_rules.id"), nullable=True, index=True)
    run_id           = Column(Integer, ForeignKey("statement_runs.id"), nullable=True, index=True)
    provider = Column(Enum("postmark", name="email_provider"), nullable=False, default="postmark")
    provider_message_id = Column(String(64), nullable=True, unique=True)
    delivery_status = Column(Enum("queued","sent","delivered","bounced","complained","deferred",name="delivery_status"), nullable=False, default="queued")
    delivery_detail = Column(JSON, nullable=True)
    delivered_at  = Column(DateTime, nullable=True)
    bounced_at    = Column(DateTime, nullable=True)
    complained_at = Column(DateTime, nullable=True)

    # match DB enum exactly
    status           = Column(Enum("queued","processing","sent","failed","canceled",
                                   name="outbox_status"),
                              nullable=False, default="queued")

    attempt_count    = Column(Integer, nullable=False, default=0) # NOT 'try_count'
    last_error       = Column(Text, nullable=True)
    next_attempt_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    lock_owner       = Column(String(64), nullable=True)
    lock_acquired_at = Column(DateTime, nullable=True)

    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at       = Column(DateTime, nullable=False, default=datetime.utcnow,
                              onupdate=datetime.utcnow)          # NOT 'sent_at'

    __table_args__ = (
        Index("ix_outbox_status_next", "status", "next_attempt_at"),
        Index("ix_outbox_user_status", "user_id", "status"),
    )

class DeliveryEvent(Base):
    __tablename__ = "delivery_events"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    outbox_id = Column(BigInteger, ForeignKey("email_outbox.id"), nullable=False, index=True)
    provider_message_id = Column(String(64), nullable=True, index=True)
    record_type = Column(String(40), nullable=False, index=True)   # Delivery, Bounce, SpamComplaint, etc.
    event_at = Column(DateTime, nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    provider_event_id = Column(String(64), nullable=True, unique=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

class ReminderGlobalExclusion(Base):
    __tablename__ = "reminder_global_exclusions"

    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    # Reuse the same enum values as in rules, but only weekly/monthly are valid here
    frequency   = Column(Enum("weekly","monthly", name="reminder_frequency_global"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "frequency", "customer_id", name="pk_reminder_global_exclusions"),
    )

class ReminderTemplate(Base):
    __tablename__ = "reminder_templates"

    # Index / constraints mirror your patterns elsewhere
    __table_args__ = (
        UniqueConstraint("user_id", "key", "channel", name="uq_reminder_templates_user_key_channel"),
        Index("ix_reminder_templates_user", "user_id"),
        Index("ix_reminder_templates_tag_step", "tag", "step_number"),
    )

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
    key         = Column(String(64), nullable=False)          # e.g. 'chase_g1'
    channel     = Column(REMINDER_CHANNEL_ENUM, nullable=False)  # 'email' | 'sms'
    tag         = Column(TEMPLATE_TAG_ENUM, nullable=False, default="custom")
    step_number = Column(Integer, nullable=True)              # optional ordering within a tag
    name        = Column(String(120), nullable=False)

    # email fields (sms ignores subject/body_html, uses body_text)
    subject     = Column(String(255), nullable=True)          # email-only
    body_html   = Column(Text, nullable=True)                 # email HTML (fine as Text; MEDIUMTEXT optional later)
    body_text   = Column(Text, nullable=True)                 # email plain or sms text

    is_active   = Column(Boolean, nullable=False, default=True)

    # match your timestamp style (server default + onupdate)
    updated_at  = Column(
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=datetime.utcnow
    )
