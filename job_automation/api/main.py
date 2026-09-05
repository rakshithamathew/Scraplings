"""Minimal local FastAPI backend for job discovery and tracking."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field

from job_automation.database import (
    ApplicationMethod,
    ApplicationStatus,
    DEFAULT_DATABASE_URL,
    Job,
    JobRepository,
    JobStatus,
    OutreachStatus,
    initialize_database,
)
from job_automation.matching.ats_score import UserProfile
from job_automation.matching.ranker import load_profile, rank_jobs
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
    description: str | None
    skills: list[str]
    source: str
    source_url: str | None
    application_url: str
    posted_at: datetime | None
    scraped_at: datetime
    match_score: float | None
    status: JobStatus
    recommended_resume: str | None
    resume_used: str | None
    applied_at: datetime | None
    application_method: str | None
    application_status: ApplicationStatus | None
    contact_name: str | None
    contact_email: str | None
    outreach_status: OutreachStatus
    created_at: datetime
    updated_at: datetime


class StatusUpdate(BaseModel):
    status: JobStatus


class MarkAppliedRequest(BaseModel):
    resume_used: str = Field(min_length=1, max_length=2000)
    application_method: ApplicationMethod
    applied_at: datetime | None = None


class StatsResponse(BaseModel):
    total_jobs: int
    discovered: int
    qualified: int
    ready_to_apply: int
    applied: int
    interview: int
    rejected: int
    offer: int


class DiscoveryResponse(BaseModel):
    sources_checked: int
    jobs_discovered: int
    new_jobs: int
    duplicates: int
    failed_sources: int


class ScoreResponse(BaseModel):
    jobs_scored: int
    qualified: int
    scored: int


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
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    return repository.get_jobs(status=status, min_score=min_score, limit=limit, offset=offset)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int, repository: RepositoryDependency) -> Job:
    job = repository.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/jobs/{job_id}/status", response_model=JobResponse)
def patch_status(job_id: int, body: StatusUpdate, repository: RepositoryDependency) -> Job:
    job = repository.update_status(job_id, body.status)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/jobs/{job_id}/mark-applied", response_model=JobResponse)
def mark_applied(job_id: int, body: MarkAppliedRequest, repository: RepositoryDependency) -> Job:
    job = repository.mark_applied(
        job_id,
        resume_used=body.resume_used,
        application_method=body.application_method,
        applied_at=body.applied_at or datetime.now(timezone.utc),
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/stats", response_model=StatsResponse)
def stats(repository: RepositoryDependency) -> StatsResponse:
    counts = repository.get_status_counts()
    return StatsResponse(
        total_jobs=repository.count_jobs(),
        discovered=counts[JobStatus.DISCOVERED],
        qualified=counts[JobStatus.QUALIFIED],
        ready_to_apply=counts[JobStatus.READY_TO_APPLY],
        applied=counts[JobStatus.APPLIED],
        interview=counts[JobStatus.INTERVIEW],
        rejected=counts[JobStatus.REJECTED],
        offer=counts[JobStatus.OFFER],
    )


@router.post("/scrape", response_model=DiscoveryResponse)
async def scrape(request: Request, repository: RepositoryDependency) -> DiscoverySummary:
    try:
        sources = request.app.state.sources_loader()
        service = request.app.state.discovery_service_factory(repository)
        return await service.run(sources)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.post("/score", response_model=ScoreResponse)
def score(request: Request, repository: RepositoryDependency) -> ScoreResponse:
    try:
        profile: UserProfile = request.app.state.profile_loader()
        ranked = rank_jobs(repository, profile)
    except ValueError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return ScoreResponse(
        jobs_scored=len(ranked),
        qualified=sum(item.job.status is JobStatus.QUALIFIED for item in ranked),
        scored=sum(item.job.status is JobStatus.SCORED for item in ranked),
    )


def create_app(database_url: str = DEFAULT_DATABASE_URL) -> FastAPI:
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
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )
    application.state.repository = JobRepository(engine)
    application.state.sources_loader = load_sources
    application.state.profile_loader = load_profile
    application.state.discovery_service_factory = JobDiscoveryService
    application.include_router(router)
    return application


app = create_app()
