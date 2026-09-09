"""Dashboard projections over existing public contacts, drafts and receipts."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database.models import Job, JobContact, OutreachMessage, OutreachEmail, LinkedInOutreach, ContactDiscoveryTask
from job_automation.outreach.personalized import clean


def outreach_dashboard(repository):
    with Session(repository.engine) as session:
        jobs = list(session.scalars(select(Job)))
        contacts = {}
        for contact in session.scalars(select(JobContact).order_by(JobContact.priority, JobContact.name, JobContact.id)):
            contacts.setdefault(contact.job_id, []).append(contact)
        drafts = {row.job_id: row for row in session.scalars(select(OutreachMessage))}
        emails = {row.job_id: row for row in session.scalars(select(OutreachEmail))}
        messages = {row.job_id: row for row in session.scalars(select(LinkedInOutreach))}
        tasks = {row.job_id: row for row in session.scalars(select(ContactDiscoveryTask))}
        rows = []
        for job in jobs:
            contact = next((c for c in contacts.get(job.id, [])
                if clean(c.company).casefold() == clean(job.company).casefold()), None)
            draft, email, message = drafts.get(job.id), emails.get(job.id), messages.get(job.id)
            email_status = 'FOUND' if contact and contact.work_email else 'NOT_FOUND'
            if contact and contact.work_email and draft and draft.status == 'DRAFTED' and draft.contact_id == contact.id and draft.email_body:
                email_status = 'DRAFTED'
            if email and email.status == 'SENT':
                email_status = 'SENT'
            linkedin_status = 'FOUND' if contact and contact.linkedin_url else 'NOT_FOUND'
            if message and message.linkedin_message_sent:
                linkedin_status = 'SENT'
            rows.append({
                'job_id': job.id, 'contact_id': contact.id if contact else None,
                'contact_name': contact.name if contact else None,
                'contact_role': contact.title if contact else None,
                'public_work_email': contact.work_email if contact else None,
                'linkedin_url': contact.linkedin_url if contact else None,
                'contact_source': contact.source if contact else None,
                'discovery_status': tasks[job.id].status if job.id in tasks else 'PENDING',
                'discovery_detail': tasks[job.id].detail if job.id in tasks else 'Contact discovery has not run for this job',
                'email_status': email_status, 'linkedin_status': linkedin_status,
                'email_attempted': email is not None,
                'email_detail': (email.error or email.status) if email else None,
                'email_recipient': email.recipient if email else None,
                'linkedin_detail': message.detail if message else None,
                'application_confirmed': bool(job.application_confirmation or job.external_application_id),
            })
        return {'jobs': rows, 'contacts_found': sum(bool(row['contact_id']) for row in rows),
            'emails_sent': sum(row['email_status'] == 'SENT' for row in rows),
            'linkedin_messages_sent': sum(row['linkedin_status'] == 'SENT' for row in rows)}
