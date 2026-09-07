from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.applications.base import ApplicationValidation, BrowserSubmissionResult
from job_automation.applications.candidate_profile import CandidateProfile
from job_automation.applications.field_matcher import FormFillResult, identify_profile_field
from job_automation.applications.service import ApplicationService, ApplicationSettings
from job_automation.database import JobRepository, JobStatus, WorkplaceType, initialize_database
from job_automation.resume import ParsedCandidateProfile


class DryRunAgent:
    def __init__(self) -> None:
        self.page = None
        self.closed = False

    async def open_application(self, job: object) -> bool:
        return True

    async def fill_candidate_details(self, profile: CandidateProfile) -> FormFillResult:
        return FormFillResult(fields_filled=["first_name", "email"])

    async def upload_resume(self, path: Path) -> bool:
        return True

    async def fill_job_questions(self, job: object, profile: CandidateProfile) -> FormFillResult:
        return FormFillResult(fields_filled=["requires_sponsorship"])

    async def validate_before_submit(self) -> ApplicationValidation:
        return ApplicationValidation(valid=True)

    async def submit(self) -> BrowserSubmissionResult:
        return BrowserSubmissionResult(ready_to_submit=True, message="READY_TO_SUBMIT")

    async def verify_submission(self) -> BrowserSubmissionResult:
        raise AssertionError("dry-run must not verify a submission")

    async def close(self) -> None:
        self.closed = True


class ConfirmedAgent(DryRunAgent):
    async def submit(self) -> BrowserSubmissionResult:
        return BrowserSubmissionResult(submitted=True, message="Submitted")

    async def verify_submission(self) -> BrowserSubmissionResult:
        return BrowserSubmissionResult(
            submitted=True,
            confirmed=True,
            confirmation_text="Application submitted successfully",
            external_application_id="CONFIRM-123",
            message="Confirmed",
        )


class FakeSessionManager:
    def __init__(self, connected: bool) -> None:
        self.connected = connected

    def is_connected(self, platform: str) -> bool:
        return self.connected

    def status(self, platform: str):
        from job_automation.auth import ConnectionState
        return ConnectionState(platform, "connected" if self.connected else "disconnected", self.connected, "")

    def profile_directory(self, platform: str) -> Path:
        return Path("profiles") / platform

    def mark_disconnected(self, platform: str, reason: str = "") -> None:
        self.connected = False


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'applications.db').as_posix()}")
    try:
        yield JobRepository(engine)
    finally:
        engine.dispose()


def _eligible_job(repository: JobRepository):
    return repository.create_job(
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        description="Build user interfaces with React and TypeScript.",
        skills=["React", "TypeScript"],
        source="greenhouse",
        application_url="https://job-boards.greenhouse.io/example/jobs/1",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )


def test_semantic_field_identification() -> None:
    assert identify_profile_field("Applicant LinkedIn Profile URL") == "linkedin_url"
    assert identify_profile_field("aria-label: Current employer") == "current_company"
    assert identify_profile_field("Favorite color") is None


@pytest.mark.asyncio
async def test_dry_run_records_ready_without_applied(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    (resumes / "frontend_resume.pdf").touch()
    repository.set_active_resume(
        original_filename="frontend_resume.pdf",
        path="resumes/frontend_resume.pdf",
        content_type="application/pdf",
        sha256="a" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React", "TypeScript"]).model_dump(),
    )
    job = _eligible_job(repository)
    agent = DryRunAgent()
    service = ApplicationService(
        repository,
        settings=ApplicationSettings(
            dry_run=True,
            headless=True,
            delay_between_applications_seconds=0,
            max_applications_per_run=3,
        ),
        candidate_profile=CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
        project_root=tmp_path,
        agent_factory=lambda *args, **kwargs: agent,  # type: ignore[arg-type]
    )

    summary = await service.run_application_cycle()
    stored = repository.get_job(job.id)

    assert summary.eligible == 1
    assert summary.attempted == 1
    assert summary.applied == 0
    assert stored is not None
    assert stored.status is JobStatus.QUALIFIED
    assert stored.applied_at is None
    assert agent.closed is True


@pytest.mark.asyncio
async def test_missing_resume_stops_before_browser(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    job = _eligible_job(repository)
    repository.set_active_resume(
        original_filename="missing.pdf",
        path="resumes/missing.pdf",
        content_type="application/pdf",
        sha256="a" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React"]).model_dump(),
    )
    called = False

    def factory(*args: object, **kwargs: object):
        nonlocal called
        called = True
        return DryRunAgent()

    service = ApplicationService(
        repository,
        settings=ApplicationSettings(delay_between_applications_seconds=0),
        candidate_profile=CandidateProfile(first_name="Rakshitha"),
        project_root=tmp_path,
        agent_factory=factory,
    )

    summary = await service.run_application_cycle()
    stored = repository.get_job(job.id)

    assert summary.failed == 0
    assert summary.needs_review == 1
    assert summary.attempted == 0
    assert called is False
    assert stored is not None
    assert stored.status is JobStatus.QUALIFIED
    assert "resume" in (stored.review_reason or "").casefold()


@pytest.mark.asyncio
async def test_confirmed_submission_is_the_only_path_to_applied(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    (resumes / "active.pdf").write_bytes(b"%PDF-1.4\n")
    repository.set_active_resume(
        original_filename="active.pdf",
        path="resumes/active.pdf",
        content_type="application/pdf",
        sha256="c" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React", "TypeScript"]).model_dump(),
    )
    job = _eligible_job(repository)
    agent = ConfirmedAgent()
    service = ApplicationService(
        repository,
        settings=ApplicationSettings(
            dry_run=False,
            headless=True,
            delay_between_applications_seconds=0,
        ),
        candidate_profile=CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
        project_root=tmp_path,
        agent_factory=lambda *args, **kwargs: agent,  # type: ignore[arg-type]
    )

    summary = await service.run_application_cycle()
    stored = repository.get_job(job.id)

    assert summary.applied == 1
    assert stored is not None
    assert stored.status is JobStatus.APPLIED
    assert stored.application_confirmation == "Application submitted successfully"
    assert stored.external_application_id == "CONFIRM-123"


@pytest.mark.asyncio
async def test_explicit_selection_processes_all_eligible_selected_jobs(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    (resumes / "active.pdf").write_bytes(b"%PDF-1.4\n")
    repository.set_active_resume(
        original_filename="active.pdf",
        path="resumes/active.pdf",
        content_type="application/pdf",
        sha256="d" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React"]).model_dump(),
    )
    first = _eligible_job(repository)
    second = repository.create_job(
        title="React Developer",
        company="Second",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        description="React application development",
        skills=["React"],
        source="lever",
        application_url="https://jobs.lever.co/second/2",
        match_score=88,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    ineligible = repository.create_job(
        title="Frontend Developer",
        company="Third",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="test",
        application_url="https://example.test/third",
        match_score=65,
        status=JobStatus.DISCOVERED,
        is_open=True,
    )
    service = ApplicationService(
        repository,
        settings=ApplicationSettings(
            dry_run=False,
            headless=True,
            delay_between_applications_seconds=0,
            max_applications_per_run=1,
        ),
        candidate_profile=CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
        project_root=tmp_path,
        agent_factory=lambda *args, **kwargs: ConfirmedAgent(),  # type: ignore[arg-type]
    )

    summary = await service.run_application_cycle(job_ids=[first.id, second.id, ineligible.id])

    assert summary.selected == 3
    assert summary.eligible == 2
    assert summary.ineligible == 1
    assert summary.applied == 2
    assert repository.get_job(first.id).status is JobStatus.APPLIED  # type: ignore[union-attr]
    assert repository.get_job(second.id).status is JobStatus.APPLIED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_platform_job_requires_saved_connection(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    (tmp_path / "resumes").mkdir()
    (tmp_path / "resumes" / "active.pdf").touch()
    repository.set_active_resume(
        original_filename="active.pdf",
        path="resumes/active.pdf",
        content_type="application/pdf",
        sha256="e" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React"]).model_dump(),
    )
    job = _eligible_job(repository)
    repository.update_job(job.id, application_url="https://www.linkedin.com/jobs/view/123")
    agent = ConfirmedAgent()
    service = ApplicationService(
        repository,
        settings=ApplicationSettings(dry_run=False, delay_between_applications_seconds=0),
        candidate_profile=CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
        project_root=tmp_path,
        agent_factory=lambda *args, **kwargs: agent,  # type: ignore[arg-type]
        session_manager=FakeSessionManager(False),  # type: ignore[arg-type]
    )

    summary = await service.run_application_cycle(job_id=job.id)

    assert summary.needs_review == 1
    assert summary.attempted == 0
    assert agent.closed is False
    assert "not connected" in (repository.get_job(job.id).review_reason or "").casefold()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_public_ats_does_not_require_discovery_platform_connection(
    repository: JobRepository,
    tmp_path: Path,
) -> None:
    (tmp_path / "resumes").mkdir()
    (tmp_path / "resumes" / "active.pdf").touch()
    repository.set_active_resume(
        original_filename="active.pdf",
        path="resumes/active.pdf",
        content_type="application/pdf",
        sha256="f" * 64,
        parsed_profile=ParsedCandidateProfile(skills=["React"]).model_dump(),
    )
    job = _eligible_job(repository)
    repository.update_job(job.id, source="linkedin", source_url="https://www.linkedin.com/jobs/view/123")
    service = ApplicationService(
        repository,
        settings=ApplicationSettings(dry_run=False, delay_between_applications_seconds=0),
        candidate_profile=CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
        project_root=tmp_path,
        agent_factory=lambda *args, **kwargs: ConfirmedAgent(),  # type: ignore[arg-type]
        session_manager=FakeSessionManager(False),  # type: ignore[arg-type]
    )

    summary = await service.run_application_cycle(job_id=job.id)

    assert summary.applied == 1
