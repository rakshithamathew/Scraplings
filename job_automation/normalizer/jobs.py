"""Canonical job schema and defensive source-record normalization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
from hashlib import sha256
import re
from typing import Any
from unicodedata import normalize as unicode_normalize
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, field_validator


_WHITESPACE = re.compile(r"\s+")
_COMMA_SPACING = re.compile(r"\s*,\s*")


def _clean_text(value: Any) -> str | None:
    """Convert a scalar-like value to clean text without guessing content."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("name", "text", "value"):
            if value.get(key) is not None:
                return _clean_text(value[key])
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float)):
        cleaned = _WHITESPACE.sub(" ", unicode_normalize("NFKC", str(value))).strip()
        return cleaned or None
    return None


def normalize_title(value: Any) -> str | None:
    """Normalize title whitespace while preserving the supplied wording."""
    return _clean_text(value)


def normalize_company(value: Any) -> str | None:
    """Normalize company whitespace without removing legal suffixes or branding."""
    return _clean_text(value)


def normalize_location(value: Any) -> str | None:
    """Normalize scalar, mapping, or list-shaped location values."""
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("location") or value.get("value")
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        parts = [_clean_text(part) for part in value]
        value = ", ".join(part for part in parts if part)

    cleaned = _clean_text(value)
    return _COMMA_SPACING.sub(", ", cleaned) if cleaned else None


def _normalize_url(value: Any, *, base_url: str | None = None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None

    candidate = urljoin(base_url, cleaned) if base_url else cleaned
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return cleaned

    if not parsed.scheme or not parsed.netloc:
        return cleaned.rstrip("/") or cleaned

    scheme = parsed.scheme.casefold()
    netloc = parsed.netloc.casefold()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _normalize_skills(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidates: Iterable[Any] = re.split(r"[,;\n]+", value)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, Mapping)):
        candidates = value
    else:
        candidates = (value,)

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = _clean_text(candidate)
        if cleaned and cleaned.casefold() not in seen:
            result.append(cleaned)
            seen.add(cleaned.casefold())
    return result or None


def _normalize_posted_at(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            timestamp = value / 1_000 if abs(value) >= 100_000_000_000 else value
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    try:
        if cleaned.endswith("Z"):
            cleaned = f"{cleaned[:-1]}+00:00"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
            return datetime.combine(date.fromisoformat(cleaned), time.min, tzinfo=timezone.utc)
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


class NormalizedJob(BaseModel):
    """Source-independent representation returned by every job scraper."""

    model_config = ConfigDict(extra="ignore")

    external_id: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    skills: list[str] | None = None
    source: str | None = None
    source_url: str | None = None
    application_url: str | None = None
    posted_at: datetime | None = None

    @field_validator("external_id", "description", "source", mode="before")
    @classmethod
    def clean_text_fields(cls, value: Any) -> str | None:
        return _clean_text(value)

    @field_validator("title", mode="before")
    @classmethod
    def clean_title(cls, value: Any) -> str | None:
        return normalize_title(value)

    @field_validator("company", mode="before")
    @classmethod
    def clean_company(cls, value: Any) -> str | None:
        return normalize_company(value)

    @field_validator("location", mode="before")
    @classmethod
    def clean_location(cls, value: Any) -> str | None:
        return normalize_location(value)

    @field_validator("source_url", "application_url", mode="before")
    @classmethod
    def clean_urls(cls, value: Any) -> str | None:
        return _normalize_url(value)

    @field_validator("skills", mode="before")
    @classmethod
    def clean_skills(cls, value: Any) -> list[str] | None:
        return _normalize_skills(value)

    @field_validator("posted_at", mode="before")
    @classmethod
    def clean_posted_at(cls, value: Any) -> datetime | None:
        return _normalize_posted_at(value)


_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "external_id": ("external_id", "id", "job_id", "jobId", "jobReqId", "requisition_id"),
    "title": ("title", "text", "job_title", "jobTitle"),
    "company": ("company", "company_name", "companyName", "organization"),
    "location": ("location", "location_name", "locationsText", "workplace_location"),
    "description": ("description", "descriptionPlain", "job_description", "jobDescription", "content"),
    "skills": ("skills", "skill_list", "skillList"),
    "source": ("source",),
    "source_url": ("source_url", "sourceUrl", "absolute_url", "hostedUrl", "hosted_url", "url", "externalPath"),
    "application_url": ("application_url", "applicationUrl", "applyUrl", "apply_url"),
    "posted_at": ("posted_at", "postedAt", "date_posted", "postedOn", "createdAt", "created_at"),
}


def _first_present(record: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in record and record[key] is not None:
            return record[key]
    return None


def normalize_job(
    raw_job: Mapping[str, Any] | None,
    *,
    source: str | None = None,
    base_url: str | None = None,
) -> NormalizedJob:
    """Convert a partial source record into the canonical job structure."""
    record: Mapping[str, Any] = raw_job if isinstance(raw_job, Mapping) else {}
    values = {field: _first_present(record, aliases) for field, aliases in _FIELD_ALIASES.items()}

    categories = record.get("categories")
    if values["location"] is None and isinstance(categories, Mapping):
        values["location"] = categories.get("location") or categories.get("allLocations")
    if source is not None:
        values["source"] = source
    if base_url:
        values["source_url"] = _normalize_url(values["source_url"], base_url=base_url)
        values["application_url"] = _normalize_url(values["application_url"], base_url=base_url)

    return NormalizedJob.model_validate(values)


def generate_job_fingerprint(job: NormalizedJob | Mapping[str, Any] | None) -> str:
    """Hash the normalized company, title, and application URL identity triple."""
    normalized = job if isinstance(job, NormalizedJob) else normalize_job(job)
    identity = "\x1f".join(
        (
            (normalize_company(normalized.company) or "").casefold(),
            (normalize_title(normalized.title) or "").casefold(),
            _normalize_url(normalized.application_url) or "",
        )
    )
    return sha256(identity.encode("utf-8")).hexdigest()
