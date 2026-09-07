from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.database import JobRepository, JobStatus, initialize_database
from job_automation.matching import UserProfile, score_job
from job_automation.matching.ranker import rank_jobs
from job_automation.matching.requirements import (
    ExperienceRequirement,
    evaluate_hard_constraints,
    experience_compatibility,
    extract_experience_requirement,
)
from job_automation.normalizer import NormalizedJob, WorkplaceType


@pytest.fixture
def profile() -> UserProfile:
    return UserProfile(
        target_titles=["Python Engineer"],
        skills=["Python", "SQL"],
        preferred_skills=["backend", "API"],
        frontend_fullstack_skills=["Python", "SQL", "backend", "API"],
        domain_keywords=["API"],
        preferred_locations=["Remote"],
        minimum_experience=5,
        maximum_experience=5,
        keywords=["backend", "API"],
        excluded_keywords=["manager", "director"],
    )


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'matching.db').as_posix()}")
    try:
        yield JobRepository(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Requires 3-5 years of experience", ExperienceRequirement(3, 5)),
        ("At least 4 years experience", ExperienceRequirement(4, None)),
        ("2+ years of experience", ExperienceRequirement(2, None)),
        ("Up to 6 years experience", ExperienceRequirement(None, 6)),
        (None, None),
    ],
)
def test_extract_experience_requirement(
    text: str | None,
    expected: ExperienceRequirement | None,
) -> None:
    assert extract_experience_requirement(text) == expected


def test_experience_compatibility_is_neutral_when_job_is_unknown() -> None:
    assert experience_compatibility(None, 2, 5) == 0.5
    assert experience_compatibility(ExperienceRequirement(3, 4), 2, 5) == 1.0
    assert experience_compatibility(ExperienceRequirement(8, None), 2, 5) == 0.0


def test_score_job_has_explainable_fixed_components(profile: UserProfile) -> None:
    job = NormalizedJob(
        title="Senior Python Engineer",
        company="Example",
        location="Remote, India",
        description="Build backend API services with Python and SQL. Requires 3-5 years of experience.",
        skills=["Python", "SQL"],
        required_skills=["Python", "SQL"],
        preferred_skills=["backend", "API"],
        workplace_type=WorkplaceType.REMOTE,
        source="test",
        application_url="https://example.test/jobs/1",
    )

    score = score_job(job, profile)

    assert score.title == 20
    assert score.required_skills == 30
    assert score.preferred_skills == 10
    assert score.frontend_fullstack_relevance == 10
    assert score.domain_relevance == 5
    assert score.location == 10
    assert score.experience == 15
    assert score.excluded_penalty == 0
    assert score.total == 100
    assert score.matched_skills == ("Python", "SQL")


def test_excluded_keyword_penalty_and_score_floor(profile: UserProfile) -> None:
    job = NormalizedJob(
        title="Engineering Director",
        description="Manager and director role",
        source="test",
        application_url="https://example.test/jobs/2",
    )

    score = score_job(job, profile)

    assert score.excluded_penalty == 0
    assert 0 <= score.total <= 100
    assert score.matched_excluded_keywords == ("manager", "director")


def test_sparse_job_skills_are_not_divided_by_every_resume_skill() -> None:
    profile = UserProfile(
        target_titles=["Frontend Developer"],
        skills=["React", "TypeScript", "JavaScript", "CSS", "SQL", "Python", "Azure"],
        frontend_fullstack_skills=["React", "TypeScript", "JavaScript", "CSS"],
        preferred_locations=["Remote"],
    )
    job = NormalizedJob(
        title="Frontend Developer - React and TypeScript",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        description="Build React interfaces using TypeScript, JavaScript and CSS.",
        skills=["React"],
        source="test",
        application_url="https://example.test/jobs/frontend",
    )

    score = score_job(job, profile)

    assert score.required_skills == 30
    assert score.matched_skills == ("React", "TypeScript", "JavaScript", "CSS")
    assert score.total > 70


def test_ranker_updates_scores_statuses_and_preserves_applied(
    repository: JobRepository,
    profile: UserProfile,
) -> None:
    strong = repository.create_job(
        title="Python Engineer",
        company="Strong Co",
        location="Remote",
        description="Backend API work using Python and SQL. 3-5 years of experience.",
        skills=["Python", "SQL"],
        workplace_type=WorkplaceType.REMOTE,
        is_open=True,
        source="test",
        application_url="https://example.test/strong",
    )
    weak = repository.create_job(
        title="Accountant",
        company="Weak Co",
        location="London",
        description="Prepare financial statements.",
        skills=[],
        workplace_type=WorkplaceType.ONSITE,
        is_open=True,
        source="test",
        application_url="https://example.test/weak",
    )
    applied = repository.create_job(
        title="Python Engineer",
        company="Applied Co",
        location="Remote",
        description="Backend API work using Python and SQL. 3 years experience.",
        skills=["Python", "SQL"],
        workplace_type=WorkplaceType.REMOTE,
        is_open=True,
        source="test",
        application_url="https://example.test/applied",
        status=JobStatus.APPLIED,
    )

    ranked = rank_jobs(repository, profile)

    assert [item.job.id for item in ranked] == [strong.id]
    assert repository.get_job(strong.id).status is JobStatus.QUALIFIED  # type: ignore[union-attr]
    assert repository.get_job(weak.id).status is JobStatus.SKIPPED  # type: ignore[union-attr]
    assert repository.get_job(applied.id).status is JobStatus.APPLIED  # type: ignore[union-attr]
    assert repository.get_job(strong.id).match_score == 100  # type: ignore[union-attr]
    assert repository.get_job(strong.id).matched_skills == ["Python", "SQL"]  # type: ignore[union-attr]
    assert repository.get_job(strong.id).missing_skills == []  # type: ignore[union-attr]
    assert "Title" in repository.get_job(strong.id).score_reason  # type: ignore[operator,union-attr]
    assert repository.get_job(applied.id).match_score is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("job", "eligible", "category", "reason_fragment"),
    [
        (
            NormalizedJob(
                title="Senior Frontend Engineer",
                location="Worldwide remote",
                workplace_type=WorkplaceType.REMOTE,
                application_url="https://example.test/remote",
            ),
            True,
            "remote_eligible",
            None,
        ),
        (
            NormalizedJob(
                title="React Developer",
                location="Remote - US only",
                workplace_type=WorkplaceType.REMOTE,
                application_url="https://example.test/us-only",
            ),
            False,
            None,
            "excludes India",
        ),
        (
            NormalizedJob(
                title="React Developer",
                location="United States",
                workplace_type=WorkplaceType.REMOTE,
                application_url="https://example.test/us-location",
            ),
            False,
            None,
            "excludes India",
        ),
        (
            NormalizedJob(
                title="Frontend Lead",
                location="Bangalore, Karnataka",
                workplace_type=WorkplaceType.HYBRID,
                application_url="https://example.test/bangalore",
            ),
            True,
            "bengaluru_hybrid",
            None,
        ),
        (
            NormalizedJob(
                title="Frontend Engineer",
                location="Hyderabad",
                workplace_type=WorkplaceType.ONSITE,
                application_url="https://example.test/hyderabad",
            ),
            False,
            None,
            "outside Bengaluru",
        ),
        (
            NormalizedJob(
                title="Machine Learning Engineer",
                location="Remote worldwide",
                workplace_type=WorkplaceType.REMOTE,
                application_url="https://example.test/ml",
            ),
            False,
            None,
            "not aligned",
        ),
    ],
)
def test_mandatory_location_and_role_filter(
    job: NormalizedJob,
    eligible: bool,
    category: str | None,
    reason_fragment: str | None,
) -> None:
    result = evaluate_hard_constraints(
        job,
        target_titles=["Frontend Engineer", "React Developer", "Frontend Lead"],
    )

    assert result.eligible is eligible
    assert result.category == category
    if reason_fragment:
        assert reason_fragment in (result.reason or "")
