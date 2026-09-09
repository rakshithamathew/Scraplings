"""Persistence for public contacts, separate from selected outreach recipients."""
from hashlib import sha256

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from job_automation.database.models import ContactDiscoveryTask, Job, JobContact
from job_automation.database.repository import normalize_identity_text


class ContactRepository:
    def __init__(self, engine):
        self.engine = engine

    def tasks(self, job_id=None):
        with Session(self.engine) as session:
            if job_id is not None:
                job = session.get(Job, job_id)
                if job is None:
                    raise ValueError('Job does not exist')
                session.execute(insert(ContactDiscoveryTask).values(
                    job_id=job.id, company=normalize_identity_text(job.company), status='PENDING'
                ).on_conflict_do_nothing())
                session.commit()
            query = select(ContactDiscoveryTask)
            if job_id is None:
                query = query.where(ContactDiscoveryTask.status == 'PENDING')
            else:
                query = query.where(ContactDiscoveryTask.job_id == job_id)
            return list(session.scalars(query.order_by(ContactDiscoveryTask.job_id)))

    def finish(self, job_id, status, detail=None):
        with Session(self.engine) as session, session.begin():
            task = session.get(ContactDiscoveryTask, job_id)
            if task:
                task.status, task.detail = status, detail

    def save(self, job_id, contact):
        values = contact.model_dump()
        with Session(self.engine) as session, session.begin():
            job = session.get(Job, job_id)
            if job is None or normalize_identity_text(job.company) != normalize_identity_text(contact.company):
                raise ValueError('Contact must belong to the related job company')
            identity = sha256((normalize_identity_text(contact.company) + '\x1f' + normalize_identity_text(contact.name)).encode()).hexdigest()
            matches = [JobContact.identity_key == identity]
            if contact.linkedin_url:
                matches.append(JobContact.linkedin_url == contact.linkedin_url)
            if contact.work_email:
                matches.append(JobContact.work_email == contact.work_email)
            existing = session.scalar(select(JobContact).where(JobContact.job_id == job_id, or_(*matches)))
            if existing:
                # Keep the highest-priority relationship and enrich missing public fields.
                if contact.priority < existing.priority:
                    existing.priority = contact.priority
                    existing.title, existing.source, existing.evidence = contact.title, contact.source, contact.evidence
                if not existing.linkedin_url:
                    existing.linkedin_url = contact.linkedin_url
                if not existing.work_email:
                    existing.work_email = contact.work_email
                    if contact.work_email:
                        existing.source, existing.evidence = contact.source, contact.evidence
                return False
            statement = insert(JobContact).values(job_id=job_id, identity_key=identity, **values).on_conflict_do_nothing()
            return session.execute(statement).rowcount == 1

    def list(self, job_id):
        with Session(self.engine) as session:
            return list(session.scalars(select(JobContact).where(JobContact.job_id == job_id).order_by(JobContact.priority, JobContact.name)))
