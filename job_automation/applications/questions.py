"""Truthful deterministic answers for application questions."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

from job_automation.applications.candidate_profile import CandidateProfile


@dataclass(frozen=True, slots=True)
class QuestionAnswer:
    answer: str | None
    source_field: str | None
    reason: str


_QUESTION_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"first name", re.I), "first_name"),
    (re.compile(r"last name|surname", re.I), "last_name"),
    (re.compile(r"full name|legal name", re.I), "full_name"),
    (re.compile(r"e-?mail", re.I), "email"),
    (re.compile(r"phone|mobile", re.I), "phone"),
    (re.compile(r"linkedin", re.I), "linkedin_url"),
    (re.compile(r"github", re.I), "github_url"),
    (re.compile(r"portfolio|personal website", re.I), "portfolio_url"),
    (re.compile(r"current company|current employer", re.I), "current_company"),
    (re.compile(r"current title|current role", re.I), "current_title"),
    (re.compile(r"total.*years.*experience|years of experience", re.I), "years_of_experience"),
    (re.compile(r"notice period|available to (?:start|join)", re.I), "notice_period"),
    (re.compile(r"current (?:salary|compensation)", re.I), "current_salary"),
    (re.compile(r"expected (?:salary|compensation)", re.I), "expected_salary"),
    (re.compile(r"require.*sponsorship|need.*sponsorship", re.I), "requires_sponsorship"),
    (re.compile(r"authorized to work.*india|work authorization.*india", re.I), "work_authorization"),
    (re.compile(r"university|college", re.I), "university"),
    (re.compile(r"degree|education", re.I), "degree"),
    (re.compile(r"city", re.I), "city"),
    (re.compile(r"state", re.I), "state"),
    (re.compile(r"country", re.I), "country"),
)


def answer_known_question(question: str, profile: CandidateProfile) -> QuestionAnswer:
    """Return a configured fact or explicitly leave the question unresolved."""
    for pattern, field_name in _QUESTION_RULES:
        if pattern.search(question):
            if field_name in {"current_salary", "expected_salary"} and re.search(
                r"\b(?:usd|dollars?|eur|euros?|gbp|pounds?|currency)\b",
                question,
                re.I,
            ):
                return QuestionAnswer(
                    None,
                    field_name,
                    "Salary currency is not configured and cannot be inferred",
                )
            value = profile.configured_value(field_name)
            if value:
                return QuestionAnswer(value, field_name, f"Configured candidate field: {field_name}")
            return QuestionAnswer(None, field_name, f"Candidate field is unresolved: {field_name}")
    return QuestionAnswer(None, None, "No truthful deterministic mapping exists")


def generate_truthful_free_text(
    question: str,
    *,
    profile: CandidateProfile,
    job_title: str,
    company: str,
    matched_skills: list[str] | tuple[str, ...] = (),
) -> QuestionAnswer:
    """Generate restrained text using only explicitly supplied facts."""
    normalized = question.casefold()
    relevant = ", ".join(matched_skills[:5])
    if "why" in normalized and ("role" in normalized or "company" in normalized or "join" in normalized):
        parts = [f"I am interested in the {job_title} role at {company}."]
        if profile.current_title and profile.current_company:
            parts.append(f"I currently work as {profile.current_title} at {profile.current_company}.")
        if relevant:
            parts.append(f"My relevant skills include {relevant}.")
        return QuestionAnswer(" ".join(parts), "configured profile and matched skills", "Factual free-text response")
    if "relevant experience" in normalized and (profile.current_title or relevant):
        parts = []
        if profile.current_title:
            parts.append(f"My current role is {profile.current_title}.")
        if relevant:
            parts.append(f"My relevant skills include {relevant}.")
        return QuestionAnswer(" ".join(parts), "configured profile and matched skills", "Factual experience response")
    return QuestionAnswer(None, None, "Optional free-text question cannot be answered safely")
