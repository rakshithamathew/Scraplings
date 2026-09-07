"""Reusable, label-oriented Playwright form-field matching."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping

from job_automation.applications.candidate_profile import CandidateProfile
from job_automation.applications.questions import answer_known_question, generate_truthful_free_text


_FIELD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("first_name", re.compile(r"\bfirst\s*name\b|\bgiven\s*name\b", re.I)),
    ("last_name", re.compile(r"\blast\s*name\b|\bsurname\b|\bfamily\s*name\b", re.I)),
    ("full_name", re.compile(r"\bfull\s*name\b|\blegal\s*name\b|\byour\s*name\b", re.I)),
    ("email", re.compile(r"\be-?mail\b", re.I)),
    ("phone", re.compile(r"\bphone\b|\bmobile\b", re.I)),
    ("linkedin_url", re.compile(r"linkedin", re.I)),
    ("github_url", re.compile(r"github", re.I)),
    ("portfolio_url", re.compile(r"portfolio|personal\s+website|website\s+url", re.I)),
    ("current_company", re.compile(r"current\s+(?:company|employer)", re.I)),
    ("current_title", re.compile(r"current\s+(?:title|role|position)", re.I)),
    ("years_of_experience", re.compile(r"(?:total\s+)?years?\s+of\s+experience", re.I)),
    ("notice_period", re.compile(r"notice\s+period|available\s+to\s+(?:start|join)", re.I)),
    ("current_salary", re.compile(r"current\s+(?:salary|compensation)", re.I)),
    ("expected_salary", re.compile(r"expected\s+(?:salary|compensation)", re.I)),
    ("requires_sponsorship", re.compile(r"(?:require|need).*sponsorship", re.I)),
    ("work_authorization", re.compile(r"authori[sz]ed\s+to\s+work|work\s+authorization", re.I)),
    ("city", re.compile(r"\bcity\b", re.I)),
    ("state", re.compile(r"\bstate\b|province", re.I)),
    ("country", re.compile(r"\bcountry\b", re.I)),
    ("degree", re.compile(r"\bdegree\b|education", re.I)),
    ("university", re.compile(r"university|college|school", re.I)),
)

_LEGAL_OR_CONSENT = re.compile(
    r"terms|privacy|legal|declaration|certif(?:y|ication)|signature|accurate|truthful",
    re.I,
)

_STANDARD_PROFILE_FIELDS = {
    "first_name",
    "last_name",
    "full_name",
    "email",
    "phone",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "current_company",
    "current_title",
    "years_of_experience",
    "city",
    "state",
    "country",
    "degree",
    "university",
}


@dataclass(frozen=True, slots=True)
class FieldDescriptor:
    index: int
    tag: str
    input_type: str
    name: str
    field_id: str
    value: str
    text: str
    required: bool


@dataclass(slots=True)
class FormFillResult:
    fields_filled: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    resume_attached: bool = False


def identify_profile_field(text: str) -> str | None:
    """Classify a field from semantic text rather than DOM position."""
    return next((name for name, pattern in _FIELD_PATTERNS if pattern.search(text)), None)


class FormFieldMatcher:
    """Inspect and fill standard HTML controls through Playwright locators."""

    selector = "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select, [role=combobox]"

    def __init__(self, page: Any):
        self.page = page

    async def descriptors(self) -> list[tuple[FieldDescriptor, Any]]:
        controls = self.page.locator(self.selector)
        result: list[tuple[FieldDescriptor, Any]] = []
        for index in range(await controls.count()):
            locator = controls.nth(index)
            try:
                metadata: Mapping[str, Any] = await locator.evaluate(
                    """element => {
                      const explicit = element.id ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`) : null;
                      const wrapping = element.closest('label');
                      const group = element.closest('fieldset, [role=group], .field, .form-field, .application-question');
                      return {
                        tag: element.tagName.toLowerCase(),
                        type: (element.getAttribute('type') || '').toLowerCase(),
                        name: element.getAttribute('name') || '',
                        id: element.id || '',
                        value: element.getAttribute('value') || '',
                        required: element.required || element.getAttribute('aria-required') === 'true',
                        text: [explicit?.innerText, wrapping?.innerText, element.getAttribute('aria-label'),
                          element.getAttribute('placeholder'), element.getAttribute('name'), element.id,
                          group?.innerText].filter(Boolean).join(' ').slice(0, 1000)
                      };
                    }"""
                )
            except Exception:
                continue
            descriptor = FieldDescriptor(
                index=index,
                tag=str(metadata.get("tag", "")),
                input_type=str(metadata.get("type", "")),
                name=str(metadata.get("name", "")),
                field_id=str(metadata.get("id", "")),
                value=str(metadata.get("value", "")),
                text=" ".join(str(metadata.get("text", "")).split()),
                required=bool(metadata.get("required")),
            )
            result.append((descriptor, locator))
        return result

    async def fill_candidate_details(self, profile: CandidateProfile) -> FormFillResult:
        result = FormFillResult()
        for descriptor, locator in await self.descriptors():
            if descriptor.input_type == "file":
                continue
            if descriptor.input_type in {"radio", "checkbox"}:
                continue
            profile_field = identify_profile_field(descriptor.text)
            if not profile_field or profile_field not in _STANDARD_PROFILE_FIELDS:
                continue
            value = profile.configured_value(profile_field)
            if not value:
                if descriptor.required:
                    result.unresolved_questions.append(descriptor.text or descriptor.name)
                continue
            if await self._fill_control(locator, descriptor, value):
                result.fields_filled.append(profile_field)
        return result

    async def upload_resume(self, path: str) -> bool:
        inputs = self.page.locator('input[type="file"]')
        for index in range(await inputs.count()):
            locator = inputs.nth(index)
            try:
                await locator.set_input_files(path)
                return True
            except Exception:
                continue
        return False

    async def fill_questions(
        self,
        profile: CandidateProfile,
        *,
        job_title: str,
        company: str,
        matched_skills: list[str] | tuple[str, ...] = (),
        already_filled: set[str] | None = None,
    ) -> FormFillResult:
        result = FormFillResult()
        known_fields = already_filled or set()
        for descriptor, locator in await self.descriptors():
            if descriptor.input_type in {"file", "hidden", "submit", "button"}:
                continue
            profile_field = identify_profile_field(descriptor.text)
            if profile_field and profile_field in known_fields:
                continue
            if _LEGAL_OR_CONSENT.search(descriptor.text):
                if descriptor.required:
                    result.unresolved_questions.append(descriptor.text or descriptor.name)
                continue
            answer = answer_known_question(descriptor.text, profile)
            if answer.answer is None and descriptor.tag == "textarea":
                answer = generate_truthful_free_text(
                    descriptor.text,
                    profile=profile,
                    job_title=job_title,
                    company=company,
                    matched_skills=matched_skills,
                )
            if answer.answer and await self._fill_control(locator, descriptor, answer.answer):
                result.fields_filled.append(answer.source_field or descriptor.name or "custom_question")
            elif descriptor.required and descriptor.input_type not in {"radio", "checkbox"}:
                result.unresolved_questions.append(descriptor.text or descriptor.name or "Required question")
        return result

    @staticmethod
    async def _fill_control(locator: Any, descriptor: FieldDescriptor, value: str) -> bool:
        try:
            if descriptor.tag == "select":
                try:
                    await locator.select_option(label=value)
                except Exception:
                    await locator.select_option(value=value)
                return True
            if descriptor.input_type in {"checkbox", "radio"}:
                if _LEGAL_OR_CONSENT.search(descriptor.text):
                    return False
                normalized = value.casefold().strip()
                control_value = descriptor.value.casefold().strip()
                affirmative = {"yes", "true", "1", "y"}
                negative = {"no", "false", "0", "n"}
                should_check = (
                    normalized in affirmative and (not control_value or control_value in affirmative)
                ) or (normalized in negative and control_value in negative)
                if should_check:
                    await locator.check()
                    return True
                return False
            if descriptor.tag in {"input", "textarea"}:
                await locator.fill(value)
            else:
                await locator.click()
                await locator.press_sequentially(value)
                await locator.press("Enter")
            return True
        except Exception:
            return False
