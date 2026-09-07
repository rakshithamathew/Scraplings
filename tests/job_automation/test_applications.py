from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.applications import (
    BrowserApplicationAgent,
    GreenhouseApplicationAgent,
    LeverApplicationAgent,
    WorkdayApplicationAgent,
    select_application_agent,
    store_application_plan,
)
from job_automation.database import (
    ApplicationMethod,
    ApplicationStatus,
    Job,
    JobRepository,
    JobStatus,
    WorkplaceType,
    initialize_database,
)


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    (resumes / "software.pdf").touch()
    return tmp_path


def _job(**overrides: object) -> Job:
    values: dict[str, object] = {
        "id": 42,
        "title": "Software Engineer",
        "company": "Example",
        "source": "greenhouse",
        "source_url": "https://boards.greenhouse.io/example/jobs/42",
        "application_url": "https://boards.greenhouse.io/example/jobs/42",
        "recommended_resume": "resumes/software.pdf",
        "match_score": 85,
        "status": JobStatus.QUALIFIED,
        "is_open": True,
        "workplace_type": WorkplaceType.REMOTE,
        "location": "Remote worldwide",
    }
    values.update(overrides)
    return Job(**values)


@pytest.mark.parametrize(
    ("job", "expected_type"),
    [
        (_job(), GreenhouseApplicationAgent),
        (
            _job(
                source="lever",
                source_url="https://jobs.lever.co/example/42",
                application_url="https://jobs.lever.co/example/42",
            ),
            LeverApplicationAgent,
        ),
        (
            _job(
                source="workday",
                source_url="https://example.wd5.myworkdayjobs.com/jobs/42",
                application_url="https://example.wd5.myworkdayjobs.com/jobs/42",
            ),
            WorkdayApplicationAgent,
        ),
        (
            _job(
                source="company",
                source_url="https://careers.example.test/jobs/42",
                application_url="https://careers.example.test/jobs/42/apply",
            ),
            BrowserApplicationAgent,
        ),
    ],
)
def test_provider_selection(job: Job, expected_type: type, project_root: Path) -> None:
    agent = select_application_agent(job, project_root=project_root)

    assert isinstance(agent, expected_type)


def test_prepare_application_uses_existing_recommended_resume(project_root: Path) -> None:
    agent = GreenhouseApplicationAgent(project_root=project_root)

    plan = agent.prepare_application(_job())

    assert plan.provider is ApplicationMethod.GREENHOUSE
    assert plan.application_status is ApplicationStatus.PREPARED
    assert plan.resume_path == "resumes/software.pdf"
    assert plan.requires_user_approval is True
    assert {field.name for field in plan.fields} >= {"first_name", "email", "resume", "custom_questions"}
    assert agent.validate_application(plan).valid is True


def test_missing_resume_requires_review(project_root: Path) -> None:
    agent = LeverApplicationAgent(project_root=project_root)
    job = _job(
        source="lever",
        source_url="https://jobs.lever.co/example/42",
        application_url="https://jobs.lever.co/example/42",
        recommended_resume="resumes/missing.pdf",
    )

    plan = agent.prepare_application(job)

    assert plan.application_status is ApplicationStatus.NEEDS_REVIEW
    assert plan.resume_path is None
    assert any("resume" in warning.casefold() for warning in plan.warnings)
    assert agent.validate_application(plan).valid is False


@pytest.mark.parametrize(
    ("changes", "warning"),
    [
        ({"match_score": 70}, "greater than 70"),
        ({"is_open": None}, "still open"),
        ({"description": "A coding assessment is mandatory."}, "assessment"),
        ({"location": "Hyderabad", "workplace_type": WorkplaceType.ONSITE}, "Bengaluru"),
    ],
)
def test_pre_application_gate_requires_review(
    project_root: Path,
    changes: dict[str, object],
    warning: str,
) -> None:
    plan = GreenhouseApplicationAgent(project_root=project_root).prepare_application(_job(**changes))

    assert plan.application_status is ApplicationStatus.NEEDS_REVIEW
    assert any(warning.casefold() in item.casefold() for item in plan.warnings)


def test_submission_is_always_disabled(project_root: Path) -> None:
    agent = BrowserApplicationAgent(project_root=project_root)
    plan = agent.prepare_application(
        _job(
            source="company",
            application_url="https://careers.example.test/jobs/42/apply",
        )
    )

    result = agent.submit_application(plan)

    assert result.submitted is False
    assert result.requires_user_approval is True
    assert result.application_status is ApplicationStatus.NEEDS_REVIEW


def test_plan_storage_uses_repository(project_root: Path, tmp_path: Path) -> None:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'applications.db').as_posix()}")
    repository = JobRepository(engine)
    try:
        job = repository.create_job(
            title="Software Engineer",
            company="Example",
            source="greenhouse",
            source_url="https://boards.greenhouse.io/example/jobs/42",
            application_url="https://boards.greenhouse.io/example/jobs/42",
            recommended_resume="resumes/software.pdf",
            match_score=85,
            status=JobStatus.QUALIFIED,
            is_open=True,
            workplace_type=WorkplaceType.REMOTE,
            location="Remote worldwide",
        )
        plan = GreenhouseApplicationAgent(project_root=project_root).prepare_application(job)

        stored = store_application_plan(repository, plan)

        assert stored is not None
        assert stored.application_method == "GREENHOUSE"
        assert stored.application_status is ApplicationStatus.PREPARED
        assert stored.status is JobStatus.QUALIFIED
    finally:
        engine.dispose()


def test_browser_adapter_rejects_non_public_url(project_root: Path) -> None:
    job = _job(source="company", source_url=None, application_url="file:///private/form")

    assert select_application_agent(job, project_root=project_root) is None
