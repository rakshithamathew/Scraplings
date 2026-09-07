"""Deterministic generation of reviewable, one-to-one outreach drafts."""

from __future__ import annotations

import re
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict

from job_automation.database import JobStatus, OutreachStatus
from job_automation.outreach.contacts import ContactCandidate, ContactMatch


_LINE_BREAKS = re.compile(r"[\r\n]+")
_ELIGIBLE_STATUSES = {JobStatus.QUALIFIED.value, JobStatus.APPLIED.value}


class OutreachJob(Protocol):
    id: int
    title: str
    company: str
    status: JobStatus | str
    application_url: str


class EmailDraft(BaseModel):
    """Local draft content. It is never delivered by this model."""

    model_config = ConfigDict(extra="forbid")

    job_id: int
    contact_name: str
    contact_role: str
    to_email: str
    subject: str
    body: str
    outreach_status: Literal[OutreachStatus.DRAFTED] = OutreachStatus.DRAFTED
    approval_status: Literal["NEEDS_APPROVAL"] = "NEEDS_APPROVAL"
    requires_user_approval: Literal[True] = True


def generate_email_draft(
    job: OutreachJob,
    contact: ContactCandidate | ContactMatch,
    *,
    sender_name: str,
) -> EmailDraft:
    """Generate a factual draft without inventing qualifications or relationships."""
    status = job.status.value if isinstance(job.status, JobStatus) else str(job.status)
    if status not in _ELIGIBLE_STATUSES:
        raise ValueError("Outreach drafts are limited to QUALIFIED or APPLIED jobs")
    candidate = contact.contact if isinstance(contact, ContactMatch) else contact
    safe_sender = _single_line(sender_name)
    if not safe_sender:
        raise ValueError("sender_name is required")
    safe_title = _single_line(job.title)
    safe_company = _single_line(job.company)
    subject_prefix = "Applied" if status == JobStatus.APPLIED.value else "Interest"
    opening = (
        f"I recently applied for the {safe_title} role at {safe_company}."
        if status == JobStatus.APPLIED.value
        else f"I'm interested in the {safe_title} role at {safe_company}."
    )
    body = "\n".join(
        (
            f"Hi {_single_line(candidate.name)},",
            "",
            opening,
            "I would appreciate any guidance you can share about the role or the appropriate hiring contact.",
            "",
            f"Job posting: {job.application_url}",
            "",
            f"Best,\n{safe_sender}",
        )
    )
    return EmailDraft(
        job_id=job.id,
        contact_name=candidate.name,
        contact_role=candidate.title,
        to_email=candidate.email,
        subject=f"{subject_prefix}: {safe_title} at {safe_company}",
        body=body,
    )


def _single_line(value: str) -> str:
    return _LINE_BREAKS.sub(" ", value).strip()
