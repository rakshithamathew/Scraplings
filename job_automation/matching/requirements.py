"""Deterministic extraction and comparison of basic job requirements."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
from unicodedata import normalize as unicode_normalize
from urllib.parse import urlsplit

from job_automation.normalizer import NormalizedJob, WorkplaceType


_WHITESPACE = re.compile(r"\s+")
_RANGE = re.compile(r"\b(\d{1,2})\s*(?:-|–|—|to)\s*(\d{1,2})\s*(?:\+\s*)?years?\b", re.I)
_MINIMUM = re.compile(
    r"\b(?:at\s+least|minimum(?:\s+of)?|min(?:imum)?\.?)\s*(\d{1,2})\s*(?:\+\s*)?years?\b",
    re.I,
)
_PLUS = re.compile(r"\b(\d{1,2})\s*\+\s*years?\b", re.I)
_UP_TO = re.compile(r"\b(?:up\s+to|maximum(?:\s+of)?|max(?:imum)?\.?)\s*(\d{1,2})\s*years?\b", re.I)
_PLAIN = re.compile(r"\b(\d{1,2})\s+years?\s+(?:of\s+)?(?:relevant\s+)?experience\b", re.I)
_BENGALURU = re.compile(r"\b(?:bengaluru|bangalore)(?:\s+urban)?\b", re.I)
_INTERNATIONAL = re.compile(
    r"\b(?:worldwide|global|work\s+from\s+anywhere|international candidates?|"
    r"candidates?\s+worldwide|visa sponsorship|relocation support|remote[^.]{0,30}india)\b",
    re.I,
)
_REMOTE_EXCLUSIONS = (
    re.compile(r"\b(?:remote\s*[-,/()]?\s*)?(?:us|u\.s\.|usa|united states)\s+only\b", re.I),
    re.compile(r"\b(?:must be|be)\s+(?:located|based|resident)\s+in\s+(?:the\s+)?(?:us|usa|united states)\b", re.I),
    re.compile(r"\bcanada\s+only\b", re.I),
    re.compile(r"\b(?:eu|european union)\s+(?:residents?\s+)?only\b", re.I),
    re.compile(r"\b(?:uk|united kingdom)\s+only\b", re.I),
    re.compile(r"\bremote\s+(?:in|within|[-,/])\s*(?:us|usa|united states|canada|uk|eu)\b", re.I),
)
_REMOTE_LOCATION_EXCLUSIONS = re.compile(
    r"\b(?:united states|u\.s\.|usa|canada|united kingdom|uk|europe|european union|eu|"
    r"emea|latin america|latam|australia|new zealand)\b",
    re.I,
)
_AVOID_TITLE_PATTERNS = (
    "data scientist",
    "machine learning researcher",
    "ml researcher",
    "machine learning engineer",
    "ml engineer",
    "devops engineer",
    "site reliability engineer",
    "cloud engineer",
    "database administrator",
    "network engineer",
    "security engineer",
    "cybersecurity",
    "qa engineer",
    "quality assurance",
    "manual tester",
    "android developer",
    "ios developer",
    "embedded",
    "firmware",
)


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


@dataclass(frozen=True, slots=True)
class HardFilterResult:
    eligible: bool
    category: str | None = None
    reason: str | None = None


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


def valid_public_application_url(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def remote_allows_india(location: str | None, description: str | None, *, require_evidence: bool = False) -> bool:
    text = " ".join(filter(None, (location, description)))
    if re.search(r"\b(?:excluding|except|not (?:available|open) (?:in|to))\s+india\b|\bindia\s+(?:is\s+)?(?:excluded|not eligible)\b", text, re.I):
        return False
    if any(pattern.search(text) for pattern in _REMOTE_EXCLUSIONS):
        return False
    # Portal cards commonly say only "United States" or "EMEA" while the
    # remote filter is active. Treat that location itself as a restriction;
    # otherwise worldwide searches would incorrectly qualify it for India.
    if location and _REMOTE_LOCATION_EXCLUSIONS.search(location):
        return False
    if not require_evidence:
        return True
    return bool(
        re.search(r"\b(?:india|bengaluru|bangalore)\b", location or "", re.I)
        or re.search(r"\b(?:worldwide|globally|work from anywhere|anywhere in the world)\b", text, re.I)
        or re.search(r"\b(?:remote\s+(?:in|from)|(?:open|available)\s+to\s+(?:candidates\s+(?:in|from)\s+)?)\s*india\b", text, re.I)
    )


def is_bengaluru_location(location: str | None) -> bool:
    return bool(location and _BENGALURU.search(location))


def role_is_relevant(
    title: str | None,
    description: str | None,
    target_titles: Iterable[str],
) -> bool:
    normalized_title = normalize_for_matching(title)
    if not normalized_title or any(pattern in normalized_title for pattern in _AVOID_TITLE_PATTERNS):
        return False
    title_tokens = set(normalized_title.split())
    for target in target_titles:
        normalized_target = normalize_for_matching(target)
        if not normalized_target:
            continue
        target_tokens = set(normalized_target.split())
        if normalized_target in normalized_title or normalized_title in normalized_target:
            return True
        if target_tokens and len(title_tokens & target_tokens) / len(target_tokens) >= 0.6:
            return True
    relevant_role = bool(
        title_tokens
        & {
            "frontend",
            "react",
            "angular",
            "javascript",
            "typescript",
            "ui",
            "web",
            "fullstack",
            "product",
        }
    ) and bool(title_tokens & {"engineer", "developer", "lead"})
    if relevant_role:
        return True
    if any(term in normalized_title for term in ("ai ", "genai", "llm")):
        context = normalize_for_matching(description)
        return any(term in context for term in ("frontend", "full stack", "fullstack", "react", "angular"))
    return False


def evaluate_hard_constraints(
    job: NormalizedJob,
    *,
    target_titles: Iterable[str],
) -> HardFilterResult:
    """Apply mandatory pre-scoring rules from the candidate requirements."""
    if job.is_open is False:
        return HardFilterResult(False, reason="Job is closed or expired")
    if not valid_public_application_url(job.application_url):
        return HardFilterResult(False, reason="Application URL is missing or invalid")
    if not role_is_relevant(job.title, job.description, target_titles):
        return HardFilterResult(False, reason="Role is not aligned with the candidate profile")
    if job.workplace_type is WorkplaceType.REMOTE:
        if not remote_allows_india(job.location, job.description, require_evidence=job.source == "linkedin"):
            return HardFilterResult(False, reason="Remote geography excludes India or India eligibility is unconfirmed")
        return HardFilterResult(True, category="remote_eligible")
    if job.workplace_type is WorkplaceType.HYBRID:
        if is_bengaluru_location(job.location):
            return HardFilterResult(True, category="bengaluru_hybrid")
        return HardFilterResult(False, reason="Hybrid role is outside Bengaluru")
    if job.workplace_type is WorkplaceType.ONSITE:
        if is_bengaluru_location(job.location):
            return HardFilterResult(True, category="bengaluru_onsite")
        return HardFilterResult(False, reason="Onsite role is outside Bengaluru")
    return HardFilterResult(False, reason="Workplace type is not explicit")
