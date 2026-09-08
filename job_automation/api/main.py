"""Minimal local FastAPI backend for job discovery and tracking."""

from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_automation.applications import application_eligibility_errors
from job_automation.applications.service import ApplicationCycleSummary, ApplicationService
from job_automation.auth import ConnectionState, SessionManager
from job_automation.database import (
    ApplicationMethod,
    ApplicationStatus,
    DEFAULT_DATABASE_URL,
    Job,
    JobRepository,
    JobStatus,
    OutreachStatus,
    WorkplaceType,
    initialize_database,
)
from job_automation.matching.ranker import rank_jobs
from job_automation.resume import ParsedCandidateProfile, parse_resume
from job_automation.scraper.service import (
    DiscoverySummary,
    JobDiscoveryService,
    load_sources,
)


LOCAL_FRONTEND_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str | None
    title: str
    company: str
    location: str | None
    workplace_type: WorkplaceType
    description: str | None
    skills: list[str]
    source: str
    source_url: str | None
    application_url: str
    posted_at: datetime | None
    scraped_at: datetime
    match_score: float | None
    matched_skills: list[str]
    missing_skills: list[str]
    score_reason: str | None
    status: JobStatus
    skip_reason: str | None
    recommended_resume: str | None
    resume_used: str | None
    applied_at: datetime | None
    application_method: str | None
    application_status: ApplicationStatus | None
    application_confirmation: str | None
    external_application_id: str | None
    review_reason: str | None
    failure_reason: str | None
    contact_name: str | None
    contact_role: str | None
    contact_email: str | None
    outreach_sent_at: datetime | None
    reply_status: str | None
    outreach_status: OutreachStatus
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    status: JobStatus


class MarkAppliedRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    resume_used: str = Field(min_length=1, max_length=2000)
    application_method: ApplicationMethod
    applied_at: datetime | None = None
    application_confirmation: str | None = Field(default=None, max_length=4000)
    external_application_id: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def require_submission_evidence(self) -> "MarkAppliedRequest":
        if not (self.application_confirmation or "").strip() and not (
            self.external_application_id or ""
        ).strip():
            raise ValueError("submission confirmation or external application ID is required")
        return self


class StatsResponse(BaseModel):
    total_jobs: int
    qualified: int
    applied: int
    needs_review: int


class DiscoveryResponse(BaseModel):
    sources_checked: int
    jobs_discovered: int
    new_jobs: int
    duplicates: int
    failed_sources: int
    remote_eligible: int
    bengaluru_hybrid: int
    bengaluru_onsite: int
    filtered: int
    jobs_scored: int = 0
    qualified: int = 0


class ScoreResponse(BaseModel):
    jobs_scored: int
    qualified: int
    discovered: int


class ResumeResponse(BaseModel):
    filename: str
    path: str
    status: str = "Active"
    uploaded_at: datetime
    job_titles: list[str]
    skills: list[str]
    years_of_experience: float | None


class ApplicationStatusResponse(BaseModel):
    running: bool
    dry_run: bool
    auto_apply_enabled: bool
    last_run: dict[str, Any] | None


class ApplicationSelectionRequest(BaseModel):
    job_ids: list[int] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_positive_ids(self) -> "ApplicationSelectionRequest":
        if any(job_id < 1 for job_id in self.job_ids):
            raise ValueError("job IDs must be positive")
        self.job_ids = list(dict.fromkeys(self.job_ids))
        return self


class ConnectionResponse(BaseModel):
    platform: str
    status: str
    connected: bool
    message: str
    connected_at: str | None = None
    last_verified_at: str | None = None


def get_repository(request: Request) -> JobRepository:
    return request.app.state.repository


RepositoryDependency = Annotated[JobRepository, Depends(get_repository)]
router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    repository: RepositoryDependency,
    status: JobStatus | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    needs_review: bool = False,
    exclude_needs_review: bool = False,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    return repository.get_jobs(
        status=status,
        min_score=min_score,
        needs_review=needs_review,
        exclude_needs_review=exclude_needs_review,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, repository: RepositoryDependency) -> Job:
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/jobs/{job_id}/status", response_model=JobResponse)
def patch_status(
    job_id: int,
    body: StatusUpdate,
    request: Request,
    repository: RepositoryDependency,
) -> Job:
    if body.status is JobStatus.APPLIED:
        raise HTTPException(
            status_code=400,
            detail="Use mark-applied with successful-submission evidence",
        )
    current = repository.get_job(job_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Job not found")
    job = repository.update_status(job_id, body.status)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/mark-applied", response_model=JobResponse)
def mark_applied(
    job_id: int,
    body: MarkAppliedRequest,
    request: Request,
    repository: RepositoryDependency,
) -> Job:
    current = repository.get_job(job_id)
    if current is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if repository.already_applied(job_id):
        raise HTTPException(status_code=409, detail="Job is already recorded as applied")
    if current.match_score is None or current.match_score <= 70:
        raise HTTPException(status_code=400, detail="ATS score must be greater than 70")
    if current.is_open is not True:
        raise HTTPException(status_code=400, detail="Job must be verified as still open")
    project_root: Path = request.app.state.project_root
    resume_root = (project_root / "resumes").resolve()
    candidate = (project_root / body.resume_used).resolve()
    try:
        candidate.relative_to(resume_root)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Resume must be inside the resumes directory") from error
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail="Selected resume file does not exist")
    eligibility_errors = application_eligibility_errors(current, resume_available=True)
    if eligibility_errors:
        raise HTTPException(status_code=400, detail="; ".join(eligibility_errors))
    job = repository.mark_applied(
        job_id,
        resume_used=body.resume_used,
        application_method=body.application_method,
        applied_at=body.applied_at or datetime.now(timezone.utc),
        application_confirmation=body.application_confirmation,
        external_application_id=body.external_application_id,
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/stats", response_model=StatsResponse)
def stats(repository: RepositoryDependency) -> StatsResponse:
    counts = repository.get_status_counts()
    return StatsResponse(
        total_jobs=repository.count_jobs(),
        qualified=repository.count_actionable_qualified(),
        applied=counts[JobStatus.APPLIED],
        needs_review=repository.count_needs_review(),
    )


def _active_profile(repository: JobRepository) -> tuple[ParsedCandidateProfile, str]:
    active = repository.get_active_resume()
    if active is None:
        raise HTTPException(status_code=400, detail="Upload an active resume before running this action")
    try:
        return ParsedCandidateProfile.model_validate(active.parsed_profile), active.path
    except Exception as error:
        raise HTTPException(status_code=500, detail="The cached active-resume profile is invalid") from error


def _resume_response(active: Any) -> ResumeResponse:
    profile = ParsedCandidateProfile.model_validate(active.parsed_profile)
    return ResumeResponse(
        filename=active.original_filename,
        path=active.path,
        uploaded_at=active.uploaded_at,
        job_titles=profile.job_titles,
        skills=profile.skills,
        years_of_experience=profile.years_of_experience,
    )


@router.get("/resume", response_model=ResumeResponse)
def get_resume(repository: RepositoryDependency) -> ResumeResponse:
    active = repository.get_active_resume()
    if active is None:
        raise HTTPException(status_code=404, detail="No active resume has been uploaded")
    return _resume_response(active)


@router.post("/resume/upload", response_model=ResumeResponse)
async def upload_resume(
    request: Request,
    repository: RepositoryDependency,
    file: UploadFile = File(...),
) -> ResumeResponse:
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.casefold()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(status_code=400, detail="Resume must be a PDF or DOCX file")
    contents = await file.read(10 * 1024 * 1024 + 1)
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded resume is empty")
    if len(contents) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Resume must not exceed 10 MB")

    digest = sha256(contents).hexdigest()
    resume_directory = (request.app.state.project_root / "resumes").resolve()
    resume_directory.mkdir(parents=True, exist_ok=True)
    stored_name = f"{digest[:12]}_{original_name}"
    destination = (resume_directory / stored_name).resolve()
    try:
        destination.relative_to(resume_directory)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid resume filename") from error
    destination.write_bytes(contents)
    try:
        parsed = parse_resume(destination)
    except (ValueError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(error)) from error

    relative_path = destination.relative_to(request.app.state.project_root).as_posix()
    active = repository.set_active_resume(
        original_filename=original_name,
        path=relative_path,
        content_type=file.content_type or "application/octet-stream",
        sha256=digest,
        parsed_profile=parsed.model_dump(),
    )
    rank_jobs(repository, parsed.to_user_profile(), relative_path)
    request.app.state.application_service.refresh_active_resume()
    return _resume_response(active)


@router.post("/scrape", response_model=DiscoveryResponse)
async def scrape(request: Request, repository: RepositoryDependency) -> DiscoveryResponse:
    try:
        parsed, resume_path = _active_profile(repository)
        sources = request.app.state.sources_loader()
        service = request.app.state.discovery_service_factory(repository, parsed.to_user_profile())
        summary = await service.run(sources)
        ranked = rank_jobs(repository, parsed.to_user_profile(), resume_path)
        return DiscoveryResponse(**asdict(summary), jobs_scored=len(ranked), qualified=sum(
            item.job.status is JobStatus.QUALIFIED for item in ranked
        ))
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/score", response_model=ScoreResponse)
def score(request: Request, repository: RepositoryDependency) -> ScoreResponse:
    try:
        parsed, resume_path = _active_profile(repository)
        ranked = rank_jobs(repository, parsed.to_user_profile(), resume_path)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return ScoreResponse(
        jobs_scored=len(ranked),
        qualified=sum(item.job.status is JobStatus.QUALIFIED for item in ranked),
        discovered=sum(item.job.status is JobStatus.DISCOVERED for item in ranked),
    )


@router.post("/applications/run", response_model=ApplicationCycleSummary)
async def run_applications(
    request: Request,
    body: ApplicationSelectionRequest | None = None,
) -> ApplicationCycleSummary:
    try:
        return await request.app.state.application_service.run_application_cycle(
            job_ids=body.job_ids if body is not None else None,
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/applications/{job_id}/apply", response_model=ApplicationCycleSummary)
async def apply_one_job(job_id: int, request: Request, repository: RepositoryDependency) -> ApplicationCycleSummary:
    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return await request.app.state.application_service.run_application_cycle(job_id=job_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/applications/status", response_model=ApplicationStatusResponse)
def application_run_status(request: Request) -> dict[str, Any]:
    return request.app.state.application_service.status()


@router.get("/jobs/{job_id}/contacts")
def list_job_contacts(job_id: int, repository: RepositoryDependency) -> list[dict[str, Any]]:
    from job_automation.outreach.contact_repository import ContactRepository

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return [
        {key: getattr(contact, key) for key in (
            "id", "job_id", "name", "title", "company", "linkedin_url", "work_email", "source", "priority", "evidence"
        )}
        for contact in ContactRepository(repository.engine).list(job_id)
    ]


@router.post("/contacts/discover")
async def discover_pending_contacts(repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict
    from job_automation.outreach.discovery import ContactDiscoveryService

    return asdict(await ContactDiscoveryService(repository).run())


@router.post("/jobs/{job_id}/contacts/discover")
async def discover_job_contacts(job_id: int, repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict
    from job_automation.outreach.discovery import ContactDiscoveryService

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return asdict(await ContactDiscoveryService(repository).run(job_id))


@router.post("/outreach/generate")
def generate_personalized_outreach(request: Request, repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict
    from job_automation.outreach.personalized import PersonalizedOutreachService

    return asdict(PersonalizedOutreachService(repository, project_root=request.app.state.project_root).run())


@router.get("/jobs/{job_id}/outreach")
def get_personalized_outreach(job_id: int, repository: RepositoryDependency) -> dict[str, Any]:
    from job_automation.outreach.personalized import PersonalizedOutreachService, serialize_message

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    records = PersonalizedOutreachService(repository).list(job_id)
    if not records:
        raise HTTPException(status_code=404, detail="Outreach has not been generated")
    return serialize_message(records[0])


@router.post("/jobs/{job_id}/outreach/generate")
def generate_job_outreach(job_id: int, request: Request, repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict
    from job_automation.outreach.personalized import PersonalizedOutreachService

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return asdict(PersonalizedOutreachService(repository, project_root=request.app.state.project_root).run(job_id))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/outreach/send")
def send_outreach(request: Request) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(request.app.state.outreach_email_service.run())


@router.post("/jobs/{job_id}/outreach/send")
def send_job_outreach(job_id: int, request: Request, repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return asdict(request.app.state.outreach_email_service.run(job_id))


@router.get("/outreach/email-status")
def outreach_email_status(request: Request, job_id: int | None = None) -> list[dict[str, Any]]:
    return request.app.state.outreach_email_service.deliveries(job_id)


@router.post("/outreach/linkedin/send")
async def send_linkedin_outreach(request: Request) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(await request.app.state.linkedin_outreach_service.run())


@router.post("/jobs/{job_id}/outreach/linkedin/send")
async def send_job_linkedin_outreach(job_id: int, request: Request, repository: RepositoryDependency) -> dict[str, Any]:
    from dataclasses import asdict

    if repository.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return asdict(await request.app.state.linkedin_outreach_service.run(job_id))


@router.get("/outreach/linkedin/status")
def linkedin_outreach_status(request: Request, job_id: int | None = None) -> list[dict[str, Any]]:
    return request.app.state.linkedin_outreach_service.deliveries(job_id)


@router.get("/connections", response_model=list[ConnectionResponse])
def connections(request: Request) -> list[ConnectionState]:
    states = request.app.state.session_manager.list_connections()
    for state in states:
        if state.connected:
            request.app.state.repository.clear_connection_reviews(state.platform)
    return states


@router.post("/connections/{platform}/connect", response_model=ConnectionResponse)
def connect_platform(platform: str, request: Request) -> ConnectionState:
    try:
        return request.app.state.session_manager.connect(platform)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.delete("/connections/{platform}", status_code=204)
def clear_platform_connection(platform: str, request: Request) -> None:
    try:
        request.app.state.session_manager.clear_session(platform)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/automation/run")
async def run_automation_pipeline(request: Request) -> dict[str, Any]:
    pipeline = request.app.state.automation_pipeline
    try:
        # Also supports Windows API servers using a Selector loop: browser
        # subprocesses run on the worker's normal Proactor loop.
        result = await asyncio.to_thread(lambda: asyncio.run(pipeline.run(request.app.state.sources_loader())))
        return asdict(result)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/automation/status")
def automation_pipeline_status(request: Request) -> list[dict[str, Any]]:
    return request.app.state.automation_pipeline.history()


def create_app(
    database_url: str = DEFAULT_DATABASE_URL,
    *,
    project_root: str | Path = Path(__file__).resolve().parents[2],
) -> FastAPI:
    engine = initialize_database(database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        engine.dispose()

    application = FastAPI(title="Job Automation API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(LOCAL_FRONTEND_ORIGINS),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )
    application.state.repository = JobRepository(engine)
    from job_automation.outreach.email_automation import EmailAutomationService

    application.state.outreach_email_service = EmailAutomationService(
        application.state.repository, project_root=project_root
    )
    application.state.project_root = Path(project_root).resolve()
    application.state.sources_loader = load_sources
    application.state.discovery_service_factory = lambda repository, profile: JobDiscoveryService(
        repository,
        profile=profile,
    )
    application.state.application_service = ApplicationService(
        application.state.repository,
        project_root=application.state.project_root,
        session_manager=SessionManager(
            application.state.project_root / "data" / "auth",
            project_root=application.state.project_root,
            browser_channel="chrome",
        ),
    )
    application.state.session_manager = application.state.application_service.session_manager
    from job_automation.outreach.linkedin_automation import LinkedInOutreachService

    application.state.linkedin_outreach_service = LinkedInOutreachService(
        application.state.repository, project_root=project_root,
        session_manager=application.state.session_manager,
    )
    from job_automation.pipeline import AutomationPipeline
    application.state.automation_pipeline = AutomationPipeline(
        application.state.repository, project_root=project_root,
        applications=application.state.application_service,
        email=application.state.outreach_email_service,
        linkedin=application.state.linkedin_outreach_service,
    )
    application.include_router(router)
    return application


app = create_app()
