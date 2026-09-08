from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from job_automation.database import (
    DuplicateJobError,
    JobRepository,
    JobStatus,
    initialize_database,
)


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    database_url = f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}"
    engine = initialize_database(database_url)
    initialize_database(engine=engine)
    try:
        yield JobRepository(engine)
    finally:
        engine.dispose()


@pytest.fixture
def job_data() -> dict:
    return {
        "external_id": "gh-123",
        "title": "Senior Python Engineer",
        "company": "Example Labs",
        "location": "Remote",
        "description": "Build reliable data services.",
        "skills": ["Python", "SQL"],
        "source": "greenhouse",
        "source_url": "https://boards.example.test/jobs/123",
        "application_url": "https://boards.example.test/jobs/123/apply/",
    }


def test_create_job(repository: JobRepository, job_data: dict) -> None:
    job = repository.create_job(**job_data)

    assert job.id is not None
    assert job.status is JobStatus.DISCOVERED
    assert job.outreach_status.value == "NOT_STARTED"
    assert job.skills == ["Python", "SQL"]
    assert job.application_url == "https://boards.example.test/jobs/123/apply"
    assert job.created_at is not None
    assert job.updated_at is not None


def test_retrieve_job(repository: JobRepository, job_data: dict) -> None:
    created = repository.create_job(**job_data)

    retrieved = repository.get_job(created.id)
    jobs = repository.get_jobs()

    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.title == "Senior Python Engineer"
    assert [job.id for job in jobs] == [created.id]
    assert repository.job_exists(job_id=created.id)
    assert repository.job_exists(external_id="gh-123", source="greenhouse")


def test_update_status(repository: JobRepository, job_data: dict) -> None:
    created = repository.create_job(**job_data)

    updated = repository.update_status(created.id, JobStatus.QUALIFIED)

    assert updated is not None
    assert updated.status is JobStatus.QUALIFIED
    assert repository.get_job(created.id).status is JobStatus.QUALIFIED  # type: ignore[union-attr]


def test_duplicate_detection(repository: JobRepository, job_data: dict) -> None:
    created = repository.create_job(**job_data)

    duplicate = repository.find_duplicate(
        company="  EXAMPLE   labs ",
        title="senior python engineer",
        application_url="https://boards.example.test/jobs/123/apply/",
    )

    assert duplicate is not None
    assert duplicate.id == created.id
    assert repository.job_exists(
        company="example labs",
        title="Senior Python Engineer",
        application_url="https://boards.example.test/jobs/123/apply",
    )

    with pytest.raises(DuplicateJobError):
        repository.create_job(
            **{
                **job_data,
                "external_id": "different-source-id",
                "company": "EXAMPLE LABS",
                "title": " Senior   Python Engineer ",
            }
        )


def test_initialization_adds_recommended_resume_to_existing_database(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}"
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jobs (id INTEGER PRIMARY KEY, title TEXT NOT NULL)"))
        connection.execute(text("INSERT INTO jobs (id, title) VALUES (1, 'Existing Job')"))

    initialize_database(engine=engine)

    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    with engine.connect() as connection:
        title = connection.scalar(text("SELECT title FROM jobs WHERE id = 1"))
    assert {"recommended_resume", "application_status"}.issubset(columns)
    assert title == "Existing Job"
    engine.dispose()


def test_active_resume_is_singleton(repository: JobRepository) -> None:
    first = repository.set_active_resume(
        original_filename="first.pdf",
        path="resumes/first.pdf",
        content_type="application/pdf",
        sha256="a" * 64,
        parsed_profile={"skills": ["React"]},
    )
    second = repository.set_active_resume(
        original_filename="second.docx",
        path="resumes/second.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        sha256="b" * 64,
        parsed_profile={"skills": ["Angular"]},
    )

    active = repository.get_active_resume()
    assert first.id == second.id == 1
    assert active is not None
    assert active.original_filename == "second.docx"
    assert active.parsed_profile["skills"] == ["Angular"]


def test_needs_review_is_separate_from_actionable_qualified(
    repository: JobRepository,
    job_data: dict,
) -> None:
    job = repository.create_job(**job_data)
    repository.update_status(job.id, JobStatus.QUALIFIED)
    repository.mark_application_needs_review(job.id, "Linkedin is not connected (disconnected)")

    assert repository.count_needs_review() == 1
    assert repository.count_actionable_qualified() == 0
    assert repository.get_jobs(status=JobStatus.QUALIFIED, exclude_needs_review=True) == []

    assert repository.clear_connection_reviews("linkedin") == 1
    assert repository.count_needs_review() == 0
    assert repository.count_actionable_qualified() == 1
