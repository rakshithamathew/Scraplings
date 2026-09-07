"""Deterministic selection of an existing base resume for a job."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from job_automation.matching.requirements import matched_terms, normalize_for_matching


LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESUME_CONFIG_PATH = PROJECT_ROOT / "config" / "resumes.json"


class JobLike(Protocol):
    title: str | None
    description: str | None
    skills: list[str] | None


class ResumeMetadata(BaseModel):
    """User-maintained metadata for one resume file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    skills: list[str] = Field(default_factory=list)
    target_roles: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    @field_validator("skills", "target_roles", "keywords")
    @classmethod
    def clean_terms(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class ResumeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resumes: list[ResumeMetadata] = Field(default_factory=list)


class ResumeSelection(BaseModel):
    """Explainable result returned by :class:`ResumeSelector`."""

    resume: ResumeMetadata
    score: float
    role_score: float
    skill_score: float
    keyword_score: float


def load_resume_config(path: str | Path = DEFAULT_RESUME_CONFIG_PATH) -> ResumeConfig:
    """Load and validate resume metadata without opening any resume files."""
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Resume configuration not found: {config_path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Unable to read resume configuration {config_path}: {error}") from error
    try:
        return ResumeConfig.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"Invalid resume configuration {config_path}: {error}") from error


def _term_ratio(terms: list[str], text: str) -> float:
    configured = {normalize_for_matching(term) for term in terms if normalize_for_matching(term)}
    if not configured:
        return 0.0
    return len(matched_terms(terms, text)) / len(configured)


def _role_ratio(title: str | None, roles: list[str]) -> float:
    normalized_title = normalize_for_matching(title)
    if not normalized_title or not roles:
        return 0.0
    title_tokens = set(normalized_title.split())
    best = 0.0
    for role in roles:
        normalized_role = normalize_for_matching(role)
        if not normalized_role:
            continue
        if normalized_role in normalized_title or normalized_title in normalized_role:
            return 1.0
        role_tokens = set(normalized_role.split())
        if role_tokens:
            best = max(best, len(title_tokens & role_tokens) / len(role_tokens))
    return best


class ResumeSelector:
    """Choose the highest-scoring configured resume that exists on disk."""

    def __init__(
        self,
        config: ResumeConfig,
        *,
        project_root: str | Path = PROJECT_ROOT,
        resume_directory: str | Path | None = None,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.resume_directory = Path(resume_directory or self.project_root / "resumes").resolve()
        self._available_paths = {
            resume.path: resolved
            for resume in self.config.resumes
            if (resolved := self.resolve_path(resume)) is not None
        }

    @classmethod
    def from_file(
        cls,
        config_path: str | Path = DEFAULT_RESUME_CONFIG_PATH,
        *,
        project_root: str | Path = PROJECT_ROOT,
        resume_directory: str | Path | None = None,
    ) -> "ResumeSelector":
        return cls(
            load_resume_config(config_path),
            project_root=project_root,
            resume_directory=resume_directory,
        )

    def resolve_path(self, resume: ResumeMetadata) -> Path | None:
        candidate = (self.project_root / resume.path).resolve()
        try:
            candidate.relative_to(self.resume_directory)
        except ValueError:
            LOGGER.warning("Ignoring resume outside configured resume directory: %s", resume.path)
            return None
        if not candidate.is_file():
            LOGGER.info("Configured resume file is not present: %s", resume.path)
            return None
        return candidate

    def select(self, job: JobLike) -> ResumeSelection | None:
        """Return the best existing resume, or ``None`` when none has a positive match."""
        searchable = " ".join(
            value
            for value in (
                job.title,
                job.description,
                " ".join(job.skills or []),
            )
            if value
        )
        candidates: list[ResumeSelection] = []
        for resume in self.config.resumes:
            if resume.path not in self._available_paths:
                continue
            role_score = 50.0 * _role_ratio(job.title, resume.target_roles)
            skill_score = 35.0 * _term_ratio(resume.skills, searchable)
            keyword_score = 15.0 * _term_ratio(resume.keywords, searchable)
            score = round(role_score + skill_score + keyword_score, 2)
            if score > 0:
                candidates.append(
                    ResumeSelection(
                        resume=resume,
                        score=score,
                        role_score=round(role_score, 2),
                        skill_score=round(skill_score, 2),
                        keyword_score=round(keyword_score, 2),
                    )
                )
        if not candidates:
            return None
        return sorted(
            candidates,
            key=lambda item: (-item.score, item.resume.name.casefold(), item.resume.path.casefold()),
        )[0]
