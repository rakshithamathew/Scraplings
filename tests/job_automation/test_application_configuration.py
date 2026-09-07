from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.applications.candidate_profile import CandidateProfile, load_candidate_profile
from job_automation.applications.detector import ApplicationProvider, detect_provider_from_url
from job_automation.applications.questions import answer_known_question, generate_truthful_free_text
from job_automation.applications.service import load_application_settings


def test_candidate_profile_loads_explicit_values() -> None:
    profile = load_candidate_profile()

    assert profile.full_name == "Rakshitha M"
    assert profile.country == "India"
    assert profile.preferred_locations == ["Bengaluru"]


def test_missing_candidate_profile_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not found"):
        load_candidate_profile(tmp_path / "missing.json")


def test_dry_run_environment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")

    assert load_application_settings().dry_run is False


@pytest.mark.parametrize(
    ("url", "provider"),
    [
        ("https://job-boards.greenhouse.io/acme/jobs/1", ApplicationProvider.GREENHOUSE),
        ("https://jobs.lever.co/acme/1/apply", ApplicationProvider.LEVER),
        ("https://acme.wd5.myworkdayjobs.com/jobs/1", ApplicationProvider.WORKDAY),
        ("https://jobs.ashbyhq.com/acme/1", ApplicationProvider.ASHBY),
        ("https://jobs.smartrecruiters.com/acme/1", ApplicationProvider.SMARTRECRUITERS),
        ("https://careers.example.test/apply", ApplicationProvider.GENERIC),
    ],
)
def test_provider_detection(url: str, provider: ApplicationProvider) -> None:
    assert detect_provider_from_url(url) is provider


def test_question_answer_uses_only_configured_value() -> None:
    profile = CandidateProfile(work_authorization="YES")

    known = answer_known_question("Are you authorized to work in India?", profile)
    unknown = answer_known_question("How many years of React experience?", profile)

    assert known.answer == "YES"
    assert known.source_field == "work_authorization"
    assert unknown.answer is None

    other_country = answer_known_question("Are you authorized to work in the United States?", profile)
    assert other_country.answer is None


def test_salary_currency_is_not_invented() -> None:
    profile = CandidateProfile(expected_salary="16,00,000")

    assert answer_known_question("What is your expected salary?", profile).answer == "16,00,000"
    assert answer_known_question("What is your expected salary in USD?", profile).answer is None


def test_free_text_uses_only_supplied_facts() -> None:
    answer = generate_truthful_free_text(
        "Why are you interested in this role?",
        profile=CandidateProfile(current_title="Development Team Lead", current_company="kaizen Que"),
        job_title="Frontend Engineer",
        company="Example",
        matched_skills=["React", "TypeScript"],
    )

    assert answer.answer is not None
    assert "Development Team Lead" in answer.answer
    assert "React" in answer.answer
    assert "achievement" not in answer.answer.casefold()
