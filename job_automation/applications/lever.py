"""Lever application adapter."""

from urllib.parse import urlsplit

from job_automation.applications.base import ApplicationField, ApplicationJob, BaseApplicationAgent, COMMON_FIELDS
from job_automation.database import ApplicationMethod


class LeverApplicationAgent(BaseApplicationAgent):
    provider = ApplicationMethod.LEVER

    def can_handle(self, job: ApplicationJob) -> bool:
        host = urlsplit(job.application_url or job.source_url or "").netloc.casefold()
        return "lever" in (job.source or "").casefold() or host.endswith("lever.co")

    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        return COMMON_FIELDS + (
            ApplicationField(name="current_company", label="Current company", required=False),
            ApplicationField(name="linkedin_url", label="LinkedIn profile", required=False),
            ApplicationField(name="portfolio_url", label="Portfolio URL", required=False),
            ApplicationField(name="additional_information", label="Additional information", required=False),
            ApplicationField(
                name="custom_questions",
                label="Company-specific questions",
                notes="Must be reviewed on the public application page.",
            ),
        )
