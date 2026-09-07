"""Application planning and approval-gated browser execution adapters."""

from job_automation.applications.base import (
    ApplicationField,
    ApplicationPlan,
    ApplicationValidation,
    ApplicationNeedsReviewError,
    BaseApplicationAgent,
    BrowserSubmissionResult,
    SubmissionResult,
    application_eligibility_errors,
    store_application_plan,
)
from job_automation.applications.browser_apply import BrowserApplicationAgent
from job_automation.applications.candidate_profile import CandidateProfile, load_candidate_profile
from job_automation.applications.detector import ApplicationProvider, detect_provider, detect_provider_from_url
from job_automation.applications.generic import GenericApplicationAgent
from job_automation.applications.greenhouse import GreenhouseApplicationAgent
from job_automation.applications.lever import LeverApplicationAgent
from job_automation.applications.workday import WorkdayApplicationAgent


def select_application_agent(job: object, **agent_options: object) -> BaseApplicationAgent | None:
    """Choose a specific provider adapter before the generic browser fallback."""
    agents: tuple[BaseApplicationAgent, ...] = (
        GreenhouseApplicationAgent(**agent_options),
        LeverApplicationAgent(**agent_options),
        WorkdayApplicationAgent(**agent_options),
        BrowserApplicationAgent(**agent_options),
    )
    return next((agent for agent in agents if agent.can_handle(job)), None)  # type: ignore[arg-type]


__all__ = [
    "ApplicationField",
    "ApplicationPlan",
    "ApplicationValidation",
    "ApplicationNeedsReviewError",
    "ApplicationProvider",
    "BaseApplicationAgent",
    "BrowserSubmissionResult",
    "application_eligibility_errors",
    "BrowserApplicationAgent",
    "GreenhouseApplicationAgent",
    "GenericApplicationAgent",
    "LeverApplicationAgent",
    "SubmissionResult",
    "WorkdayApplicationAgent",
    "CandidateProfile",
    "detect_provider",
    "detect_provider_from_url",
    "load_candidate_profile",
    "select_application_agent",
    "store_application_plan",
]
