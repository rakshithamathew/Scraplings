"""Safe application planning contracts and browser application lifecycle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
import re
from typing import Any, Literal, Protocol
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict

from job_automation.applications.candidate_profile import CandidateProfile
from job_automation.applications.field_matcher import FormFillResult, FormFieldMatcher
from job_automation.database import ApplicationMethod, ApplicationStatus, Job, JobRepository, JobStatus
from job_automation.matching.requirements import remote_allows_india, valid_public_application_url
from job_automation.resume import ResumeMetadata


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ApplicationJob(Protocol):
    id: int
    source: str
    source_url: str | None
    application_url: str
    recommended_resume: str | None
    match_score: float | None
    status: JobStatus
    is_open: bool | None
    workplace_type: object
    location: str | None
    description: str | None


class ApplicationField(BaseModel):
    """A likely form field; it intentionally contains no applicant value."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    required: bool = True
    notes: str | None = None


class ApplicationValidation(BaseModel):
    valid: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class ApplicationPlan(BaseModel):
    """Serializable preparation output that cannot itself submit an application."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    provider: ApplicationMethod
    application_url: str | None
    resume_path: str | None
    fields: tuple[ApplicationField, ...]
    application_status: ApplicationStatus
    requires_user_approval: Literal[True] = True
    warnings: tuple[str, ...] = ()


class SubmissionResult(BaseModel):
    submitted: Literal[False] = False
    requires_user_approval: Literal[True] = True
    application_status: Literal[ApplicationStatus.NEEDS_REVIEW] = ApplicationStatus.NEEDS_REVIEW
    message: str = "Submission is disabled. Review the plan and apply manually."


class BrowserSubmissionResult(BaseModel):
    submitted: bool = False
    ready_to_submit: bool = False
    confirmed: bool = False
    confirmation_text: str | None = None
    external_application_id: str | None = None
    message: str


class ApplicationNeedsReviewError(RuntimeError):
    """Raised when a browser flow reaches a mandatory human checkpoint."""


COMMON_FIELDS = (
    ApplicationField(name="first_name", label="First name"),
    ApplicationField(name="last_name", label="Last name"),
    ApplicationField(name="email", label="Email"),
    ApplicationField(name="phone", label="Phone", required=False),
    ApplicationField(name="resume", label="Resume"),
)


_HUMAN_CHECKPOINTS = {
    "CAPTCHA": re.compile(r"\bcaptcha\b", re.IGNORECASE),
    "OTP": re.compile(r"\b(?:otp|one[- ]time password)\b", re.IGNORECASE),
    "login approval": re.compile(r"\b(?:sign in|log in|login required)\b", re.IGNORECASE),
    "assessment": re.compile(r"\b(?:assessment|coding test|take-home)\b", re.IGNORECASE),
    "legal declaration": re.compile(r"\b(?:legal declaration|certify under penalty|attestation)\b", re.IGNORECASE),
    "video response": re.compile(r"\b(?:video response|video interview|record(?:ed)? video)\b", re.IGNORECASE),
}

_UNKNOWN_MANDATORY_QUALIFICATIONS = {
    "work authorization": re.compile(r"\b(?:must be authorized to work|work authorization required)\b", re.IGNORECASE),
    "citizenship": re.compile(r"\b(?:citizenship required|must be a (?:u\.?s\.?|us) citizen)\b", re.IGNORECASE),
    "security clearance": re.compile(r"\b(?:security clearance|active clearance)\b", re.IGNORECASE),
    "mandatory certification": re.compile(r"\b(?:certification|certificate) (?:is )?required\b", re.IGNORECASE),
}


def application_eligibility_errors(job: ApplicationJob, *, resume_available: bool) -> tuple[str, ...]:
    """Evaluate every deterministic pre-application requirement available locally."""
    errors: list[str] = []
    if job.is_open is not True:
        errors.append("The job opening has not been verified as still open")
    if job.match_score is None or job.match_score <= 70:
        errors.append("ATS score must be greater than 70")
    if job.status is JobStatus.APPLIED:
        errors.append("This job is already recorded as applied")
    if not valid_public_application_url(job.application_url):
        errors.append("A valid public HTTP(S) application URL is required")
    if not resume_available:
        errors.append("An existing, matching resume is required")

    work_type = getattr(job.workplace_type, "value", str(job.workplace_type or "UNKNOWN")).upper()
    location = (job.location or "").casefold()
    if work_type in {"HYBRID", "ONSITE"} and "bengaluru" not in location and "bangalore" not in location:
        errors.append("Hybrid/onsite applications are allowed only in Bengaluru")
    if work_type == "REMOTE" and not remote_allows_india(job.location, job.description):
        errors.append("Remote role explicitly excludes applicants based in India")
    if work_type == "UNKNOWN":
        errors.append("Workplace type must be verified")

    description = job.description or ""
    for label, pattern in _HUMAN_CHECKPOINTS.items():
        if pattern.search(description):
            errors.append(f"Application requires user review: {label}")
    for label, pattern in _UNKNOWN_MANDATORY_QUALIFICATIONS.items():
        if pattern.search(description):
            errors.append(f"Mandatory qualification must be verified: {label}")
    return tuple(dict.fromkeys(errors))


class BaseApplicationAgent(ABC):
    """Planning API plus a conservative Playwright application lifecycle."""

    provider: ApplicationMethod

    def __init__(
        self,
        *,
        project_root: str | Path = PROJECT_ROOT,
        headless: bool = False,
        dry_run: bool = True,
        navigation_timeout_seconds: float = 30,
        matched_skills: tuple[str, ...] = (),
        browser_profile_directory: str | Path = "data/application_browser_profile",
        browser_channel: str | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.resume_directory = (self.project_root / "resumes").resolve()
        self.headless = headless
        self.dry_run = dry_run
        self.navigation_timeout_ms = int(navigation_timeout_seconds * 1000)
        self.matched_skills = matched_skills
        raw_profile = Path(browser_profile_directory)
        self.browser_profile_directory = (
            raw_profile if raw_profile.is_absolute() else self.project_root / raw_profile
        ).resolve()
        self.browser_channel = browser_channel.strip() if browser_channel else None
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self.page: Any = None
        self.current_job: ApplicationJob | None = None
        self.resolved_application_url: str | None = None
        self.fill_result = FormFillResult()
        self._submission_clicked = False

    @abstractmethod
    def can_handle(self, job: ApplicationJob) -> bool:
        """Return whether this adapter recognizes the job's provider."""

    @abstractmethod
    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        """Return provider-level field expectations without scraping or filling a form."""

    def prepare_application(
        self,
        job: ApplicationJob,
        resume: str | Path | ResumeMetadata | None = None,
    ) -> ApplicationPlan:
        resume_path = self._select_resume_path(job, resume)
        warnings: list[str] = []
        if not self.can_handle(job):
            warnings.append(f"{self.provider.value} adapter does not recognize this application URL")
        if not self._valid_application_url(job.application_url):
            warnings.append("A public HTTP(S) application URL is required")
        if resume_path is None:
            warnings.append("An existing resume inside the resumes directory is required")
        warnings.extend(application_eligibility_errors(job, resume_available=resume_path is not None))
        plan = ApplicationPlan(
            job_id=job.id,
            provider=self.provider,
            application_url=job.application_url or None,
            resume_path=resume_path,
            fields=self.likely_fields(job),
            application_status=ApplicationStatus.NEEDS_REVIEW if warnings else ApplicationStatus.PREPARED,
            warnings=tuple(warnings),
        )
        validation = self.validate_application(plan)
        if validation.valid:
            return plan
        combined = tuple(dict.fromkeys((*plan.warnings, *validation.errors)))
        return plan.model_copy(
            update={"application_status": ApplicationStatus.NEEDS_REVIEW, "warnings": combined}
        )

    def validate_application(self, plan: ApplicationPlan) -> ApplicationValidation:
        """Validate preparation inputs only; no remote page is opened."""
        errors: list[str] = []
        if not self._valid_application_url(plan.application_url):
            errors.append("Invalid or missing application URL")
        if not plan.resume_path or self._resolve_resume(plan.resume_path) is None:
            errors.append("Resume is missing or outside the resumes directory")
        return ApplicationValidation(valid=not errors, errors=tuple(errors), warnings=plan.warnings)

    def submit_application(self, plan: ApplicationPlan) -> SubmissionResult:
        """Never submit. The user must review and complete the application manually."""
        return SubmissionResult()

    async def open_application(self, job: ApplicationJob) -> bool:
        """Open one public application page in an isolated Chromium context."""
        if not self.can_handle(job) or not self._valid_application_url(job.application_url):
            raise ApplicationNeedsReviewError("Application URL is invalid or unsupported")
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:
            raise RuntimeError("Playwright is not installed in the active environment") from error
        self.current_job = job
        self._playwright = await async_playwright().start()
        try:
            self.browser_profile_directory.mkdir(parents=True, exist_ok=True)
            launch_options: dict[str, Any] = {
                "user_data_dir": str(self.browser_profile_directory),
                "headless": self.headless,
            }
            if self.browser_channel:
                launch_options["channel"] = self.browser_channel
            self._context = await self._playwright.chromium.launch_persistent_context(**launch_options)
            pages = self._context.pages
            self.page = pages[0] if pages else await self._context.new_page()
            response = await self.page.goto(
                job.application_url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            if response is not None and response.status in {401, 403}:
                raise ApplicationNeedsReviewError(
                    f"Application page requires authentication or access approval (HTTP {response.status})"
                )
            if response is not None and response.status in {404, 410}:
                raise ApplicationNeedsReviewError(f"Application page returned HTTP {response.status}")
            if response is not None and response.status >= 500:
                raise RuntimeError(f"Application page returned HTTP {response.status}")
            await self.page.wait_for_timeout(500)
            await self._validate_open_page()
            await self._resolve_application_destination()
            self.resolved_application_url = self.page.url
            await self._validate_open_page()
            return True
        except Exception:
            await self.close()
            raise

    async def _resolve_application_destination(self, *, max_hops: int = 4) -> None:
        """Follow public listing-page application links to the actual form.

        HTTP redirects are already followed by ``page.goto``.  This handles the
        additional user-visible Apply/Continue control used by LinkedIn, Naukri,
        and company career pages.  It is deliberately bounded and never tries to
        cross a login, CAPTCHA, OTP, or other human checkpoint.
        """
        self._require_page()
        visited = {self.page.url.rstrip("/")}
        for _ in range(max_hops):
            if await self._has_application_form():
                return
            control = await self._find_application_control()
            if control is None:
                raise ApplicationNeedsReviewError(
                    "No public application form or application redirect was found"
                )

            href = await control.get_attribute("href")
            destination = urljoin(self.page.url, href) if href else None
            if destination and self._valid_application_url(destination):
                normalized = destination.rstrip("/")
                if normalized in visited:
                    raise ApplicationNeedsReviewError("Application redirect loops back to the listing page")
                visited.add(normalized)
                response = await self.page.goto(
                    destination,
                    wait_until="domcontentloaded",
                    timeout=self.navigation_timeout_ms,
                )
                await self._validate_navigation_response(response)
            else:
                pages_before = tuple(self._context.pages)
                await control.click(timeout=self.navigation_timeout_ms)
                await self.page.wait_for_timeout(750)
                pages_after = tuple(self._context.pages)
                new_pages = [page for page in pages_after if page not in pages_before]
                if new_pages:
                    self.page = new_pages[-1]
                try:
                    await self.page.wait_for_load_state(
                        "domcontentloaded", timeout=self.navigation_timeout_ms
                    )
                except Exception:
                    # Client-side forms may render without a navigation event.
                    pass
                normalized = self.page.url.rstrip("/")
                if normalized in visited and not await self._has_application_form():
                    await self._validate_open_page()
                    raise ApplicationNeedsReviewError(
                        "Apply control did not open a public application form"
                    )
                visited.add(normalized)
            await self.page.wait_for_timeout(500)
            await self._validate_open_page()

        if not await self._has_application_form():
            raise ApplicationNeedsReviewError("Too many application redirects without reaching a form")

    async def _has_application_form(self) -> bool:
        file_inputs = await self.page.locator('input[type="file"]').count()
        if file_inputs:
            return True
        forms = self.page.locator("form")
        for index in range(await forms.count()):
            form = forms.nth(index)
            controls = await form.locator("input, textarea, select, [role=combobox]").count()
            identity = await form.locator(
                'input[type="email"], input[name*="email" i], input[name*="name" i]'
            ).count()
            submit = await form.locator(
                'button[type="submit"], input[type="submit"], button:has-text("Submit")'
            ).count()
            if controls >= 2 and identity and submit:
                return True
        return False

    async def _find_application_control(self) -> Any | None:
        labels = re.compile(
            r"^(?:easy\s+)?apply(?:\s+now)?(?:\s+for\s+this\s+job)?$|"
            r"apply\s+on\s+(?:company|employer)\s+(?:site|website)|"
            r"continue\s+(?:to\s+)?application|start\s+application",
            re.I,
        )
        candidates: list[tuple[int, Any]] = []
        controls = self.page.locator('a[href], button:not([type="submit"]), [role="button"]')
        for index in range(await controls.count()):
            control = controls.nth(index)
            try:
                if not await control.is_visible():
                    continue
                text = " ".join((await control.inner_text()).split())
                aria = await control.get_attribute("aria-label") or ""
                title = await control.get_attribute("title") or ""
                label = " ".join(filter(None, (text, aria, title)))
                if not labels.search(label):
                    continue
                href = await control.get_attribute("href")
                external_priority = 0 if re.search(r"company|employer|continue", label, re.I) else 1
                href_priority = 0 if href else 1
                candidates.append((external_priority * 2 + href_priority, control))
            except Exception:
                continue
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    async def _validate_open_page(self) -> None:
        page_text = (await self.page.locator("body").inner_text()).casefold()
        closed_markers = (
            "job is no longer available",
            "position has been filled",
            "no longer accepting applications",
            "this job has closed",
            "job posting is no longer active",
        )
        if any(marker in page_text for marker in closed_markers):
            raise ApplicationNeedsReviewError("The application page indicates that the job is closed")
        password_fields = await self.page.locator('input[type="password"]').count()
        login_markers = (
            "sign in to apply",
            "log in to apply",
            "login to apply",
            "sign in to continue",
            "create an account to apply",
        )
        if password_fields or any(marker in page_text for marker in login_markers):
            raise ApplicationNeedsReviewError("Login or account approval requires user review")
        if checkpoint := self._checkpoint_reason(page_text):
            raise ApplicationNeedsReviewError(checkpoint)

    @staticmethod
    async def _validate_navigation_response(response: Any | None) -> None:
        if response is None:
            return
        if response.status in {401, 403}:
            raise ApplicationNeedsReviewError(
                f"Application page requires authentication or access approval (HTTP {response.status})"
            )
        if response.status in {404, 410}:
            raise ApplicationNeedsReviewError(f"Application page returned HTTP {response.status}")
        if response.status >= 500:
            raise RuntimeError(f"Application page returned HTTP {response.status}")

    async def fill_candidate_details(self, profile: CandidateProfile) -> FormFillResult:
        self._require_page()
        result = await FormFieldMatcher(self.page).fill_candidate_details(profile)
        self.fill_result.fields_filled.extend(result.fields_filled)
        self.fill_result.unresolved_questions.extend(result.unresolved_questions)
        return result

    async def upload_resume(self, path: str | Path) -> bool:
        self._require_page()
        resolved = self._resolve_resume(path)
        if resolved is None:
            return False
        attached = await FormFieldMatcher(self.page).upload_resume(str(resolved))
        self.fill_result.resume_attached = attached
        return attached

    async def fill_job_questions(
        self,
        job: ApplicationJob,
        profile: CandidateProfile,
    ) -> FormFillResult:
        self._require_page()
        result = await FormFieldMatcher(self.page).fill_questions(
            profile,
            job_title=getattr(job, "title", "the role"),
            company=getattr(job, "company", "the company"),
            matched_skills=self.matched_skills,
            already_filled=set(self.fill_result.fields_filled),
        )
        self.fill_result.fields_filled.extend(result.fields_filled)
        self.fill_result.unresolved_questions.extend(result.unresolved_questions)
        return result

    async def validate_before_submit(self) -> ApplicationValidation:
        """Require a resume, valid required controls, and no human checkpoint."""
        self._require_page()
        errors: list[str] = []
        if not self.fill_result.resume_attached:
            errors.append("Resume is not attached")
        filled = set(self.fill_result.fields_filled)
        if "email" not in filled or not ({"first_name", "full_name"} & filled):
            errors.append("Candidate identity fields are not fully populated")
        errors.extend(
            f"Unresolved required question: {question}"
            for question in dict.fromkeys(self.fill_result.unresolved_questions)
        )
        try:
            invalid = await self.page.locator(
                "input:required:invalid, textarea:required:invalid, select:required:invalid"
            ).count()
            if invalid:
                errors.append(f"{invalid} required form field(s) remain invalid")
            page_text = (await self.page.locator("body").inner_text()).casefold()
            if self.current_job is None:
                errors.append("Current job identity is unavailable")
            else:
                expected_title = " ".join(getattr(self.current_job, "title", "").casefold().split())
                normalized_page = " ".join(page_text.split())
                if expected_title and expected_title not in normalized_page:
                    errors.append("Rendered application page does not confirm the expected job title")
            if checkpoint := self._checkpoint_reason(page_text):
                errors.append(checkpoint)
        except Exception as error:
            errors.append(f"Unable to validate rendered form: {type(error).__name__}")
        return ApplicationValidation(valid=not errors, errors=tuple(dict.fromkeys(errors)))

    async def submit(self) -> BrowserSubmissionResult:
        """Click the final submit control only when dry-run mode is disabled."""
        validation = await self.validate_before_submit()
        if not validation.valid:
            return BrowserSubmissionResult(message="; ".join(validation.errors))
        if self.dry_run:
            return BrowserSubmissionResult(
                ready_to_submit=True,
                message="READY_TO_SUBMIT (DRY_RUN prevented the final click)",
            )
        controls = self.page.locator('button[type="submit"], input[type="submit"]')
        if await controls.count() == 0:
            controls = self.page.get_by_role("button", name=re.compile(r"submit|apply", re.I))
        if await controls.count() == 0:
            return BrowserSubmissionResult(message="No final submit control was found")
        await controls.first.click(timeout=self.navigation_timeout_ms)
        self._submission_clicked = True
        return BrowserSubmissionResult(submitted=True, message="Submit control clicked; awaiting confirmation")

    async def verify_submission(self) -> BrowserSubmissionResult:
        """Recognize explicit success evidence after a real submit click."""
        self._require_page()
        if not self._submission_clicked:
            return BrowserSubmissionResult(message="Submission was not clicked")
        try:
            await self.page.wait_for_timeout(1000)
            body = " ".join((await self.page.locator("body").inner_text()).split())
        except Exception as error:
            return BrowserSubmissionResult(submitted=True, message=f"Unable to read confirmation: {error}")
        success_pattern = re.compile(
            r"(application (?:has been )?(?:received|submitted)|thank you for applying|"
            r"thanks for applying|successfully submitted|application complete)",
            re.I,
        )
        match = success_pattern.search(body)
        if not match:
            return BrowserSubmissionResult(submitted=True, message="No explicit submission confirmation was found")
        reference_match = re.search(
            r"(?:application|confirmation|reference)\s*(?:id|number|#)\s*[:#-]?\s*([A-Z0-9-]{4,})",
            body,
            re.I,
        )
        confirmation = body[max(0, match.start() - 80) : match.end() + 160]
        return BrowserSubmissionResult(
            submitted=True,
            confirmed=True,
            confirmation_text=confirmation,
            external_application_id=reference_match.group(1) if reference_match else None,
            message="Submission confirmation detected",
        )

    async def close(self) -> None:
        """Close all browser resources, including partially opened sessions."""
        for resource in (self._context, self._browser):
            if resource is not None:
                try:
                    await resource.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self.page = self._context = self._browser = self._playwright = None

    def _require_page(self) -> None:
        if self.page is None:
            raise RuntimeError("Open an application page before using browser actions")

    @staticmethod
    def _checkpoint_reason(page_text: str) -> str | None:
        checkpoints = (
            (r"\bcaptcha\b|i am not a robot", "CAPTCHA requires user review"),
            (r"\b(?:otp|one-time password|verification code)\b", "OTP or verification requires user review"),
            (r"\b(?:two-factor|2fa|login approval)\b", "Login approval requires user review"),
            (r"\b(?:coding assessment|video assessment|record a video)\b", "Assessment requires user review"),
        )
        return next((reason for pattern, reason in checkpoints if re.search(pattern, page_text, re.I)), None)

    def _select_resume_path(
        self,
        job: ApplicationJob,
        resume: str | Path | ResumeMetadata | None,
    ) -> str | None:
        supplied = resume.path if isinstance(resume, ResumeMetadata) else resume
        value = str(supplied or job.recommended_resume or "").strip()
        resolved = self._resolve_resume(value)
        if resolved is None:
            return None
        return resolved.relative_to(self.project_root).as_posix()

    def _resolve_resume(self, value: str | Path) -> Path | None:
        if not value:
            return None
        raw = Path(value)
        candidate = (raw if raw.is_absolute() else self.project_root / raw).resolve()
        try:
            candidate.relative_to(self.resume_directory)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _valid_application_url(value: str | None) -> bool:
        if not value:
            return False
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def store_application_plan(repository: JobRepository, plan: ApplicationPlan) -> Job | None:
    """Persist plan metadata explicitly; preparation itself remains side-effect free."""
    return repository.record_application_preparation(
        plan.job_id,
        application_method=plan.provider,
        application_status=plan.application_status,
    )
