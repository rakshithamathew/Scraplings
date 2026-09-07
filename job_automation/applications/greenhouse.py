"""Greenhouse application adapter."""

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

    async def open_application(self, job: ApplicationJob) -> bool:
        opened = await super().open_application(job)
        if await self.page.locator('input[type="file"]').count() == 0:
            apply_controls = self.page.get_by_role("link", name="Apply for this job", exact=False)
            if await apply_controls.count():
                await apply_controls.first.click(timeout=self.navigation_timeout_ms)
                await self.page.wait_for_load_state("domcontentloaded")
        return opened
