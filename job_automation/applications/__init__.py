"""Application preparation adapters; submission is intentionally disabled."""

from job_automation.applications.base import (
    ApplicationField,
    ApplicationPlan,
    ApplicationValidation,
    BaseApplicationAgent,
    SubmissionResult,
    store_application_plan,
)
from job_automation.applications.browser_apply import BrowserApplicationAgent
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
    "BaseApplicationAgent",
    "BrowserApplicationAgent",
    "GreenhouseApplicationAgent",
    "LeverApplicationAgent",
    "SubmissionResult",
    "WorkdayApplicationAgent",
    "select_application_agent",
    "store_application_plan",
]
