"""Contact selection and draft-first outreach boundaries."""

from .contacts import ContactCandidate, ContactMatch, select_relevant_contact
from .email_generator import EmailDraft, generate_email_draft
from .gmail import EmailSendingDisabledError, GmailDraftWorkflow

__all__ = [
    "ContactCandidate",
    "ContactMatch",
    "EmailDraft",
    "EmailSendingDisabledError",
    "GmailDraftWorkflow",
    "generate_email_draft",
    "select_relevant_contact",
]
