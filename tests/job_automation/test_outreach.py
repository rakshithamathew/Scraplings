from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.database import Job, JobRepository, JobStatus, OutreachStatus, initialize_database
from job_automation.outreach import (
    ContactCandidate,
    EmailSendingDisabledError,
    GmailDraftWorkflow,
    generate_email_draft,
    select_relevant_contact,
)


def _job(*, status: JobStatus = JobStatus.QUALIFIED) -> Job:
    return Job(
        id=12,
        title="Frontend Engineer",
        company="Example Labs",
        source="test",
        application_url="https://example.test/jobs/12",
        status=status,
    )


def _contact(name: str, title: str, **changes: object) -> ContactCandidate:
    values: dict[str, object] = {
        "name": name,
        "email": f"{name.casefold().replace(' ', '.')}@example.test",
        "title": title,
        "company": "Example Labs",
        "verified": True,
    }
    values.update(changes)
    return ContactCandidate(**values)


def test_contact_priority_prefers_role_recruiter() -> None:
    contacts = [
        _contact("Taylor Talent", "Talent Acquisition Partner"),
        _contact("Morgan Manager", "Engineering Manager"),
        _contact(
            "Robin Recruiter",
            "Technical Recruiter",
            role_focus=["Frontend Engineering"],
        ),
    ]

    match = select_relevant_contact(_job(), contacts)

    assert match is not None
    assert match.contact.name == "Robin Recruiter"
    assert match.priority == 1


def test_contacts_must_match_company_and_be_verified() -> None:
    contacts = [
        _contact("Wrong Company", "Technical Recruiter", company="Other Company"),
        _contact("Unverified", "Talent Acquisition", verified=False),
    ]

    assert select_relevant_contact(_job(), contacts) is None


def test_ceo_is_excluded_and_founder_requires_small_company_confirmation() -> None:
    ceo = _contact("Casey CEO", "Chief Executive Officer")
    founder = _contact("Frank Founder", "Founder and CEO")

    assert select_relevant_contact(_job(), [ceo], small_company=True) is None
    assert select_relevant_contact(_job(), [founder], small_company=False) is None
    match = select_relevant_contact(_job(), [founder], small_company=True)
    assert match is not None
    assert match.priority == 5


@pytest.mark.parametrize("status", [JobStatus.QUALIFIED, JobStatus.APPLIED])
def test_email_draft_is_factual_and_requires_approval(status: JobStatus) -> None:
    contact = _contact("Robin Recruiter", "Technical Recruiter")

    draft = generate_email_draft(_job(status=status), contact, sender_name="Applicant")

    assert draft.to_email == "robin.recruiter@example.test"
    assert draft.outreach_status is OutreachStatus.DRAFTED
    assert draft.approval_status == "NEEDS_APPROVAL"
    assert draft.requires_user_approval is True
    assert "Job posting: https://example.test/jobs/12" in draft.body
    assert ("recently applied" in draft.body) is (status is JobStatus.APPLIED)


def test_email_draft_rejects_ineligible_job() -> None:
    with pytest.raises(ValueError, match="QUALIFIED or APPLIED"):
        generate_email_draft(
            _job(status=JobStatus.DISCOVERED),
            _contact("Robin Recruiter", "Technical Recruiter"),
            sender_name="Applicant",
        )


def test_gmail_boundary_never_sends() -> None:
    workflow = GmailDraftWorkflow()
    draft = workflow.create_draft(
        _job(),
        _contact("Robin Recruiter", "Technical Recruiter"),
        sender_name="Applicant",
    )

    with pytest.raises(EmailSendingDisabledError, match="sending is disabled"):
        workflow.send(draft)


def test_create_and_store_draft_updates_tracking(tmp_path: Path) -> None:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'outreach.db').as_posix()}")
    repository = JobRepository(engine)
    try:
        job = repository.create_job(
            title="Frontend Engineer",
            company="Example Labs",
            source="test",
            application_url="https://example.test/jobs/12",
            status=JobStatus.QUALIFIED,
        )
        assert job.outreach_status is OutreachStatus.NOT_STARTED

        draft, stored = GmailDraftWorkflow().create_and_store_draft(
            repository,
            job,
            _contact("Robin Recruiter", "Technical Recruiter"),
            sender_name="Applicant",
        )

        assert draft.approval_status == "NEEDS_APPROVAL"
        assert stored.contact_name == "Robin Recruiter"
        assert stored.contact_role == "Technical Recruiter"
        assert stored.contact_email == "robin.recruiter@example.test"
        assert stored.outreach_status is OutreachStatus.DRAFTED

        with pytest.raises(ValueError, match="already exists"):
            GmailDraftWorkflow().create_and_store_draft(
                repository,
                job,
                _contact("Robin Recruiter", "Technical Recruiter"),
                sender_name="Applicant",
            )
    finally:
        engine.dispose()
