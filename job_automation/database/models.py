"""SQLAlchemy models for the job-automation application."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Enum as SqlEnum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from job_automation.normalizer import WorkplaceType


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for job-automation tables."""


class JobStatus(str, Enum):
    """Supported states in the job-application lifecycle."""

    DISCOVERED = "DISCOVERED"
    QUALIFIED = "QUALIFIED"
    APPLIED = "APPLIED"
    SKIPPED = "SKIPPED"


class ApplicationMethod(str, Enum):
    """Supported ways an application can be completed and tracked."""

    MANUAL = "MANUAL"
    GREENHOUSE = "GREENHOUSE"
    LEVER = "LEVER"
    WORKDAY = "WORKDAY"
    BROWSER = "BROWSER"
    ASHBY = "ASHBY"
    SMARTRECRUITERS = "SMARTRECRUITERS"


class ApplicationStatus(str, Enum):
    """Preparation state for a future, explicitly approved application."""

    PREPARED = "PREPARED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


class OutreachStatus(str, Enum):
    """Lifecycle for one-to-one, draft-first job outreach."""

    NOT_STARTED = "NOT_STARTED"
    CONTACT_FOUND = "CONTACT_FOUND"
    DRAFTED = "DRAFTED"
    APPROVED = "APPROVED"
    SENT = "SENT"
    REPLIED = "REPLIED"
    SKIPPED = "SKIPPED"


class Job(Base):
    """A discovered job and its local application-tracking state."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_external_id_source", "external_id", "source"),
        Index("ix_jobs_company_title_application", "company", "title", "application_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    workplace_type: Mapped[WorkplaceType] = mapped_column(
        SqlEnum(
            WorkplaceType,
            name="workplace_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda values: [value.value for value in values],
        ),
        nullable=False,
        default=WorkplaceType.UNKNOWN,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description_complete: Mapped[Optional[bool]] = mapped_column(nullable=True)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    required_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    preferred_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    minimum_experience: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    maximum_experience: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_open: Mapped[Optional[bool]] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    application_url: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    matched_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    score_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[JobStatus] = mapped_column(
        SqlEnum(
            JobStatus,
            name="job_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=JobStatus.DISCOVERED,
        index=True,
    )
    skip_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_resume: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resume_match_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resume_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    application_method: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    application_status: Mapped[Optional[ApplicationStatus]] = mapped_column(
        SqlEnum(
            ApplicationStatus,
            name="application_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=True,
    )
    application_confirmation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_application_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contact_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_role: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    outreach_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reply_status: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    outreach_status: Mapped[OutreachStatus] = mapped_column(
        SqlEnum(
            OutreachStatus,
            name="outreach_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        nullable=False,
        default=OutreachStatus.NOT_STARTED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id!r} company={self.company!r} title={self.title!r}>"


class ActiveResume(Base):
    """Singleton record for the resume used by scoring and applications."""

    __tablename__ = "active_resume"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parsed_profile: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ContactDiscoveryTask(Base):
    """Durable, independently retryable public contact discovery for one job."""

    __tablename__ = "contact_discovery_tasks"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    company: Mapped[str] = mapped_column(String(500), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class JobContact(Base):
    """Public professional contact and the evidence linking them to a job."""

    __tablename__ = "job_contacts"
    __table_args__ = (
        UniqueConstraint("job_id", "identity_key"),
        UniqueConstraint("job_id", "linkedin_url"),
        UniqueConstraint("job_id", "work_email"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(500))
    linkedin_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    source: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class OutreachMessage(Base):
    """Current CV-grounded email and LinkedIn draft for one qualified job."""

    __tablename__ = "outreach_messages"
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    contact_id: Mapped[Optional[int]] = mapped_column(ForeignKey("job_contacts.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="WAITING_INPUT")
    email_subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    linkedin_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resume_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64))
    missing_inputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    contact_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class OutreachEmail(Base):
    """Durable send claim and delivery audit. Never automatically replay a claim."""

    __tablename__ = "outreach_emails"
    __table_args__ = (UniqueConstraint("job_id", "recipient"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # One selected contact per job, even if contact discovery later finds more people.
    job_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    contact_id: Mapped[int] = mapped_column(Integer)
    recipient: Mapped[str] = mapped_column(String(320), index=True)
    contact_name: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(32), default="SENDING", index=True)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    resume_sha256: Mapped[str] = mapped_column(String(64))
    resume_filename: Mapped[str] = mapped_column(Text)
    draft_input_hash: Mapped[str] = mapped_column(String(64))
    connected_account_id: Mapped[str] = mapped_column(String(255))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class OutreachSendGate(Base):
    """One database-wide cooldown shared by all API and CLI workers."""

    __tablename__ = "outreach_send_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_allowed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class LinkedInOutreach(Base):
    """Independent LinkedIn delivery audit, including uncertain send attempts."""

    __tablename__ = "linkedin_outreach"
    __table_args__ = (UniqueConstraint("job_key", "contact_url"),)
    job_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(255), index=True)
    contact_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contact_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    contact: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="WAITING_INPUT")
    linkedin_message_sent: Mapped[bool] = mapped_column(default=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class LinkedInOutreachGate(Base):
    __tablename__ = "linkedin_outreach_gate"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_allowed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ApplicationDispatchClaim(Base):
    """Retained even after a crash: uncertain submissions must not be replayed."""
    __tablename__ = "application_dispatch_claims"
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer, unique=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="RUNNING")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[list] = mapped_column(JSON, default=list)
