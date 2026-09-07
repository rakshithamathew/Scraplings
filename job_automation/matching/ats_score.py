"""Explainable, deterministic compatibility scoring for normalized jobs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_automation.matching.requirements import (
    ExperienceRequirement,
    evaluate_hard_constraints,
    extract_experience_requirement,
    matched_terms,
    normalize_for_matching,
)
from job_automation.normalizer import NormalizedJob


class UserProfile(BaseModel):
    """Candidate matching facts derived from the active uploaded resume."""

    model_config = ConfigDict(extra="forbid")

    target_titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    frontend_fullstack_skills: list[str] = Field(default_factory=list)
    domain_keywords: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    minimum_experience: int | None = Field(default=None, ge=0)
    maximum_experience: int | None = Field(default=None, ge=0)
    keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_experience_range(self) -> "UserProfile":
        if (
            self.minimum_experience is not None
            and self.maximum_experience is not None
            and self.minimum_experience > self.maximum_experience
        ):
            raise ValueError("minimum_experience cannot exceed maximum_experience")
        return self


class ScoreBreakdown(BaseModel):
    """Auditable component scores and matches for one job."""

    title: float
    skills: float
    required_skills: float = 0
    preferred_skills: float = 0
    frontend_fullstack_relevance: float = 0
    domain_relevance: float = 0
    keywords: float
    location: float
    experience: float
    excluded_penalty: float
    total: float
    matched_skills: tuple[str, ...] = ()
    missing_required_skills: tuple[str, ...] = ()
    missing_preferred_skills: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    matched_excluded_keywords: tuple[str, ...] = ()
    extracted_experience_minimum: int | None = None
    extracted_experience_maximum: int | None = None
    experience_match: bool = False
    title_match: bool = False
    location_match: bool = False
    reason: str = ""


def _ratio(matches: tuple[str, ...], configured: list[str]) -> float:
    unique = {normalize_for_matching(term) for term in configured if normalize_for_matching(term)}
    return 1.0 if not unique else min(1.0, len(matches) / len(unique))


def _title_ratio(title: str | None, targets: list[str]) -> float:
    if not targets:
        return 1.0
    normalized_title = normalize_for_matching(title)
    if not normalized_title:
        return 0.0
    title_tokens = set(normalized_title.split())
    best = 0.0
    for target in targets:
        normalized_target = normalize_for_matching(target)
        if not normalized_target:
            continue
        if normalized_target in normalized_title or normalized_title in normalized_target:
            return 1.0
        target_tokens = set(normalized_target.split())
        if target_tokens:
            best = max(best, len(title_tokens & target_tokens) / len(target_tokens))
    return best


def score_job(job: NormalizedJob, profile: UserProfile) -> ScoreBreakdown:
    """Score an eligible job using the requirements-document component weights."""
    searchable = " ".join(
        value for value in (job.title, job.description, " ".join(job.skills or [])) if value
    )
    explicit_required = job.required_skills or job.skills or []
    explicit_preferred = job.preferred_skills or []
    required_basis = explicit_required or profile.skills
    candidate_skill_text = " ".join(
        (*profile.skills, *profile.preferred_skills, *profile.frontend_fullstack_skills)
    )
    explicit_skill_matches = matched_terms(explicit_required, candidate_skill_text)
    # Listing cards (especially public job portals) frequently omit a structured
    # requirements list.  In that case, measure the candidate skills actually
    # mentioned by the available job text instead of dividing by every skill on
    # the resume.  The latter incorrectly penalised broader resumes and made a
    # strong React match score lower merely because the candidate also knew SQL,
    # Azure, Python, etc.
    observed_skill_matches = matched_terms(profile.skills, searchable)
    skill_matches = tuple(dict.fromkeys((*explicit_skill_matches, *observed_skill_matches)))
    preferred_matches = matched_terms(explicit_preferred, candidate_skill_text)
    frontend_matches = matched_terms(profile.frontend_fullstack_skills, searchable)
    domain_matches = matched_terms(profile.domain_keywords, searchable)
    keyword_matches = matched_terms(profile.keywords, searchable)
    excluded_matches = matched_terms(profile.excluded_keywords, searchable)
    requirement = (
        ExperienceRequirement(job.minimum_experience, job.maximum_experience)
        if job.minimum_experience is not None or job.maximum_experience is not None
        else extract_experience_requirement(job.description)
    )

    title_ratio = _title_ratio(job.title, profile.target_titles)
    title_points = 20.0 * title_ratio
    if explicit_required:
        explicit_points = 30.0 * _ratio(explicit_skill_matches, explicit_required)
        observed_points = min(30.0, 7.5 * len(observed_skill_matches))
        skill_points = max(explicit_points, observed_points)
    else:
        # Sparse public cards provide weaker evidence than a full description.
        # Four independently observed CV skills are required for full points.
        skill_points = min(30.0, 7.5 * len(observed_skill_matches))
    preferred_points = 5.0 if not explicit_preferred else 10.0 * _ratio(preferred_matches, explicit_preferred)
    frontend_points = 10.0 * min(1.0, len(frontend_matches) / 4.0)
    domain_points = 5.0 if domain_matches else 0.0
    decision = evaluate_hard_constraints(job, target_titles=profile.target_titles)
    location_points = 10.0 if decision.eligible else 0.0
    known_years = profile.minimum_experience
    experience_match = requirement is None or requirement.minimum is None or (
        known_years is not None and known_years >= requirement.minimum
    )
    experience_points = 7.5 if requirement is None else (15.0 if experience_match else 0.0)
    keyword_points = 5.0 * _ratio(keyword_matches, profile.keywords)
    excluded_penalty = 0.0
    total = max(
        0.0,
        min(
            100.0,
            title_points
            + skill_points
            + preferred_points
            + frontend_points
            + domain_points
            + keyword_points
            + location_points
            + experience_points
            - excluded_penalty,
        ),
    )
    values: dict[str, Any] = {
        "title": round(title_points, 2),
        "skills": round(skill_points, 2),
        "required_skills": round(skill_points, 2),
        "preferred_skills": round(preferred_points, 2),
        "frontend_fullstack_relevance": round(frontend_points, 2),
        "domain_relevance": round(domain_points, 2),
        "keywords": round(keyword_points, 2),
        "location": round(location_points, 2),
        "experience": round(experience_points, 2),
        "excluded_penalty": round(excluded_penalty, 2),
        "total": round(total, 2),
        "matched_skills": skill_matches,
        "missing_required_skills": tuple(
            skill for skill in explicit_required if skill not in explicit_skill_matches
        ),
        "missing_preferred_skills": tuple(skill for skill in explicit_preferred if skill not in preferred_matches),
        "matched_keywords": keyword_matches,
        "matched_excluded_keywords": excluded_matches,
        "extracted_experience_minimum": requirement.minimum if requirement else None,
        "extracted_experience_maximum": requirement.maximum if requirement else None,
        "experience_match": experience_match,
        "title_match": title_ratio >= 0.6,
        "location_match": decision.eligible,
        "reason": (
            f"Title {title_points:.1f}/20, required skills {skill_points:.1f}/30, "
            f"experience {experience_points:.1f}/15, frontend/full-stack {frontend_points:.1f}/10, "
            f"preferred skills {preferred_points:.1f}/10, domain {domain_points:.1f}/5, "
            f"keywords {keyword_points:.1f}/5, "
            f"location {location_points:.1f}/10."
        ),
    }
    return ScoreBreakdown.model_validate(values)
