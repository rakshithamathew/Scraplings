"""Repository operations for persisted jobs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional
from unicodedata import normalize as unicode_normalize

from sqlalchemy import Engine, Select, func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import ApplicationMethod, ApplicationStatus, Job, JobStatus, OutreachStatus, utc_now


class DuplicateJobError(ValueError):
    """Raised when a job matches the repository's deduplication key."""


def normalize_identity_text(value: str) -> str:
    """Normalize company/title text for stable identity comparisons."""
    return " ".join(unicode_normalize("NFKC", value).casefold().split())


def normalize_application_url(value: str) -> str:
    """Canonicalize surrounding whitespace and a trailing URL slash."""
    normalized = unicode_normalize("NFKC", value).strip()
    return normalized.rstrip("/") or normalized


class JobRepository:
    """Transaction-scoped CRUD operations for :class:`Job`."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False)

    def create_job(self, job: Optional[Job | Mapping[str, Any]] = None, **values: Any) -> Job:
        """Create a job, rejecting an existing normalized identity triple."""
        if isinstance(job, Job):
            if values:
                raise ValueError("Pass either a Job instance or keyword values, not both")
            instance = job
        else:
            payload = dict(job or {})
            payload.update(values)
            instance = Job(**payload)

        self._prepare_identity(instance)
        with self._sessions.begin() as session:
            duplicate = self._find_duplicate_in_session(
                session,
                company=instance.company,
                title=instance.title,
                application_url=instance.application_url,
            )
            if duplicate is not None:
                raise DuplicateJobError(f"Job duplicates existing record {duplicate.id}")
            session.add(instance)
            session.flush()
        return instance

    def get_job(self, job_id: int) -> Optional[Job]:
        """Return one job by primary key, or ``None`` when absent."""
        with self._sessions() as session:
            return session.get(Job, job_id)

    def get_jobs(
        self,
        *,
        status: Optional[JobStatus | str] = None,
        min_score: Optional[float] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[Job]:
        """Return jobs ordered newest-first, optionally filtered by status."""
        statement: Select[tuple[Job]] = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).offset(offset)
        if status is not None:
            statement = statement.where(Job.status == self._coerce_status(status))
        if min_score is not None:
            statement = statement.where(Job.match_score >= min_score)
        if limit is not None:
            statement = statement.limit(limit)
        with self._sessions() as session:
            return list(session.scalars(statement))

    def update_job(self, job_id: int, **changes: Any) -> Optional[Job]:
        """Update allowed model fields and return the updated job."""
        if not changes:
            return self.get_job(job_id)
        if "id" in changes or "created_at" in changes:
            raise ValueError("id and created_at cannot be updated")

        valid_fields = {column.key for column in Job.__table__.columns}
        unknown = set(changes) - valid_fields
        if unknown:
            raise ValueError(f"Unknown Job fields: {', '.join(sorted(unknown))}")
        if "status" in changes:
            changes["status"] = self._coerce_status(changes["status"])
        if "outreach_status" in changes:
            changes["outreach_status"] = self._coerce_outreach_status(changes["outreach_status"])
        if "application_url" in changes:
            changes["application_url"] = normalize_application_url(changes["application_url"])

        with self._sessions.begin() as session:
            instance = session.get(Job, job_id)
            if instance is None:
                return None
            for field, value in changes.items():
                setattr(instance, field, value)

            duplicate = self._find_duplicate_in_session(
                session,
                company=instance.company,
                title=instance.title,
                application_url=instance.application_url,
                exclude_job_id=instance.id,
            )
            if duplicate is not None:
                raise DuplicateJobError(f"Update duplicates existing record {duplicate.id}")
            instance.updated_at = utc_now()
            session.flush()
        return instance

    def update_status(self, job_id: int, status: JobStatus | str) -> Optional[Job]:
        """Update only a job's validated lifecycle status."""
        return self.update_job(job_id, status=self._coerce_status(status))

    def mark_applied(
        self,
        job_id: int,
        *,
        resume_used: Optional[str],
        application_method: ApplicationMethod | str,
        applied_at: Any,
    ) -> Optional[Job]:
        """Record application metadata and atomically move a job to APPLIED."""
        return self.update_job(
            job_id,
            status=JobStatus.APPLIED,
            resume_used=resume_used,
            application_method=self._coerce_application_method(application_method).value,
            application_status=ApplicationStatus.APPLIED,
            applied_at=applied_at,
        )

    def record_application_preparation(
        self,
        job_id: int,
        *,
        application_method: ApplicationMethod | str,
        application_status: ApplicationStatus | str,
    ) -> Optional[Job]:
        """Persist a non-submitting application preparation result."""
        return self.update_job(
            job_id,
            application_method=self._coerce_application_method(application_method).value,
            application_status=self._coerce_application_status(application_status),
        )

    def record_outreach_contact(
        self,
        job_id: int,
        *,
        contact_name: str,
        contact_email: str,
    ) -> Optional[Job]:
        """Store one selected contact without sending any communication."""
        return self.update_job(
            job_id,
            contact_name=contact_name.strip(),
            contact_email=contact_email.strip(),
            outreach_status=OutreachStatus.CONTACT_FOUND,
        )

    def record_outreach_draft(
        self,
        job_id: int,
        *,
        contact_name: str,
        contact_email: str,
    ) -> Optional[Job]:
        """Record that a reviewable draft exists; the draft is not sent."""
        return self.update_job(
            job_id,
            contact_name=contact_name.strip(),
            contact_email=contact_email.strip(),
            outreach_status=OutreachStatus.DRAFTED,
        )

    def update_outreach_status(
        self,
        job_id: int,
        status: OutreachStatus | str,
    ) -> Optional[Job]:
        return self.update_job(job_id, outreach_status=self._coerce_outreach_status(status))

    def get_status_counts(self) -> dict[JobStatus, int]:
        """Return total rows grouped by lifecycle status."""
        statement = select(Job.status, func.count(Job.id)).group_by(Job.status)
        with self._sessions() as session:
            counts = {status: count for status, count in session.execute(statement)}
        return {status: int(counts.get(status, 0)) for status in JobStatus}

    def count_jobs(self) -> int:
        """Return the total number of persisted jobs."""
        with self._sessions() as session:
            return int(session.scalar(select(func.count(Job.id))) or 0)

    def job_exists(
        self,
        *,
        job_id: Optional[int] = None,
        external_id: Optional[str] = None,
        source: Optional[str] = None,
        company: Optional[str] = None,
        title: Optional[str] = None,
        application_url: Optional[str] = None,
    ) -> bool:
        """Check identity by ID, source/external ID, or the deduplication triple."""
        if job_id is not None:
            return self.get_job(job_id) is not None
        if external_id is not None:
            statement = select(Job.id).where(Job.external_id == external_id)
            if source is not None:
                statement = statement.where(Job.source == source)
            with self._sessions() as session:
                return session.scalar(statement.limit(1)) is not None
        if company is not None and title is not None and application_url is not None:
            return self.find_duplicate(company, title, application_url) is not None
        raise ValueError("Provide job_id, external_id, or company/title/application_url")

    def find_duplicate(
        self,
        company: str,
        title: str,
        application_url: str,
        *,
        exclude_job_id: Optional[int] = None,
    ) -> Optional[Job]:
        """Find a job by normalized company, normalized title, and application URL."""
        with self._sessions() as session:
            return self._find_duplicate_in_session(
                session,
                company=company,
                title=title,
                application_url=application_url,
                exclude_job_id=exclude_job_id,
            )

    def delete_job(self, job_id: int) -> bool:
        """Delete a job by ID and report whether a row existed."""
        with self._sessions.begin() as session:
            instance = session.get(Job, job_id)
            if instance is None:
                return False
            session.delete(instance)
        return True

    @staticmethod
    def _prepare_identity(job: Job) -> None:
        if not job.company or not job.title or not job.application_url:
            raise ValueError("company, title, and application_url are required")
        job.company = job.company.strip()
        job.title = job.title.strip()
        job.application_url = normalize_application_url(job.application_url)

    @staticmethod
    def _coerce_status(status: JobStatus | str) -> JobStatus:
        if isinstance(status, JobStatus):
            return status
        try:
            return JobStatus(status)
        except ValueError as error:
            allowed = ", ".join(member.value for member in JobStatus)
            raise ValueError(f"Invalid job status {status!r}; expected one of: {allowed}") from error

    @staticmethod
    def _coerce_application_method(method: ApplicationMethod | str) -> ApplicationMethod:
        if isinstance(method, ApplicationMethod):
            return method
        try:
            return ApplicationMethod(method)
        except ValueError as error:
            allowed = ", ".join(member.value for member in ApplicationMethod)
            raise ValueError(f"Invalid application method {method!r}; expected one of: {allowed}") from error

    @staticmethod
    def _coerce_application_status(status: ApplicationStatus | str) -> ApplicationStatus:
        if isinstance(status, ApplicationStatus):
            return status
        try:
            return ApplicationStatus(status)
        except ValueError as error:
            allowed = ", ".join(member.value for member in ApplicationStatus)
            raise ValueError(f"Invalid application status {status!r}; expected one of: {allowed}") from error

    @staticmethod
    def _coerce_outreach_status(status: OutreachStatus | str) -> OutreachStatus:
        if isinstance(status, OutreachStatus):
            return status
        try:
            return OutreachStatus(status)
        except ValueError as error:
            allowed = ", ".join(member.value for member in OutreachStatus)
            raise ValueError(f"Invalid outreach status {status!r}; expected one of: {allowed}") from error

    @staticmethod
    def _find_duplicate_in_session(
        session: Session,
        *,
        company: str,
        title: str,
        application_url: str,
        exclude_job_id: Optional[int] = None,
    ) -> Optional[Job]:
        canonical_url = normalize_application_url(application_url)
        statement = select(Job).where(Job.application_url == canonical_url)
        if exclude_job_id is not None:
            statement = statement.where(Job.id != exclude_job_id)

        normalized_company = normalize_identity_text(company)
        normalized_title = normalize_identity_text(title)
        for candidate in session.scalars(statement):
            if (
                normalize_identity_text(candidate.company) == normalized_company
                and normalize_identity_text(candidate.title) == normalized_title
            ):
                return candidate
        return None
