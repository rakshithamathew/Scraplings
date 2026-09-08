"""Send one authorized LinkedIn job message using saved authentication and durable claims."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from job_automation.auth import SessionManager
from job_automation.database import DEFAULT_DATABASE_URL, JobRepository, JobStatus, initialize_database
from job_automation.database.models import ActiveResume, Job, JobContact, LinkedInOutreach, LinkedInOutreachGate, OutreachMessage
from job_automation.outreach.contact_repository import ContactRepository
from job_automation.outreach.discovery import linkedin_url
from job_automation.outreach.email_automation import EmailLimits, utc
from job_automation.outreach.linkedin_browser import LinkedInCheckpoint, LinkedInMessagingBrowser, LinkedInUnavailable, job_url
from job_automation.outreach.personalized import PROJECT_ROOT, PersonalizedOutreachService, clean, input_fingerprint, words

CONSUMED = {'SENDING', 'SENT', 'UNKNOWN', 'ALREADY_PRESENT'}


@dataclass
class LinkedInSummary:
    job_id: int | None = None
    status: str = 'NO_ELIGIBLE_JOB'
    linkedin_message_sent: bool = False
    detail: str | None = None


class LinkedInOutreachService:
    def __init__(self, repository, *, project_root=PROJECT_ROOT, session_manager=None,
                 browser_factory=LinkedInMessagingBrowser, clock=None, limits=None):
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.manager = session_manager or SessionManager(self.project_root / 'data' / 'auth', project_root=self.project_root)
        self.browser_factory = browser_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.limits = limits or EmailLimits()

    def _acquire(self):
        now, token = utc(self.clock()), uuid4().hex
        with Session(self.repository.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            gate = session.get(LinkedInOutreachGate, 1) or LinkedInOutreachGate(id=1)
            if gate.lease_token and gate.lease_expires_at and utc(gate.lease_expires_at) > now:
                return None, 'Another LinkedIn outreach worker holds the saved profile'
            if gate.next_allowed_at and utc(gate.next_allowed_at) > now:
                return None, f'Next send permitted at {utc(gate.next_allowed_at).isoformat()}'
            attempts = list(session.scalars(select(LinkedInOutreach.attempted_at).where(
                LinkedInOutreach.attempted_at > now - timedelta(days=1))))
            if len(attempts) >= self.limits.rolling_day_limit:
                return None, 'Rolling 24-hour LinkedIn attempt limit reached'
            gate.lease_token, gate.lease_expires_at = token, now + timedelta(minutes=15)
            session.add(gate)
            session.commit()
        return token, None

    def _release(self, token):
        with Session(self.repository.engine) as session, session.begin():
            gate = session.get(LinkedInOutreachGate, 1)
            if gate and gate.lease_token == token:
                gate.lease_token = gate.lease_expires_at = None

    def _active_cv(self, session):
        active = session.get(ActiveResume, 1)
        if not active or not active.active:
            raise ValueError('An active uploaded CV is required')
        try:
            data = (self.project_root / active.path).read_bytes()
        except OSError:
            raise ValueError('Active CV file is unavailable') from None
        if sha256(data).hexdigest() != active.sha256:
            raise ValueError('Active CV differs from its uploaded hash')
        return active

    def _record(self, job, status, detail=None, *, contact=None, message=None):
        with Session(self.repository.engine) as session, session.begin():
            row = session.get(LinkedInOutreach, job.id)
            if row and row.status in CONSUMED:
                return
            row = row or LinkedInOutreach(job_id=job.id, job_key=job_url(job.source_url) or job_url(job.application_url) or str(job.id))
            row.status, row.detail = status, detail
            if contact:
                row.contact_id = contact.id
                row.contact_url = linkedin_url(contact.linkedin_url)
                row.contact = {key: getattr(contact, key) for key in ('id', 'name', 'title', 'company', 'linkedin_url', 'source')}
            if message:
                row.message, row.draft_input_hash = message.linkedin_message, message.input_hash
                row.message_hash = sha256(message.linkedin_message.encode()).hexdigest()
            if status == 'ALREADY_PRESENT':
                row.linkedin_message_sent = True
            session.add(row)

    def _claim(self, job_id, contact_id, token, expected_message):
        with Session(self.repository.engine) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            now = utc(self.clock())
            gate = session.get(LinkedInOutreachGate, 1)
            if not gate or gate.lease_token != token or utc(gate.lease_expires_at) <= now:
                raise ValueError('Browser worker lease expired before dispatch')
            job, contact = session.get(Job, job_id), session.get(JobContact, contact_id)
            draft, active = session.get(OutreachMessage, job_id), self._active_cv(session)
            if not job or job.status not in {JobStatus.QUALIFIED, JobStatus.APPLIED} or job.match_score is None or job.match_score <= 70 or job.is_open is False:
                raise ValueError('Job is no longer eligible for outreach')
            if not contact or contact.job_id != job.id or clean(contact.company).casefold() != clean(job.company).casefold():
                raise ValueError('Contact is no longer related to this job')
            snapshot = {key: getattr(contact, key) for key in ('id', 'name', 'title', 'company', 'work_email', 'linkedin_url', 'source')}
            if not draft or draft.status != 'DRAFTED' or draft.contact_id != contact.id or draft.linkedin_message != expected_message or not 50 <= words(expected_message) <= 80:
                raise ValueError('Generated LinkedIn message changed before dispatch')
            if draft.resume_sha256 != active.sha256 or draft.input_hash != input_fingerprint(job, active.sha256, snapshot, []):
                raise ValueError('Job, contact, or CV changed; regenerate the message')
            key, profile = job_url(job.source_url) or job_url(job.application_url), linkedin_url(contact.linkedin_url)
            if not key or not profile:
                raise ValueError('LinkedIn job and person URLs are required')
            previous = session.scalar(select(LinkedInOutreach).where(LinkedInOutreach.job_key == key,
                LinkedInOutreach.contact_url == profile, LinkedInOutreach.status.in_(CONSUMED)))
            row = session.get(LinkedInOutreach, job_id)
            if previous or (row and row.status in CONSUMED):
                return False
            row = row or LinkedInOutreach(job_id=job_id, job_key=key)
            row.contact_url, row.contact = profile, snapshot
            row.contact_id = contact.id
            row.status, row.attempted_at = 'SENDING', now
            row.message, row.draft_input_hash = draft.linkedin_message, draft.input_hash
            row.message_hash = sha256(draft.linkedin_message.encode()).hexdigest()
            row.detail = 'Dispatch reserved; never automatically repeat this claim'
            session.add(row)
            gate.next_allowed_at = now + timedelta(seconds=self.limits.minimum_interval_seconds)
            session.commit()
            return True

    def _finish(self, job_id, status, detail):
        with Session(self.repository.engine) as session, session.begin():
            row = session.get(LinkedInOutreach, job_id)
            row.status, row.detail = status, detail
            row.error = detail if status == 'UNKNOWN' else None
            if status == 'SENT':
                row.linkedin_message_sent, row.sent_at = True, utc(self.clock())

    async def run(self, job_id=None):
        with Session(self.repository.engine) as session:
            query = select(Job).where(Job.match_score > 70, Job.status.in_([JobStatus.QUALIFIED, JobStatus.APPLIED])).order_by(Job.match_score.desc(), Job.id)
            if job_id is not None:
                query = query.where(Job.id == job_id)
            jobs = list(session.scalars(query))
            selected = None
            for job in jobs:
                if job.is_open is False or not (job_url(job.source_url) or job_url(job.application_url)):
                    continue
                previous = session.get(LinkedInOutreach, job.id)
                if previous and (previous.status in CONSUMED or (job_id is None and previous.status in {'BLOCKED', 'UNAVAILABLE'})):
                    if job_id is not None:
                        return LinkedInSummary(job.id, 'DUPLICATE', bool(previous.linkedin_message_sent), 'Existing delivery/uncertain claim prevents another send')
                    continue
                selected = job
                break
            if selected is None:
                return LinkedInSummary()
            try:
                self._active_cv(session)
            except ValueError as error:
                self._record(selected, 'WAITING_INPUT', str(error))
                return LinkedInSummary(selected.id, 'WAITING_INPUT', detail=str(error))
        job = selected
        if not self.manager.is_connected('linkedin'):
            self._record(job, 'BLOCKED', 'Saved LinkedIn authorization is unavailable')
            return LinkedInSummary(job.id, 'BLOCKED', detail='Complete normal LinkedIn authorization; no login was attempted')
        token, reason = self._acquire()
        if not token:
            return LinkedInSummary(job.id, 'RATE_LIMITED', detail=reason)
        browser = self.browser_factory(self.manager)
        claimed = False
        contact = message = None
        try:
            await browser.start()
            details = await browser.open_job(job_url(job.source_url) or job_url(job.application_url), job.company)
            if details.description:
                self.repository.update_job(job.id, description=details.description, description_complete=True)
                job = self.repository.get_job(job.id)
            contacts = ContactRepository(self.repository.engine)
            if details.poster:
                contacts.save(job.id, details.poster)
                contact = next((item for item in contacts.list(job.id) if linkedin_url(item.linkedin_url) == linkedin_url(details.poster.linkedin_url)), None)
            else:
                contact = next((item for item in contacts.list(job.id) if linkedin_url(item.linkedin_url)
                    and clean(item.company).casefold() == clean(job.company).casefold() and 1 <= item.priority <= 7), None)
            if not contact:
                self._record(job, 'WAITING_INPUT', 'No job poster or saved relevant LinkedIn contact is available')
                return LinkedInSummary(job.id, 'WAITING_INPUT', detail='No relevant LinkedIn contact')
            generator = PersonalizedOutreachService(self.repository, project_root=self.project_root)
            generator.run(job.id, contact_id=contact.id, allow_applied=True)
            message = generator.list(job.id)[0]
            if message.status != 'DRAFTED' or not message.linkedin_message:
                self._record(job, 'WAITING_INPUT', '; '.join(message.missing_inputs), contact=contact)
                return LinkedInSummary(job.id, 'WAITING_INPUT', detail='; '.join(message.missing_inputs))
            await browser.open_profile(contact)
            if await browser.prepare_message(message.linkedin_message):
                self._record(job, 'ALREADY_PRESENT', 'Exact outgoing message is already visible; original send time unavailable', contact=contact, message=message)
                return LinkedInSummary(job.id, 'ALREADY_PRESENT', True, 'Existing outgoing message was not sent again')
            claimed = self._claim(job.id, contact.id, token, message.linkedin_message)
            if not claimed:
                return LinkedInSummary(job.id, 'DUPLICATE', detail='Existing person/job delivery claim')
            confirmation = await browser.send_prepared(message.linkedin_message)
            self._finish(job.id, 'SENT', confirmation)
            return LinkedInSummary(job.id, 'SENT', True, confirmation)
        except LinkedInCheckpoint as error:
            self.manager.mark_disconnected('linkedin', str(error))
            if claimed:
                self._finish(job.id, 'UNKNOWN', 'Verification encountered after send was reserved; no automatic retry')
            else:
                self._record(job, 'BLOCKED', str(error), contact=contact, message=message)
            return LinkedInSummary(job.id, 'UNKNOWN' if claimed else 'BLOCKED', detail=str(error))
        except (LinkedInUnavailable, ValueError) as error:
            if claimed:
                self._finish(job.id, 'UNKNOWN', str(error))
            else:
                self._record(job, 'UNAVAILABLE', str(error), contact=contact, message=message)
            return LinkedInSummary(job.id, 'UNKNOWN' if claimed else 'UNAVAILABLE', detail=str(error))
        except Exception:
            if claimed:
                self._finish(job.id, 'UNKNOWN', 'Send result unconfirmed; inspect the conversation before manual recovery')
            else:
                self._record(job, 'UNAVAILABLE', 'LinkedIn page or messaging layout could not be verified', contact=contact, message=message)
            return LinkedInSummary(job.id, 'UNKNOWN' if claimed else 'UNAVAILABLE', detail='No automatic retry')
        finally:
            try:
                await browser.close()
            finally:
                self._release(token)

    def deliveries(self, job_id=None):
        with Session(self.repository.engine) as session:
            query = select(LinkedInOutreach).order_by(LinkedInOutreach.job_id)
            if job_id is not None:
                query = query.where(LinkedInOutreach.job_id == job_id)
            return [{key: (getattr(row, key).isoformat() if isinstance(getattr(row, key), datetime) else getattr(row, key))
                for key in ('job_id', 'status', 'linkedin_message_sent', 'sent_at', 'attempted_at', 'contact', 'message', 'detail')}
                for row in session.scalars(query)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=DEFAULT_DATABASE_URL)
    parser.add_argument('--job-id', type=int)
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    engine = initialize_database(args.database_url)
    try:
        service = LinkedInOutreachService(JobRepository(engine))
        result = service.deliveries(args.job_id) if args.status else asdict(asyncio.run(service.run(args.job_id)))
        print(json.dumps(result, indent=2))
        return int(not args.status and result['status'] in {'BLOCKED', 'UNKNOWN', 'UNAVAILABLE'})
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
