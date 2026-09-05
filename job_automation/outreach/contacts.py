"""Deterministic selection from user-supplied, verified contact records."""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from job_automation.matching.requirements import normalize_for_matching


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ContactJob(Protocol):
    title: str
    company: str


class ContactCandidate(BaseModel):
    """A contact supplied by the user or a future verified contact source."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    role_focus: list[str] = Field(default_factory=list)
    source_url: str | None = None
    verified: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if not _EMAIL.fullmatch(value):
            raise ValueError("email must be a plausible address")
        return value.casefold()

    @field_validator("role_focus")
    @classmethod
    def clean_role_focus(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class ContactMatch(BaseModel):
    contact: ContactCandidate
    priority: int = Field(ge=1, le=5)
    reason: str


def select_relevant_contact(
    job: ContactJob,
    contacts: list[ContactCandidate],
    *,
    small_company: bool = False,
    require_verified: bool = True,
) -> ContactMatch | None:
    """Choose one relevant contact according to the documented priority order."""
    company = normalize_for_matching(job.company)
    matches: list[ContactMatch] = []
    for contact in contacts:
        if require_verified and not contact.verified:
            continue
        if normalize_for_matching(contact.company) != company:
            continue
        ranked = _contact_priority(job.title, contact, small_company=small_company)
        if ranked is not None:
            priority, reason = ranked
            matches.append(ContactMatch(contact=contact, priority=priority, reason=reason))
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda match: (
            match.priority,
            match.contact.name.casefold(),
            match.contact.email.casefold(),
        ),
    )[0]


def _contact_priority(
    job_title: str,
    contact: ContactCandidate,
    *,
    small_company: bool,
) -> tuple[int, str] | None:
    title = normalize_for_matching(contact.title)
    is_recruiter = "recruiter" in title or "recruiting" in title
    if is_recruiter and _focus_matches(job_title, contact.role_focus):
        return 1, "Recruiter responsible for the role"
    if is_recruiter or "talent acquisition" in title or "talent partner" in title:
        return 2, "Recruiting or talent acquisition"
    if "hiring manager" in title:
        return 3, "Hiring manager"
    if any(role in title for role in ("engineering manager", "software manager", "development manager")):
        return 4, "Engineering manager"
    is_founder_or_cto = "founder" in title or title == "cto" or "chief technology officer" in title
    if small_company and is_founder_or_cto:
        return 5, "Founder or CTO at a user-confirmed small company"
    # CEOs and unrelated executives/managers are intentionally not eligible.
    return None


def _focus_matches(job_title: str, role_focus: list[str]) -> bool:
    job_tokens = set(normalize_for_matching(job_title).split())
    return any(
        bool(job_tokens & set(normalize_for_matching(focus).split()))
        for focus in role_focus
        if normalize_for_matching(focus)
    )
