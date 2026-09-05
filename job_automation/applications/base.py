"""Safe application planning contracts. Real submission is disabled."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from job_automation.database import ApplicationMethod, ApplicationStatus, Job, JobRepository
from job_automation.resume import ResumeMetadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ApplicationJob(Protocol):
    id: int
    source: str
    source_url: str | None
    application_url: str
    recommended_resume: str | None


class ApplicationField(BaseModel):
    """A likely form field; it intentionally contains no applicant value."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    required: bool = True
    notes: str | None = None


class ApplicationValidation(BaseModel):
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ApplicationPlan(BaseModel):
    """Serializable preparation output that cannot itself submit an application."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    provider: ApplicationMethod
    application_url: str | None
    resume_path: str | None
    fields: tuple[ApplicationField, ...]
    application_status: ApplicationStatus
    requires_user_approval: Literal[True] = True
    warnings: tuple[str, ...] = ()


class SubmissionResult(BaseModel):
    submitted: Literal[False] = False
    requires_user_approval: Literal[True] = True
    application_status: Literal[ApplicationStatus.NEEDS_REVIEW] = ApplicationStatus.NEEDS_REVIEW
    message: str = "Submission is disabled. Review the plan and apply manually."


COMMON_FIELDS = (
    ApplicationField(name="first_name", label="First name"),
    ApplicationField(name="last_name", label="Last name"),
    ApplicationField(name="email", label="Email"),
    ApplicationField(name="phone", label="Phone", required=False),
    ApplicationField(name="resume", label="Resume"),
)


class BaseApplicationAgent(ABC):
    """Base class for preparing application plans without submitting forms."""

    provider: ApplicationMethod

    def __init__(self, *, project_root: str | Path = PROJECT_ROOT) -> None:
        self.project_root = Path(project_root).resolve()
        self.resume_directory = (self.project_root / "resumes").resolve()

    @abstractmethod
    def can_handle(self, job: ApplicationJob) -> bool:
        """Return whether this adapter recognizes the job's provider."""

    @abstractmethod
    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        """Return provider-level field expectations without scraping or filling a form."""

    def prepare_application(
        self,
        job: ApplicationJob,
        resume: str | Path | ResumeMetadata | None = None,
    ) -> ApplicationPlan:
        resume_path = self._select_resume_path(job, resume)
        warnings: list[str] = []
        if not self.can_handle(job):
            warnings.append(f"{self.provider.value} adapter does not recognize this application URL")
        if not self._valid_application_url(job.application_url):
            warnings.append("A public HTTP(S) application URL is required")
        if resume_path is None:
            warnings.append("An existing resume inside the resumes directory is required")
        plan = ApplicationPlan(
            job_id=job.id,
            provider=self.provider,
            application_url=job.application_url or None,
            resume_path=resume_path,
            fields=self.likely_fields(job),
            application_status=ApplicationStatus.NEEDS_REVIEW if warnings else ApplicationStatus.PREPARED,
            warnings=tuple(warnings),
        )
        validation = self.validate_application(plan)
        if validation.valid:
            return plan
        combined = tuple(dict.fromkeys((*plan.warnings, *validation.errors)))
        return plan.model_copy(
            update={"application_status": ApplicationStatus.NEEDS_REVIEW, "warnings": combined}
        )

    def validate_application(self, plan: ApplicationPlan) -> ApplicationValidation:
        """Validate preparation inputs only; no remote page is opened."""
        errors: list[str] = []
        if not self._valid_application_url(plan.application_url):
            errors.append("Invalid or missing application URL")
        if not plan.resume_path or self._resolve_resume(plan.resume_path) is None:
            errors.append("Resume is missing or outside the resumes directory")
        return ApplicationValidation(valid=not errors, errors=tuple(errors), warnings=plan.warnings)

    def submit_application(self, plan: ApplicationPlan) -> SubmissionResult:
        """Never submit. The user must review and complete the application manually."""
        return SubmissionResult()

    def _select_resume_path(
        self,
        job: ApplicationJob,
        resume: str | Path | ResumeMetadata | None,
    ) -> str | None:
        supplied = resume.path if isinstance(resume, ResumeMetadata) else resume
        value = str(supplied or job.recommended_resume or "").strip()
        resolved = self._resolve_resume(value)
        if resolved is None:
            return None
        return resolved.relative_to(self.project_root).as_posix()

    def _resolve_resume(self, value: str | Path) -> Path | None:
        if not value:
            return None
        raw = Path(value)
        candidate = (raw if raw.is_absolute() else self.project_root / raw).resolve()
        try:
            candidate.relative_to(self.resume_directory)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _valid_application_url(value: str | None) -> bool:
        if not value:
            return False
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def store_application_plan(repository: JobRepository, plan: ApplicationPlan) -> Job | None:
    """Persist plan metadata explicitly; preparation itself remains side-effect free."""
    return repository.record_application_preparation(
        plan.job_id,
        application_method=plan.provider,
        application_status=plan.application_status,
    )
