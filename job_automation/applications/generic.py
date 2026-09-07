"""Generic semantic HTML-form application adapter."""

from job_automation.applications.base import ApplicationField, ApplicationJob, BaseApplicationAgent, COMMON_FIELDS
from job_automation.database import ApplicationMethod


class GenericApplicationAgent(BaseApplicationAgent):
    provider = ApplicationMethod.BROWSER

    def can_handle(self, job: ApplicationJob) -> bool:
        return self._valid_application_url(job.application_url)

    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        return COMMON_FIELDS + (
            ApplicationField(
                name="site_specific_fields",
                label="Site-specific fields",
                notes="Unknown mandatory fields stop automation and require review.",
            ),
        )
