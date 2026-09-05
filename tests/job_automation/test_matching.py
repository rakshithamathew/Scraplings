from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_automation.database import JobRepository, JobStatus, initialize_database
from job_automation.matching import UserProfile, score_job
from job_automation.matching.ranker import load_profile, rank_jobs
from job_automation.matching.requirements import (
    ExperienceRequirement,
    experience_compatibility,
    extract_experience_requirement,
)
from job_automation.normalizer import NormalizedJob


@pytest.fixture
def profile() -> UserProfile:
    return UserProfile(
        target_titles=["Python Engineer"],
        skills=["Python", "SQL"],
        preferred_locations=["Remote"],
        minimum_experience=2,
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
        source="test",
        application_url="https://example.test/jobs/1",
    )

    score = score_job(job, profile)

    assert score.title == 30
    assert score.skills == 30
    assert score.keywords == 15
    assert score.location == 15
    assert score.experience == 10
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

    assert score.excluded_penalty == 40
    assert 0 <= score.total <= 100
    assert score.matched_excluded_keywords == ("manager", "director")


def test_load_profile_validates_experience_range(tmp_path: Path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "target_titles": [],
                "skills": [],
                "preferred_locations": [],
                "minimum_experience": 5,
                "maximum_experience": 2,
                "keywords": [],
                "excluded_keywords": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="minimum_experience cannot exceed"):
        load_profile(path)


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
        source="test",
        application_url="https://example.test/strong",
    )
    weak = repository.create_job(
        title="Accountant",
        company="Weak Co",
        location="London",
        description="Prepare financial statements.",
        skills=[],
        source="test",
        application_url="https://example.test/weak",
    )
    applied = repository.create_job(
        title="Python Engineer",
        company="Applied Co",
        location="Remote",
        description="Backend API work using Python and SQL. 3 years experience.",
        skills=["Python", "SQL"],
        source="test",
        application_url="https://example.test/applied",
        status=JobStatus.APPLIED,
    )

    ranked = rank_jobs(repository, profile)

    assert [item.job.id for item in ranked] == [applied.id, strong.id, weak.id]
    assert repository.get_job(strong.id).status is JobStatus.QUALIFIED  # type: ignore[union-attr]
    assert repository.get_job(weak.id).status is JobStatus.SCORED  # type: ignore[union-attr]
    assert repository.get_job(applied.id).status is JobStatus.APPLIED  # type: ignore[union-attr]
    assert repository.get_job(strong.id).match_score == 100  # type: ignore[union-attr]
