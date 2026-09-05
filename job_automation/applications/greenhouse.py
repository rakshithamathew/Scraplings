"""Greenhouse application-plan adapter; no form submission."""

from urllib.parse import urlsplit

from job_automation.applications.base import ApplicationField, ApplicationJob, BaseApplicationAgent, COMMON_FIELDS
from job_automation.database import ApplicationMethod


class GreenhouseApplicationAgent(BaseApplicationAgent):
    provider = ApplicationMethod.GREENHOUSE

    def can_handle(self, job: ApplicationJob) -> bool:
        host = urlsplit(job.application_url or job.source_url or "").netloc.casefold()
        return "greenhouse" in (job.source or "").casefold() or "greenhouse.io" in host

    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        return COMMON_FIELDS + (
            ApplicationField(name="cover_letter", label="Cover letter", required=False),
            ApplicationField(name="linkedin_url", label="LinkedIn profile", required=False),
            ApplicationField(name="website", label="Website", required=False),
            ApplicationField(
                name="custom_questions",
                label="Company-specific questions",
                notes="Must be reviewed on the public application page.",
            ),
        )
