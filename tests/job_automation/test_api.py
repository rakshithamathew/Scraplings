from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_automation.api.main import create_app
from job_automation.database import JobRepository, JobStatus
from job_automation.matching import UserProfile
from job_automation.scraper.service import DiscoverySummary


@pytest.fixture
def api(tmp_path: Path) -> tuple[TestClient, JobRepository, dict[str, int]]:
    app = create_app(f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    repository: JobRepository = app.state.repository
    first = repository.create_job(
        title="Python Engineer",
        company="Example",
        location="Remote",
        description="Python backend API role requiring 3 years experience.",
        skills=["Python"],
        source="test",
        application_url="https://example.test/jobs/1",
        match_score=85,
        status=JobStatus.QUALIFIED,
        recommended_resume="resumes/fullstack_resume.pdf",
    )
    second = repository.create_job(
        title="Data Analyst",
        company="Other",
        location="London",
        description="Analyze reports using SQL.",
        skills=["SQL"],
        source="test",
        application_url="https://example.test/jobs/2",
        match_score=55,
        status=JobStatus.DISCOVERED,
    )
    with TestClient(app) as client:
        yield client, repository, {"first": first.id, "second": second.id}


def test_health(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, _, _ = api
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_jobs_list_and_filters(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, _, _ = api

    assert len(client.get("/jobs").json()) == 2
    qualified = client.get("/jobs", params={"status": "QUALIFIED"})
    high_scores = client.get("/jobs", params={"min_score": 80})

    assert qualified.status_code == 200
    assert [job["title"] for job in qualified.json()] == ["Python Engineer"]
    assert qualified.json()[0]["recommended_resume"] == "resumes/fullstack_resume.pdf"
    assert qualified.json()[0]["outreach_status"] == "NOT_STARTED"
    assert high_scores.status_code == 200
    assert [job["match_score"] for job in high_scores.json()] == [85.0]
    assert client.get("/jobs", params={"status": "INVALID"}).status_code == 422


def test_get_job_and_missing_job(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, _, ids = api
    assert client.get(f"/jobs/{ids['first']}").json()["title"] == "Python Engineer"
    assert client.get("/jobs/999999").status_code == 404


def test_patch_status(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, repository, ids = api
    response = client.patch(f"/jobs/{ids['second']}/status", json={"status": "READY_TO_APPLY"})

    assert response.status_code == 200
    assert response.json()["status"] == "READY_TO_APPLY"
    assert repository.get_job(ids["second"]).status is JobStatus.READY_TO_APPLY  # type: ignore[union-attr]

    response = client.patch(f"/jobs/{ids['second']}/status", json={"status": "SKIPPED"})

    assert response.status_code == 200
    assert response.json()["status"] == "SKIPPED"
    assert repository.get_job(ids["second"]).status is JobStatus.SKIPPED  # type: ignore[union-attr]


def test_mark_applied(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, repository, ids = api
    applied_at = "2026-09-05T10:30:00Z"
    response = client.post(
        f"/jobs/{ids['first']}/mark-applied",
        json={
            "resume_used": "resumes/backend.pdf",
            "application_method": "MANUAL",
            "applied_at": applied_at,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "APPLIED"
    assert response.json()["resume_used"] == "resumes/backend.pdf"
    stored = repository.get_job(ids["first"])
    assert stored is not None
    assert stored.status is JobStatus.APPLIED
    assert stored.application_method == "MANUAL"
    assert stored.application_status.value == "APPLIED"
    assert stored.applied_at is not None

    invalid = client.post(
        f"/jobs/{ids['second']}/mark-applied",
        json={"resume_used": "resume.pdf", "application_method": "AUTOMATIC"},
    )
    assert invalid.status_code == 422


def test_stats(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, _, _ = api
    response = client.get("/stats")

    assert response.status_code == 200
    assert response.json() == {
        "total_jobs": 2,
        "discovered": 1,
        "qualified": 1,
        "ready_to_apply": 0,
        "applied": 0,
        "interview": 0,
        "rejected": 0,
        "offer": 0,
    }


def test_scrape_endpoint_uses_discovery_service(
    api: tuple[TestClient, JobRepository, dict[str, int]],
) -> None:
    client, repository, _ = api

    class FakeDiscoveryService:
        def __init__(self, received_repository: JobRepository) -> None:
            assert received_repository is repository

        async def run(self, sources: object) -> DiscoverySummary:
            assert sources == ["configured-source"]
            return DiscoverySummary(1, 4, 2, 2, 0)

    client.app.state.sources_loader = lambda: ["configured-source"]  # type: ignore[attr-defined]
    client.app.state.discovery_service_factory = FakeDiscoveryService  # type: ignore[attr-defined]
    response = client.post("/scrape")

    assert response.status_code == 200
    assert response.json() == {
        "sources_checked": 1,
        "jobs_discovered": 4,
        "new_jobs": 2,
        "duplicates": 2,
        "failed_sources": 0,
    }


def test_score_endpoint_updates_database(api: tuple[TestClient, JobRepository, dict[str, int]]) -> None:
    client, repository, ids = api
    client.app.state.profile_loader = lambda: UserProfile(  # type: ignore[attr-defined]
        target_titles=["Python Engineer"],
        skills=["Python"],
        preferred_locations=["Remote"],
        minimum_experience=1,
        maximum_experience=5,
        keywords=["backend", "API"],
        excluded_keywords=[],
    )

    response = client.post("/score")

    assert response.status_code == 200
    assert response.json()["jobs_scored"] == 2
    assert repository.get_job(ids["first"]).match_score == 100  # type: ignore[union-attr]
    assert repository.get_job(ids["first"]).status is JobStatus.QUALIFIED  # type: ignore[union-attr]


def test_cors_allows_only_local_frontend(
    api: tuple[TestClient, JobRepository, dict[str, int]],
) -> None:
    client, _, _ = api
    allowed = client.options(
        "/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = client.options(
        "/jobs",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert blocked.status_code == 400
