"""Validated candidate facts used by application automation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE_PROFILE_PATH = PROJECT_ROOT / "config" / "candidate_profile.json"


class CandidateProfile(BaseModel):
    """Only explicitly configured facts; absent values remain unresolved."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    years_of_experience: str | None = None
    current_company: str | None = None
    current_title: str | None = None
    education: str | None = None
    degree: str | None = None
    university: str | None = None
    notice_period: str | None = None
    current_salary: str | None = None
    expected_salary: str | None = None
    work_authorization: str | None = None
    requires_sponsorship: str | None = None
    preferred_locations: list[str] = Field(default_factory=list)
    remote_preference: str | None = None

    @field_validator("preferred_locations", mode="before")
    @classmethod
    def normalize_locations(cls, value: Any) -> list[str]:
        if value is None:
            return []
        return [value] if isinstance(value, str) else list(value)

    def configured_value(self, field_name: str) -> str | None:
        value = getattr(self, field_name, None)
        if isinstance(value, list):
            return ", ".join(value) or None
        return value


def load_candidate_profile(
    path: str | Path = DEFAULT_CANDIDATE_PROFILE_PATH,
) -> CandidateProfile:
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        return CandidateProfile.model_validate(payload)
    except FileNotFoundError as error:
        raise ValueError(f"Candidate profile not found: {profile_path}") from error
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        raise ValueError(f"Invalid candidate profile {profile_path}: {error}") from error
