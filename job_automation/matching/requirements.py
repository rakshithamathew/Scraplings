"""Deterministic extraction and comparison of basic job requirements."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from unicodedata import normalize as unicode_normalize


_WHITESPACE = re.compile(r"\s+")
_RANGE = re.compile(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:\+\s*)?years?\b", re.I)
_MINIMUM = re.compile(
    r"\b(?:at\s+least|minimum(?:\s+of)?|min(?:imum)?\.?)\s*(\d{1,2})\s*(?:\+\s*)?years?\b",
    re.I,
)
_PLUS = re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I)
_UP_TO = re.compile(r"\b(?:up\s+to|maximum(?:\s+of)?|max(?:imum)?\.?)\s*(\d{1,2})\s*years?\b", re.I)
_PLAIN = re.compile(r"\b(\d{1,2})\s+years?\s+(?:of\s+)?(?:relevant\s+)?experience\b", re.I)


def normalize_for_matching(value: str | None) -> str:
    """Create a case-insensitive, whitespace-stable comparison string."""
    if not value:
        return ""
    return _WHITESPACE.sub(" ", unicode_normalize("NFKC", value).casefold()).strip()


def term_is_present(term: str, text: str) -> bool:
    """Match a term on word boundaries, including multi-word terms."""
    normalized_term = normalize_for_matching(term)
    if not normalized_term:
        return False
    pattern = r"(?<!\w)" + re.escape(normalized_term).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, normalize_for_matching(text)) is not None


def matched_terms(terms: Iterable[str], text: str) -> tuple[str, ...]:
    """Return configured terms found in text, preserving configuration order."""
    matches: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = normalize_for_matching(term)
        if key and key not in seen and term_is_present(term, text):
            matches.append(term)
            seen.add(key)
    return tuple(matches)


@dataclass(frozen=True, slots=True)
class ExperienceRequirement:
    minimum: int | None = None
    maximum: int | None = None


def extract_experience_requirement(text: str | None) -> ExperienceRequirement | None:
    """Extract one simple years-of-experience interval from job text."""
    if not text:
        return None
    if match := _RANGE.search(text):
        lower, upper = int(match.group(1)), int(match.group(2))
        return ExperienceRequirement(min(lower, upper), max(lower, upper))
    if match := _MINIMUM.search(text):
        return ExperienceRequirement(minimum=int(match.group(1)))
    if match := _PLUS.search(text):
        return ExperienceRequirement(minimum=int(match.group(1)))
    if match := _UP_TO.search(text):
        return ExperienceRequirement(maximum=int(match.group(1)))
    if match := _PLAIN.search(text):
        return ExperienceRequirement(minimum=int(match.group(1)))
    return None


def experience_compatibility(
    requirement: ExperienceRequirement | None,
    profile_minimum: int | None,
    profile_maximum: int | None,
) -> float:
    """Return 0..1 compatibility between a job and the profile's target range."""
    if profile_minimum is None and profile_maximum is None:
        return 1.0
    if requirement is None:
        return 0.5

    profile_low = float("-inf") if profile_minimum is None else profile_minimum
    profile_high = float("inf") if profile_maximum is None else profile_maximum
    job_low = float("-inf") if requirement.minimum is None else requirement.minimum
    job_high = float("inf") if requirement.maximum is None else requirement.maximum
    if max(profile_low, job_low) <= min(profile_high, job_high):
        return 1.0
    gap = job_low - profile_high if job_low > profile_high else profile_low - job_high
    return 0.5 if gap <= 1 else 0.0
