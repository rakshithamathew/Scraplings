"""Sequential, dry-run-first browser application orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from job_automation.applications.base import (
    ApplicationNeedsReviewError,
    BaseApplicationAgent,
    application_eligibility_errors,
)
from job_automation.applications.candidate_profile import CandidateProfile, load_candidate_profile
from job_automation.applications.detector import (
    ApplicationProvider,
    detect_provider,
    detect_provider_from_url,
)
from job_automation.applications import select_application_agent
from job_automation.auth import SessionManager
from job_automation.database import ApplicationMethod, Job, JobRepository
from job_automation.matching.requirements import matched_terms
from job_automation.resume import ParsedCandidateProfile


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_APPLICATION_SETTINGS_PATH = PROJECT_ROOT / "config" / "application_settings.json"


class ApplicationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto_apply_enabled: bool = True
    minimum_ats_score: float = Field(default=70, ge=0, le=100)
    max_applications_per_run: int = Field(default=3, ge=1, le=20)
    delay_between_applications_seconds: float = Field(default=10, ge=0, le=300)
    headless: bool = False
    dry_run: bool = True
    navigation_timeout_seconds: float = Field(default=30, ge=5, le=120)
    browser_profile_directory: str = "data/application_browser_profile"
    browser_channel: str | None = None


class ApplicationCycleSummary(BaseModel):
    selected: int = 0
    eligible: int = 0
    ineligible: int = 0
    attempted: int = 0
    applied: int = 0
    needs_review: int = 0
    failed: int = 0
    skipped_duplicates: int = 0
    dry_run: bool = True
    started_at: datetime
    completed_at: datetime | None = None


def _environment_bool(name: str, fallback: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return fallback
    normalized = value.casefold().strip()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _environment_string(name: str, fallback: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else fallback


def load_application_settings(
    path: str | Path = DEFAULT_APPLICATION_SETTINGS_PATH,
) -> ApplicationSettings:
    settings_path = Path(path)
    try:
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        settings = ApplicationSettings.model_validate(payload)
    except FileNotFoundError as error:
        raise ValueError(f"Application settings not found: {settings_path}") from error
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid application settings {settings_path}: {error}") from error
    return settings.model_copy(
        update={
            "dry_run": _environment_bool("DRY_RUN", settings.dry_run),
            "headless": _environment_bool("APPLICATION_HEADLESS", settings.headless),
            "auto_apply_enabled": _environment_bool("AUTO_APPLY_ENABLED", settings.auto_apply_enabled),
            "browser_profile_directory": _environment_string(
                "APPLICATION_BROWSER_PROFILE", settings.browser_profile_directory
            ),
            "browser_channel": _environment_string(
                "APPLICATION_BROWSER_CHANNEL", settings.browser_channel or ""
            ) or None,
        }
    )


_PROVIDER_METHODS = {
    ApplicationProvider.GREENHOUSE: ApplicationMethod.GREENHOUSE,
    ApplicationProvider.LEVER: ApplicationMethod.LEVER,
    ApplicationProvider.WORKDAY: ApplicationMethod.WORKDAY,
    ApplicationProvider.ASHBY: ApplicationMethod.ASHBY,
    ApplicationProvider.SMARTRECRUITERS: ApplicationMethod.SMARTRECRUITERS,
    ApplicationProvider.GENERIC: ApplicationMethod.BROWSER,
}


AgentFactory = Callable[..., BaseApplicationAgent | None]


class ApplicationService:
    """Run a conservative sequential application batch; one failure never aborts it."""

    def __init__(
        self,
        repository: JobRepository,
        *,
        settings: ApplicationSettings | None = None,
        candidate_profile: CandidateProfile | None = None,
        project_root: str | Path = PROJECT_ROOT,
        agent_factory: AgentFactory = select_application_agent,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings or load_application_settings()
        self.candidate_profile = candidate_profile or load_candidate_profile()
        self.project_root = Path(project_root).resolve()
        self.agent_factory = agent_factory
        self.session_manager = session_manager or SessionManager(
            self.project_root / "data" / "auth",
            project_root=self.project_root,
            browser_channel=self.settings.browser_channel or "chrome",
        )
        self.active_resume_path: str | None = None
        self.candidate_skills: tuple[str, ...] = ()
        self.refresh_active_resume()
        self._lock = asyncio.Lock()
        self.last_summary: ApplicationCycleSummary | None = None

    def refresh_active_resume(self) -> None:
        """Reload the singleton resume used for every future application."""
        active = self.repository.get_active_resume()
        if active is None:
            self.active_resume_path = None
            self.candidate_skills = ()
            return
        profile = ParsedCandidateProfile.model_validate(active.parsed_profile)
        self.active_resume_path = active.path
        self.candidate_skills = tuple(dict.fromkeys((*profile.skills, *profile.technologies)))

    @property
    def running(self) -> bool:
        return self._lock.locked()

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "dry_run": self.settings.dry_run,
            "auto_apply_enabled": self.settings.auto_apply_enabled,
            "last_run": self.last_summary.model_dump(mode="json") if self.last_summary else None,
        }

    async def run_application_cycle(
        self,
        *,
        job_id: int | None = None,
        job_ids: list[int] | None = None,
    ) -> ApplicationCycleSummary:
        if job_id is not None and job_ids is not None:
            raise ValueError("Pass either job_id or job_ids, not both")
        if not self.settings.auto_apply_enabled:
            raise ValueError("Automatic application execution is disabled")
        if self._lock.locked():
            raise RuntimeError("An application cycle is already running")
        async with self._lock:
            self.refresh_active_resume()
            if not self.active_resume_path:
                raise ValueError("Upload an active resume before running applications")
            summary = ApplicationCycleSummary(
                dry_run=self.settings.dry_run,
                started_at=datetime.now(timezone.utc),
            )
            selected_ids = list(dict.fromkeys(job_ids or [])) if job_ids is not None else None
            requested_count = 1 if job_id is not None else len(selected_ids or [])
            limit = (
                1
                if job_id is not None
                else len(selected_ids)
                if selected_ids is not None
                else self.settings.max_applications_per_run
            )
            jobs = self.repository.get_application_candidates(
                minimum_score=self.settings.minimum_ats_score,
                limit=limit,
                job_id=job_id,
                job_ids=selected_ids,
            )
            summary.selected = requested_count if job_id is not None or selected_ids is not None else len(jobs)
            summary.eligible = len(jobs)
            summary.ineligible = max(0, summary.selected - summary.eligible)
            for index, job in enumerate(jobs):
                await self._process_job_with_compatible_loop(job, summary)
                if index + 1 < len(jobs) and self.settings.delay_between_applications_seconds:
                    await asyncio.sleep(self.settings.delay_between_applications_seconds)
            summary.completed_at = datetime.now(timezone.utc)
            self.last_summary = summary
            return summary

    async def _process_job_with_compatible_loop(
        self,
        job: Job,
        summary: ApplicationCycleSummary,
    ) -> None:
        """Run Playwright on a subprocess-capable event loop on Windows.

        Uvicorn uses ``SelectorEventLoop`` for its reload subprocess on Windows.
        That loop raises ``NotImplementedError`` when Playwright launches its
        driver.  A worker thread created through ``asyncio.to_thread`` receives
        the normal Windows Proactor loop through ``asyncio.run`` while the API's
        request loop remains responsive.
        """
        loop = asyncio.get_running_loop()
        if sys.platform == "win32" and isinstance(loop, asyncio.SelectorEventLoop):
            await asyncio.to_thread(
                lambda: asyncio.run(self._process_job(job, summary))
            )
            return
        await self._process_job(job, summary)

    async def _process_job(self, job: Job, summary: ApplicationCycleSummary) -> None:
        if self.repository.already_applied(job.id):
            summary.skipped_duplicates += 1
            return
        resume_path = self.active_resume_path
        absolute_resume = (self.project_root / (resume_path or "")).resolve()
        resume_available = bool(resume_path and absolute_resume.is_file())
        errors = application_eligibility_errors(job, resume_available=resume_available)
        if errors:
            self.repository.mark_application_needs_review(job.id, "; ".join(errors))
            summary.needs_review += 1
            LOGGER.info(
                "application_result company=%s title=%s provider=%s result=NEEDS_REVIEW reason=%s",
                job.company,
                job.title,
                detect_provider_from_url(job.application_url).value,
                "; ".join(errors),
            )
            return
        if job.recommended_resume != resume_path:
            job = self.repository.update_job(job.id, recommended_resume=resume_path) or job

        url_provider = detect_provider_from_url(job.application_url)
        detected = _PROVIDER_METHODS[url_provider]
        required_platform = SessionManager.platform_for_url(job.application_url)
        # Authentication follows the actual application host, not the site
        # that originally advertised the job. An external company or public
        # ATS URL must not be blocked by a LinkedIn/Naukri discovery source.
        if required_platform and not self.session_manager.is_connected(required_platform):
            state = self.session_manager.status(required_platform)
            self.repository.mark_application_needs_review(
                job.id,
                f"{required_platform.title()} is not connected ({state.status}); connect it before Auto Apply",
                application_method=detected,
            )
            summary.needs_review += 1
            LOGGER.info(
                "application_result company=%s title=%s result=NEEDS_REVIEW platform=%s connection=%s",
                job.company,
                job.title,
                required_platform,
                state.status,
            )
            return
        relevant_skills = matched_terms(
            self.candidate_skills,
            " ".join(filter(None, (job.title, job.description))),
        )
        agent = self.agent_factory(
            job,
            project_root=self.project_root,
            headless=self.settings.headless,
            dry_run=self.settings.dry_run,
            navigation_timeout_seconds=self.settings.navigation_timeout_seconds,
            matched_skills=relevant_skills,
            browser_profile_directory=(
                self.session_manager.profile_directory(required_platform)
                if required_platform
                else self.settings.browser_profile_directory
            ),
            browser_channel=self.settings.browser_channel,
        )
        if agent is None:
            self.repository.mark_application_needs_review(job.id, "No supported application adapter was found")
            summary.needs_review += 1
            return

        started_at = datetime.now(timezone.utc)
        summary.attempted += 1
        try:
            LOGGER.info(
                "application_started company=%s title=%s url=%s provider=%s ats_score=%s resume=%s start=%s",
                job.company,
                job.title,
                job.application_url,
                detect_provider_from_url(job.application_url).value,
                job.match_score,
                resume_path,
                started_at.isoformat(),
            )
            await agent.open_application(job)
            resolved_url = (
                getattr(agent, "resolved_application_url", None)
                or getattr(getattr(agent, "page", None), "url", None)
                or job.application_url
            )
            provider = await detect_provider(resolved_url, agent.page)
            detected = _PROVIDER_METHODS[provider]
            details = await agent.fill_candidate_details(self.candidate_profile)
            attached = await agent.upload_resume(absolute_resume)
            questions = await agent.fill_job_questions(job, self.candidate_profile)
            LOGGER.info(
                "application_filled company=%s title=%s provider=%s resolved_url=%s fields=%s resume_attached=%s unresolved=%s",
                job.company,
                job.title,
                provider.value,
                resolved_url,
                sorted(set((*details.fields_filled, *questions.fields_filled))),
                attached,
                list(dict.fromkeys((*details.unresolved_questions, *questions.unresolved_questions))),
            )
            validation = await agent.validate_before_submit()
            if not validation.valid:
                reason = "; ".join(validation.errors)
                self.repository.mark_application_needs_review(
                    job.id,
                    reason,
                    application_method=detected,
                )
                summary.needs_review += 1
                LOGGER.info(
                    "application_result company=%s title=%s result=NEEDS_REVIEW reason=%s",
                    job.company,
                    job.title,
                    reason,
                )
                return
            latest = self.repository.get_job(job.id)
            if latest is None:
                self.repository.mark_application_failed(job.id, "Job record disappeared before submission")
                summary.failed += 1
                return
            final_errors = application_eligibility_errors(latest, resume_available=True)
            if self.repository.already_applied(job.id):
                summary.skipped_duplicates += 1
                return
            if final_errors:
                reason = "; ".join(final_errors)
                self.repository.mark_application_needs_review(
                    job.id,
                    reason,
                    application_method=detected,
                )
                summary.needs_review += 1
                return
            submission = await agent.submit()
            if submission.ready_to_submit:
                self.repository.record_ready_to_submit(
                    job.id,
                    application_method=detected,
                    resume_used=resume_path or "",
                )
                LOGGER.info("application_result company=%s title=%s result=READY_TO_SUBMIT", job.company, job.title)
                return
            if not submission.submitted:
                self.repository.mark_application_failed(
                    job.id,
                    submission.message,
                    application_method=detected,
                )
                summary.failed += 1
                LOGGER.info(
                    "application_result company=%s title=%s result=FAILED confirmation=false reason=%s",
                    job.company,
                    job.title,
                    submission.message,
                )
                return
            confirmation = await agent.verify_submission()
            if not confirmation.confirmed:
                self.repository.mark_application_failed(
                    job.id,
                    confirmation.message,
                    application_method=detected,
                )
                summary.failed += 1
                LOGGER.info(
                    "application_result company=%s title=%s result=FAILED confirmation=false reason=%s",
                    job.company,
                    job.title,
                    confirmation.message,
                )
                return
            self.repository.mark_applied(
                job.id,
                resume_used=resume_path,
                application_method=detected,
                applied_at=datetime.now(timezone.utc),
                application_confirmation=confirmation.confirmation_text,
                external_application_id=confirmation.external_application_id,
            )
            summary.applied += 1
            LOGGER.info(
                "application_result company=%s title=%s result=APPLIED confirmation=true",
                job.company,
                job.title,
            )
        except ApplicationNeedsReviewError as error:
            if required_platform and any(
                marker in str(error).casefold()
                for marker in ("sign in", "log in", "login", "authentication", "access approval")
            ):
                self.session_manager.mark_disconnected(required_platform, str(error))
            self.repository.mark_application_needs_review(
                job.id,
                str(error),
                application_method=detected,
            )
            summary.needs_review += 1
            LOGGER.info(
                "application_result company=%s title=%s result=NEEDS_REVIEW reason=%s",
                job.company,
                job.title,
                error,
            )
        except Exception as error:
            reason = f"{type(error).__name__}: {error}"
            self.repository.mark_application_failed(job.id, reason, application_method=detected)
            summary.failed += 1
            LOGGER.exception("application_failed company=%s title=%s", job.company, job.title)
        finally:
            await agent.close()


async def run_application_cycle(*, job_id: int | None = None) -> ApplicationCycleSummary:
    """Convenience entry point using the default SQLite repository."""
    from job_automation.database import initialize_database

    engine = initialize_database()
    try:
        return await ApplicationService(JobRepository(engine)).run_application_cycle(job_id=job_id)
    finally:
        engine.dispose()
