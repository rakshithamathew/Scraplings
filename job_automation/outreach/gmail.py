"""Local Gmail-oriented draft workflow. Transport is intentionally disabled."""

from __future__ import annotations

from typing import NoReturn

from job_automation.database import Job, JobRepository
from job_automation.outreach.contacts import ContactCandidate, ContactMatch
from job_automation.outreach.email_generator import EmailDraft, OutreachJob, generate_email_draft


class EmailSendingDisabledError(RuntimeError):
    """Raised whenever code attempts to cross the disabled sending boundary."""


class GmailDraftWorkflow:
    """Create and optionally record one local draft; never call Gmail or Composio."""

    def create_draft(
        self,
        job: OutreachJob,
        contact: ContactCandidate | ContactMatch,
        *,
        sender_name: str,
    ) -> EmailDraft:
        return generate_email_draft(job, contact, sender_name=sender_name)

    def create_and_store_draft(
        self,
        repository: JobRepository,
        job: OutreachJob,
        contact: ContactCandidate | ContactMatch,
        *,
        sender_name: str,
    ) -> tuple[EmailDraft, Job]:
        draft = self.create_draft(job, contact, sender_name=sender_name)
        stored = repository.record_outreach_draft(
            draft.job_id,
            contact_name=draft.contact_name,
            contact_email=draft.to_email,
        )
        if stored is None:
            raise ValueError(f"Job {draft.job_id} no longer exists")
        return draft, stored

    def send(self, draft: EmailDraft) -> NoReturn:
        """Hard stop: sending requires a future approval-aware implementation."""
        raise EmailSendingDisabledError(
            "Email sending is disabled; this workflow only creates drafts that need user approval"
        )
