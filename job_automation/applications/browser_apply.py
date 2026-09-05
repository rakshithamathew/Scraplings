"""Fallback application-plan adapter; browser submission remains disabled."""

from job_automation.applications.base import ApplicationField, ApplicationJob, BaseApplicationAgent, COMMON_FIELDS
from job_automation.database import ApplicationMethod


class BrowserApplicationAgent(BaseApplicationAgent):
    provider = ApplicationMethod.BROWSER

    def can_handle(self, job: ApplicationJob) -> bool:
        return self._valid_application_url(job.application_url)

    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        return COMMON_FIELDS + (
            ApplicationField(
                name="unknown_fields",
                label="Site-specific fields",
                notes="Review manually. CAPTCHA, login, and access controls must not be bypassed.",
            ),
        )
