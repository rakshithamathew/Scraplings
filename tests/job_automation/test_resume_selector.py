from __future__ import annotations

import json
from pathlib import Path

from job_automation.database import JobRepository, JobStatus, initialize_database
from job_automation.matching import UserProfile
from job_automation.matching.ranker import rank_jobs
from job_automation.normalizer import NormalizedJob
from job_automation.resume import ResumeConfig, ResumeMetadata, ResumeSelector, load_resume_config


def _selector(tmp_path: Path) -> ResumeSelector:
    resume_directory = tmp_path / "resumes"
    resume_directory.mkdir()
    (resume_directory / "frontend.pdf").touch()
    (resume_directory / "fullstack.pdf").touch()
    return ResumeSelector(
        ResumeConfig(
            resumes=[
                ResumeMetadata(
                    name="Full Stack",
                    path="resumes/fullstack.pdf",
                    skills=["Python", "React", "SQL"],
                    target_roles=["Full Stack Engineer", "Software Engineer"],
                    keywords=["backend", "API"],
                ),
                ResumeMetadata(
                    name="Frontend",
                    path="resumes/frontend.pdf",
                    skills=["React", "TypeScript", "CSS"],
                    target_roles=["Frontend Engineer"],
                    keywords=["user interface", "frontend"],
                ),
                ResumeMetadata(
                    name="Missing",
                    path="resumes/missing.pdf",
                    skills=["React"],
                    target_roles=["Frontend Engineer"],
                    keywords=["frontend"],
                ),
            ]
        ),
        project_root=tmp_path,
    )


def test_load_resume_config(tmp_path: Path) -> None:
    path = tmp_path / "resumes.json"
    path.write_text(
        json.dumps(
            {
                "resumes": [
                    {
                        "name": "Frontend",
                        "path": "resumes/frontend.pdf",
                        "skills": [" React "],
                        "target_roles": ["Frontend Engineer"],
                        "keywords": ["UI"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    config = load_resume_config(path)

    assert config.resumes[0].skills == ["React"]


def test_selector_chooses_best_existing_resume(tmp_path: Path) -> None:
    job = NormalizedJob(
        title="Senior Frontend Engineer",
        description="Build a React and TypeScript user interface.",
        skills=["React", "TypeScript", "CSS"],
        source="test",
        application_url="https://example.test/frontend",
    )

    selection = _selector(tmp_path).select(job)

    assert selection is not None
    assert selection.resume.name == "Frontend"
    assert selection.resume.path == "resumes/frontend.pdf"
    assert selection.score == 100


def test_selector_returns_none_when_only_matching_file_is_missing(tmp_path: Path) -> None:
    resume_directory = tmp_path / "resumes"
    resume_directory.mkdir()
    selector = ResumeSelector(
        ResumeConfig(
            resumes=[
                ResumeMetadata(
                    name="Unavailable",
                    path="resumes/unavailable.pdf",
                    skills=["Rust"],
                    target_roles=["Rust Engineer"],
                    keywords=[],
                )
            ]
        ),
        project_root=tmp_path,
    )

    selection = selector.select(
        NormalizedJob(
            title="Rust Engineer",
            source="test",
            application_url="https://example.test/rust",
        )
    )

    assert selection is None


def test_ranker_stores_recommended_resume_for_qualified_job(tmp_path: Path) -> None:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    repository = JobRepository(engine)
    try:
        job = repository.create_job(
            title="Frontend Engineer",
            company="Example",
            location="Remote",
            description="Build frontend user interfaces with React, TypeScript, and CSS.",
            skills=["React", "TypeScript", "CSS"],
            source="test",
            application_url="https://example.test/jobs/frontend",
        )
        profile = UserProfile(
            target_titles=["Frontend Engineer"],
            skills=["React", "TypeScript", "CSS"],
            preferred_locations=["Remote"],
            keywords=["frontend", "user interface"],
        )

        rank_jobs(repository, profile, _selector(tmp_path))

        stored = repository.get_job(job.id)
        assert stored is not None
        assert stored.status is JobStatus.QUALIFIED
        assert stored.recommended_resume == "resumes/frontend.pdf"
    finally:
        engine.dispose()
