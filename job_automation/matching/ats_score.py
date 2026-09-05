"""Explainable, deterministic compatibility scoring for normalized jobs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from job_automation.matching.requirements import (
    experience_compatibility,
    extract_experience_requirement,
    matched_terms,
    normalize_for_matching,
)
from job_automation.normalizer import NormalizedJob


class UserProfile(BaseModel):
    """User-controlled matching criteria loaded from ``config/profile.json``."""

    model_config = ConfigDict(extra="forbid")

    target_titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
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
    keywords: float
    location: float
    experience: float
    excluded_penalty: float
    total: float
    matched_skills: tuple[str, ...] = ()
    matched_keywords: tuple[str, ...] = ()
    matched_excluded_keywords: tuple[str, ...] = ()
    extracted_experience_minimum: int | None = None
    extracted_experience_maximum: int | None = None


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


def _location_ratio(location: str | None, preferences: list[str]) -> float:
    if not preferences:
        return 1.0
    return 1.0 if matched_terms(preferences, location or "") else 0.0


def score_job(job: NormalizedJob, profile: UserProfile) -> ScoreBreakdown:
    """Score a job from 0 to 100 using fixed, documented component weights."""
    searchable = " ".join(
        value for value in (job.title, job.description, " ".join(job.skills or [])) if value
    )
    skill_matches = matched_terms(profile.skills, searchable)
    keyword_matches = matched_terms(profile.keywords, searchable)
    excluded_matches = matched_terms(profile.excluded_keywords, searchable)
    requirement = extract_experience_requirement(job.description)

    title_points = 30.0 * _title_ratio(job.title, profile.target_titles)
    skill_points = 30.0 * _ratio(skill_matches, profile.skills)
    keyword_points = 15.0 * _ratio(keyword_matches, profile.keywords)
    location_points = 15.0 * _location_ratio(job.location, profile.preferred_locations)
    experience_points = 10.0 * experience_compatibility(
        requirement,
        profile.minimum_experience,
        profile.maximum_experience,
    )
    excluded_penalty = min(40.0, 20.0 * len(excluded_matches))
    total = max(
        0.0,
        min(
            100.0,
            title_points + skill_points + keyword_points + location_points + experience_points - excluded_penalty,
        ),
    )
    values: dict[str, Any] = {
        "title": round(title_points, 2),
        "skills": round(skill_points, 2),
        "keywords": round(keyword_points, 2),
        "location": round(location_points, 2),
        "experience": round(experience_points, 2),
        "excluded_penalty": round(excluded_penalty, 2),
        "total": round(total, 2),
        "matched_skills": skill_matches,
        "matched_keywords": keyword_matches,
        "matched_excluded_keywords": excluded_matches,
        "extracted_experience_minimum": requirement.minimum if requirement else None,
        "extracted_experience_maximum": requirement.maximum if requirement else None,
    }
    return ScoreBreakdown.model_validate(values)
