"""Score persisted jobs and print a deterministic ranking."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from job_automation.database import (
    DEFAULT_DATABASE_URL,
    Job,
    JobRepository,
    JobStatus,
    initialize_database,
)
from job_automation.matching.ats_score import ScoreBreakdown, UserProfile, score_job
from job_automation.normalizer import NormalizedJob
from job_automation.resume import ResumeSelector


LOGGER = logging.getLogger(__name__)
DEFAULT_PROFILE_PATH = Path(__file__).resolve().parents[2] / "config" / "profile.json"
_PRESERVED_STATUSES = {
    JobStatus.READY_TO_APPLY,
    JobStatus.APPLIED,
    JobStatus.REJECTED,
    JobStatus.INTERVIEW,
    JobStatus.OFFER,
    JobStatus.SKIPPED,
}


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: Job
    breakdown: ScoreBreakdown


def load_profile(path: str | Path = DEFAULT_PROFILE_PATH) -> UserProfile:
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Profile configuration not found: {profile_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read profile configuration {profile_path}: {error}") from error
    try:
        return UserProfile.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"Invalid profile configuration {profile_path}: {error}") from error


def _normalized_job(job: Job) -> NormalizedJob:
    return NormalizedJob(
        external_id=job.external_id,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        skills=job.skills,
        source=job.source,
        source_url=job.source_url,
        application_url=job.application_url,
        posted_at=job.posted_at,
    )


def _status_for_score(job: Job, score: float) -> JobStatus:
    if job.status in _PRESERVED_STATUSES:
        return job.status
    return JobStatus.QUALIFIED if score >= 80 else JobStatus.SCORED


def rank_jobs(
    repository: JobRepository,
    profile: UserProfile,
    resume_selector: ResumeSelector | None = None,
) -> list[RankedJob]:
    """Score every persisted job, update its score/status, and return best first."""
    selector = resume_selector or ResumeSelector.from_file()
    ranked: list[RankedJob] = []
    for job in repository.get_jobs():
        breakdown = score_job(_normalized_job(job), profile)
        status = _status_for_score(job, breakdown.total)
        changes: dict[str, object] = {"match_score": breakdown.total, "status": status}
        if status is JobStatus.QUALIFIED:
            selection = selector.select(job)
            changes["recommended_resume"] = selection.resume.path if selection else None
        elif status is JobStatus.SCORED:
            changes["recommended_resume"] = None
        updated = repository.update_job(job.id, **changes)
        if updated is not None:
            ranked.append(RankedJob(updated, breakdown))
    return sorted(ranked, key=lambda item: (-item.breakdown.total, item.job.company.casefold(), item.job.title.casefold()))


def print_rankings(ranked: list[RankedJob], *, limit: int = 20) -> None:
    print(f"top {min(limit, len(ranked))} jobs")
    for index, item in enumerate(ranked[:limit], start=1):
        score = item.breakdown
        location = item.job.location or "Location unavailable"
        print(f"{index:>2}. {score.total:>6.2f} | {item.job.company} | {item.job.title} | {location}")
        print(
            "    "
            f"title={score.title:.1f}/30 skills={score.skills:.1f}/30 "
            f"keywords={score.keywords:.1f}/15 location={score.location:.1f}/15 "
            f"experience={score.experience:.1f}/10 penalty=-{score.excluded_penalty:.1f} "
            f"status={item.job.status.value}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.limit < 0:
        LOGGER.error("limit cannot be negative")
        return 2
    try:
        profile = load_profile(args.profile)
        engine = initialize_database(args.database_url)
    except ValueError as error:
        LOGGER.error("%s", error)
        return 2
    try:
        ranked = rank_jobs(JobRepository(engine), profile)
    finally:
        engine.dispose()
    print_rankings(ranked, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
