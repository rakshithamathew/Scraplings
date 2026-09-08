"""Score persisted jobs and print a deterministic ranking."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from datetime import timezone

from job_automation.database import (
    DEFAULT_DATABASE_URL,
    Job,
    JobRepository,
    JobStatus,
    initialize_database,
)
from job_automation.matching.ats_score import ScoreBreakdown, UserProfile, score_job
from job_automation.matching.requirements import evaluate_hard_constraints
from job_automation.normalizer import NormalizedJob


LOGGER = logging.getLogger(__name__)
_PRESERVED_STATUSES = {
    JobStatus.APPLIED,
    JobStatus.SKIPPED,
}


@dataclass(frozen=True, slots=True)
class RankedJob:
    job: Job
    breakdown: ScoreBreakdown


def _normalized_job(job: Job) -> NormalizedJob:
    return NormalizedJob(
        external_id=job.external_id,
        title=job.title,
        company=job.company,
        location=job.location,
        workplace_type=job.workplace_type,
        description=job.description,
        skills=job.skills,
        required_skills=job.required_skills,
        preferred_skills=job.preferred_skills,
        minimum_experience=job.minimum_experience,
        maximum_experience=job.maximum_experience,
        is_open=job.is_open,
        source=job.source,
        source_url=job.source_url,
        application_url=job.application_url,
        posted_at=job.posted_at,
    )


def _status_for_score(job: Job, score: float) -> JobStatus:
    if job.status in _PRESERVED_STATUSES:
        return job.status
    return JobStatus.QUALIFIED if score > 70 else JobStatus.DISCOVERED


def rank_jobs(
    repository: JobRepository,
    profile: UserProfile,
    active_resume_path: str | None = None,
    *, job_ids: list[int] | None = None, rescore_applied: bool = False,
) -> list[RankedJob]:
    """Score DISCOVERED/QUALIFIED jobs while preserving APPLIED and SKIPPED rows."""
    ranked: list[RankedJob] = []
    jobs = repository.get_jobs() if job_ids is None else [repository.get_job(job_id) for job_id in dict.fromkeys(job_ids)]
    for job in jobs:
        if job is None:
            continue
        if job.status in _PRESERVED_STATUSES and not (rescore_applied and job.status is JobStatus.APPLIED):
            continue
        normalized = _normalized_job(job)
        eligibility = evaluate_hard_constraints(normalized, target_titles=profile.target_titles)
        if not eligibility.eligible and job.status is JobStatus.APPLIED:
            continue
        if not eligibility.eligible and job.status is not JobStatus.APPLIED:
            repository.update_job(job.id, status=JobStatus.SKIPPED, skip_reason=eligibility.reason)
            LOGGER.info("job_filtered job_id=%s reason=%s", job.id, eligibility.reason)
            continue
        breakdown = score_job(normalized, profile)
        status = _status_for_score(job, breakdown.total)
        changes: dict[str, object] = {
            "match_score": breakdown.total,
            "matched_skills": list(breakdown.matched_skills),
            "missing_skills": list(breakdown.missing_required_skills),
            "score_reason": breakdown.reason,
            "status": status,
        }
        if status in {JobStatus.QUALIFIED, JobStatus.APPLIED}:
            changes["recommended_resume"] = active_resume_path
            changes["resume_match_reason"] = (
                "Active uploaded resume used for ATS scoring" if active_resume_path else "No active resume"
            )
        else:
            changes["recommended_resume"] = None
            changes["resume_match_reason"] = None
        updated = repository.update_job(job.id, **changes)
        if updated is not None:
            ranked.append(RankedJob(updated, breakdown))
            LOGGER.info(
                "score_calculated job_id=%s score=%.2f status=%s",
                job.id,
                breakdown.total,
                updated.status.value,
            )
    def sort_key(item: RankedJob) -> tuple[float, float, str, str]:
        posted = item.job.posted_at
        if posted is not None and posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        posted_value = posted.timestamp() if posted is not None else 0.0
        return (-item.breakdown.total, -posted_value, item.job.company.casefold(), item.job.title.casefold())

    return sorted(ranked, key=sort_key)


def print_rankings(ranked: list[RankedJob], *, limit: int = 20) -> None:
    print(f"top {min(limit, len(ranked))} jobs")
    for index, item in enumerate(ranked[:limit], start=1):
        score = item.breakdown
        location = item.job.location or "Location unavailable"
        print(f"{index:>2}. {score.total:>6.2f} | {item.job.company} | {item.job.title} | {location}")
        print(
            "    "
            f"title={score.title:.1f}/20 required={score.required_skills:.1f}/30 "
            f"experience={score.experience:.1f}/15 frontend={score.frontend_fullstack_relevance:.1f}/10 "
            f"preferred={score.preferred_skills:.1f}/10 domain={score.domain_relevance:.1f}/5 "
            f"location={score.location:.1f}/10 penalty=-{score.excluded_penalty:.1f} "
            f"status={item.job.status.value}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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
        engine = initialize_database(args.database_url)
    except ValueError as error:
        LOGGER.error("%s", error)
        return 2
    try:
        repository = JobRepository(engine)
        active_resume = repository.get_active_resume()
        if active_resume is None:
            raise ValueError("Upload an active resume before scoring")
        from job_automation.resume import ParsedCandidateProfile

        profile = ParsedCandidateProfile.model_validate(active_resume.parsed_profile).to_user_profile()
        ranked = rank_jobs(repository, profile, active_resume.path)
    except ValueError as error:
        LOGGER.error("%s", error)
        return 2
    finally:
        engine.dispose()
    print_rankings(ranked, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
