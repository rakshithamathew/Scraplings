"""Configuration-driven job discovery and persistence service."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from job_automation.database import (
    DEFAULT_DATABASE_URL,
    DuplicateJobError,
    Job,
    JobRepository,
    JobStatus,
    initialize_database,
)
from job_automation.matching.ranker import RankedJob, rank_jobs
from job_automation.normalizer import NormalizedJob, normalize_job
from job_automation.matching.ats_score import UserProfile
from job_automation.matching.requirements import evaluate_hard_constraints
from job_automation.scraper.base import BaseJobScraper
from job_automation.scraper.company_careers import CareerPageSelectors, GenericCareerScraper
from job_automation.scraper.greenhouse import GreenhouseScraper
from job_automation.scraper.lever import LeverScraper
from job_automation.scraper.linkedin import LinkedInScraper
from job_automation.scraper.naukri import NaukriScraper
from job_automation.scraper.workday import WorkdayScraper, WorkdaySiteConfig


LOGGER = logging.getLogger(__name__)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "job_sources.json"


class SourceConfig(BaseModel):
    """Validated configuration for one public job source."""

    model_config = ConfigDict(extra="forbid")

    type: str
    company: str | None = None
    url: str
    enabled: bool = True
    limit: int | None = None
    timeout: float = 30.0
    request_delay: float = 0.5
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.casefold().strip()
        if not normalized:
            raise ValueError("source type cannot be empty")
        return normalized

    @field_validator("url")
    @classmethod
    def require_url(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source URL cannot be empty")
        return value.strip()

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("source limit cannot be negative")
        return value


class SourcesFile(BaseModel):
    """Top-level job-sources configuration document."""

    model_config = ConfigDict(extra="forbid")
    sources: list[SourceConfig]


@dataclass(slots=True)
class DiscoverySummary:
    sources_checked: int = 0
    jobs_discovered: int = 0
    new_jobs: int = 0
    duplicates: int = 0
    failed_sources: int = 0
    remote_eligible: int = 0
    bengaluru_hybrid: int = 0
    bengaluru_onsite: int = 0
    filtered: int = 0


@dataclass(slots=True)
class AutomationCycleSummary:
    discovery: DiscoverySummary
    jobs_scored: int = 0
    ats_over_70: int = 0
    successfully_applied: int = 0
    failed: int = 0


class UnsupportedSourceError(ValueError):
    """Raised when a configured source cannot be handled safely."""


ScraperFactory = Callable[[SourceConfig], BaseJobScraper]


def load_sources(path: str | Path = DEFAULT_CONFIG_PATH) -> list[SourceConfig]:
    """Read and validate a JSON source configuration."""
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Job source configuration not found: {config_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read job source configuration {config_path}: {error}") from error
    try:
        return SourcesFile.model_validate(payload).sources
    except ValidationError as error:
        raise ValueError(f"Invalid job source configuration {config_path}: {error}") from error


def _workday_config(source: SourceConfig) -> WorkdaySiteConfig | None:
    options = source.options
    if not options.get("search_url"):
        return None
    return WorkdaySiteConfig(
        careers_url=source.url,
        company=source.company,
        tenant=options.get("tenant"),
        site_id=options.get("site_id"),
        locale=options.get("locale"),
        search_url=options["search_url"],
        detail_url_template=options.get("detail_url_template"),
        discovery_method=str(options.get("discovery_method", "POST")).upper(),  # type: ignore[arg-type]
        job_list_paths=tuple(options.get("job_list_paths", ("jobPostings", "data.jobPostings"))),
        detail_root_paths=tuple(options.get("detail_root_paths", ("jobPostingInfo", "data.jobPostingInfo"))),
        search_payload=options.get("search_payload", {}),
        page_size=int(options.get("page_size", 20)),
        max_pages=int(options.get("max_pages", 3)),
    )


def build_scraper(source: SourceConfig) -> BaseJobScraper:
    """Create a supported scraper without importing any persistence concerns."""
    common = {
        "company": source.company,
        "timeout": source.timeout,
        "request_delay": source.request_delay,
    }
    if source.type == "greenhouse":
        return GreenhouseScraper(
            source.url,
            board_token=source.options.get("board_token"),
            **common,
        )
    if source.type == "lever":
        return LeverScraper(
            source.url,
            site=source.options.get("site"),
            **common,
        )
    if source.type == "linkedin":
        return LinkedInScraper(
            source.url,
            queries=source.options.get("queries", ()),
            searches=source.options.get("searches"),
            date_posted_seconds=source.options.get("date_posted_seconds", 604800),
            max_pages=int(source.options.get("max_pages", 1)),
            page_size=int(source.options.get("page_size", 25)),
            **common,
        )
    if source.type == "naukri":
        return NaukriScraper(
            source.url,
            queries=source.options.get("queries", ()),
            searches=source.options.get("searches"),
            max_pages=int(source.options.get("max_pages", 1)),
            browser_wait_ms=int(source.options.get("browser_wait_ms", 2500)),
            **common,
        )
    if source.type == "workday":
        scraper = WorkdayScraper(
            source.url,
            config=_workday_config(source),
            **common,
        )
        if not scraper.config.supported:
            raise UnsupportedSourceError(
                scraper.config.unsupported_reason or "unsupported Workday configuration"
            )
        return scraper
    if source.type in {"generic", "company_careers"}:
        selector_values = source.options.get("selectors", {})
        try:
            selectors = CareerPageSelectors(**selector_values)
        except TypeError as error:
            raise UnsupportedSourceError(f"invalid generic selectors: {error}") from error
        return GenericCareerScraper(
            source.url,
            company=source.company,
            source="company_careers",
            selectors=selectors,
            timeout=source.timeout,
        )
    raise UnsupportedSourceError(f"unsupported source type: {source.type}")


class JobDiscoveryService:
    """Run configured scrapers and synchronize their normalized jobs to SQLite."""

    def __init__(
        self,
        repository: JobRepository,
        *,
        scraper_factory: ScraperFactory = build_scraper,
        profile: UserProfile | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.repository = repository
        self.scraper_factory = scraper_factory
        self.profile = profile
        self.logger = logger or LOGGER

    @staticmethod
    def _persistence_values(job: NormalizedJob) -> dict[str, Any] | None:
        if not job.company or not job.title or not job.application_url or not job.source:
            return None
        return {
            "external_id": job.external_id,
            "title": job.title,
            "company": job.company,
            "location": job.location,
            "workplace_type": job.workplace_type,
            "description": job.description,
            "skills": job.skills or [],
            "required_skills": job.required_skills or [],
            "preferred_skills": job.preferred_skills or [],
            "minimum_experience": job.minimum_experience,
            "maximum_experience": job.maximum_experience,
            "is_open": job.is_open,
            "source": job.source,
            "source_url": job.source_url,
            "application_url": job.application_url,
            "posted_at": job.posted_at,
            "scraped_at": datetime.now(timezone.utc),
        }

    @staticmethod
    def _changed_fields(existing: Job, values: Mapping[str, Any]) -> dict[str, Any]:
        changes: dict[str, Any] = {"scraped_at": values["scraped_at"]}
        for field in (
            "location",
            "workplace_type",
            "description",
            "skills",
            "required_skills",
            "preferred_skills",
            "minimum_experience",
            "maximum_experience",
            "is_open",
            "posted_at",
        ):
            incoming = values.get(field)
            if incoming not in (None, "", []) and getattr(existing, field) != incoming:
                changes[field] = incoming
        if existing.source == values["source"]:
            for field in ("external_id", "source_url"):
                incoming = values.get(field)
                if incoming not in (None, "") and getattr(existing, field) != incoming:
                    changes[field] = incoming
        return changes

    def _save_job(self, job: NormalizedJob, summary: DiscoverySummary) -> None:
        values = self._persistence_values(job)
        if values is None:
            self.logger.warning(
                "Skipping incomplete %s job (source, company, title, and application URL are required)",
                job.source or "unknown",
            )
            return

        status = JobStatus.DISCOVERED
        skip_reason: str | None = None
        if self.profile is not None:
            decision = evaluate_hard_constraints(job, target_titles=self.profile.target_titles)
            if decision.eligible and decision.category:
                setattr(summary, decision.category, getattr(summary, decision.category) + 1)
            elif not decision.eligible:
                status = JobStatus.SKIPPED
                skip_reason = decision.reason
                summary.filtered += 1

        duplicate = self.repository.find_duplicate(
            values["company"],
            values["title"],
            values["application_url"],
            external_id=values["external_id"],
            source=values["source"],
        )
        if duplicate is not None:
            summary.duplicates += 1
            changes = self._changed_fields(duplicate, values)
            if duplicate.status is not JobStatus.APPLIED and status is JobStatus.SKIPPED:
                changes.update(status=status, skip_reason=skip_reason)
            self.repository.update_job(duplicate.id, **changes)
            return
        try:
            self.repository.create_job(**values, status=status, skip_reason=skip_reason)
            summary.new_jobs += 1
        except DuplicateJobError:
            summary.duplicates += 1

    async def run(self, sources: Sequence[SourceConfig]) -> DiscoverySummary:
        summary = DiscoverySummary()
        for source in sources:
            if not source.enabled:
                continue
            summary.sources_checked += 1
            try:
                scraper = self.scraper_factory(source)
                returned = await scraper.search_jobs(limit=source.limit)
            except Exception:
                summary.failed_sources += 1
                self.logger.exception(
                    "Job source failed: type=%s company=%s url=%s",
                    source.type,
                    source.company or "",
                    source.url,
                )
                continue

            summary.jobs_discovered += len(returned)
            for raw_job in returned:
                try:
                    if isinstance(raw_job, NormalizedJob):
                        job = normalize_job(raw_job.model_dump())
                    elif isinstance(raw_job, Mapping):
                        job = normalize_job(raw_job)
                    else:
                        self.logger.warning("Ignoring non-job result from %s", source.url)
                        continue
                    # A job returned by a successful live listing request is open at
                    # discovery time unless the source explicitly says otherwise.
                    if job.is_open is None:
                        job = job.model_copy(update={"is_open": True})
                    self._save_job(job, summary)
                except Exception:
                    self.logger.exception("Unable to process one job from %s", source.url)
        return summary


async def run_complete_cycle(
    repository: JobRepository,
    sources: Sequence[SourceConfig],
    profile: UserProfile,
    active_resume_path: str,
) -> tuple[AutomationCycleSummary, list[RankedJob]]:
    """Run discovery and active-resume scoring; applications remain a separate action."""
    discovery = await JobDiscoveryService(repository, profile=profile).run(sources)
    ranked = rank_jobs(repository, profile, active_resume_path)
    return (
        AutomationCycleSummary(
            discovery=discovery,
            jobs_scored=len(ranked),
            ats_over_70=sum(item.breakdown.total > 70 for item in ranked),
            successfully_applied=0,
            failed=discovery.failed_sources,
        ),
        ranked,
    )


def print_summary(summary: DiscoverySummary) -> None:
    print(f"sources checked: {summary.sources_checked}")
    print(f"jobs discovered: {summary.jobs_discovered}")
    print(f"new jobs: {summary.new_jobs}")
    print(f"duplicates: {summary.duplicates}")
    print(f"failed sources: {summary.failed_sources}")
    print(f"remote eligible: {summary.remote_eligible}")
    print(f"Bengaluru hybrid: {summary.bengaluru_hybrid}")
    print(f"Bengaluru onsite: {summary.bengaluru_onsite}")
    print(f"filtered: {summary.filtered}")


def print_cycle_summary(summary: AutomationCycleSummary) -> None:
    discovery = summary.discovery
    print(f"sources checked: {discovery.sources_checked}")
    print(f"jobs discovered: {discovery.jobs_discovered}")
    print(f"remote eligible: {discovery.remote_eligible}")
    print(f"Bengaluru hybrid: {discovery.bengaluru_hybrid}")
    print(f"Bengaluru onsite: {discovery.bengaluru_onsite}")
    print(f"duplicates: {discovery.duplicates}")
    print(f"jobs scored: {summary.jobs_scored}")
    print(f"ATS > 70: {summary.ats_over_70}")
    print(f"successfully applied: {summary.successfully_applied}")
    print(f"failed: {summary.failed}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    sources = load_sources(args.config)
    engine = initialize_database(args.database_url)
    try:
        repository = JobRepository(engine)
        active = repository.get_active_resume()
        if active is None:
            raise ValueError("Upload an active resume before running discovery and scoring")
        from job_automation.resume import ParsedCandidateProfile

        profile = ParsedCandidateProfile.model_validate(active.parsed_profile).to_user_profile()
        summary, _ = await run_complete_cycle(repository, sources, profile, active.path)
    finally:
        engine.dispose()
    print_cycle_summary(summary)
    return 1 if summary.failed else 0


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("scrapling").propagate = False
    try:
        return asyncio.run(async_main(args))
    except ValueError as error:
        LOGGER.error("%s", error)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
