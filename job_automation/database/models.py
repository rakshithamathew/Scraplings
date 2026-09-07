"""SQLAlchemy models for the job-automation application."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Enum as SqlEnum, Float, Index, Integer, JSON, String, Text
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
