"""Workday application-plan adapter; no login or form submission."""

from urllib.parse import urlsplit

from job_automation.applications.base import ApplicationField, ApplicationJob, BaseApplicationAgent, COMMON_FIELDS
from job_automation.database import ApplicationMethod


class WorkdayApplicationAgent(BaseApplicationAgent):
    provider = ApplicationMethod.WORKDAY

    def can_handle(self, job: ApplicationJob) -> bool:
        host = urlsplit(job.application_url or job.source_url or "").netloc.casefold()
        return "workday" in (job.source or "").casefold() or "workdayjobs.com" in host

    def likely_fields(self, job: ApplicationJob) -> tuple[ApplicationField, ...]:
        return COMMON_FIELDS + (
            ApplicationField(
                name="account_or_login",
                label="Workday account or login",
                notes="The user must create or access any required account manually.",
            ),
            ApplicationField(name="address", label="Address", required=False),
            ApplicationField(name="work_authorization", label="Work authorization"),
            ApplicationField(name="employment_history", label="Employment history"),
            ApplicationField(name="education", label="Education", required=False),
            ApplicationField(
                name="custom_questions",
                label="Company-specific questions",
                notes="Must be reviewed manually; Workday variants differ by company.",
            ),
        )
