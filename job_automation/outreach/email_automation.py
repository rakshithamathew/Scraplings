"""Rate-limited, at-most-once email dispatch for personalized job outreach."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import time
from tempfile import TemporaryDirectory

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from job_automation.database import DEFAULT_DATABASE_URL, JobRepository, JobStatus, OutreachStatus, initialize_database
from job_automation.database.models import ActiveResume, Job, JobContact, OutreachEmail, OutreachMessage, OutreachSendGate
from job_automation.integrations.gmail_sender import ComposioGmailTransport, DeliveryUncertain, ProviderRejected
from job_automation.outreach.discovery import PERSONAL_EMAIL
from job_automation.outreach.personalized import PROJECT_ROOT, PersonalizedOutreachService, input_fingerprint


@dataclass(frozen=True)
class EmailLimits:
    minimum_interval_seconds: int = 600
    rolling_day_limit: int = 10

    def __post_init__(self):
        if self.minimum_interval_seconds < 600 or not 1 <= self.rolling_day_limit <= 10:
            raise ValueError('Use at least 600 seconds between attempts and at most 10 attempts per rolling day')


@dataclass
class SendSummary:
    considered: int = 0
    sent: int = 0
    duplicates: int = 0
    skipped: list[dict] = field(default_factory=list)
    rate_limited: bool = False
    next_allowed_at: str | None = None
    failed: int = 0
    uncertain: int = 0
    configuration_error: str | None = None


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class EmailAutomationService:
    def __init__(self, repository, *, project_root=PROJECT_ROOT, transport=None, limits=None, clock=None):
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.upload_dir = self.project_root / 'data' / 'outreach_uploads'
        self.transport = transport or ComposioGmailTransport(upload_dir=self.upload_dir)
        self.limits = limits or EmailLimits()
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _candidate(self, session, job_id):
        job = session.get(Job, job_id)
        if job is None or job.status not in {JobStatus.QUALIFIED, JobStatus.APPLIED} or job.match_score is None or job.match_score <= 70:
            raise ValueError('Job must be qualified/applied with ATS strictly above 70')
        if job.is_open is False or job.outreach_status in {OutreachStatus.SENT, OutreachStatus.REPLIED}:
            raise ValueError('Job is closed or already has sent outreach')
        message = session.get(OutreachMessage, job.id)
        if message is None or message.status != 'DRAFTED' or not message.email_subject or not message.email_body:
            raise ValueError('A generated personalized email is required')
        active = session.get(ActiveResume, 1)
        if active is None or not active.active or message.resume_sha256 != active.sha256:
            raise ValueError('Draft must use the current active uploaded CV')
        if job.description_complete is not True or not job.description:
            raise ValueError('Complete job description is required')
        contacts = list(session.scalars(select(JobContact).where(
            JobContact.job_id == job.id, JobContact.work_email.is_not(None)
        ).order_by(JobContact.priority, JobContact.name)))
        contact = next((c for c in contacts if c.company.casefold().strip() == job.company.casefold().strip() and 1 <= c.priority <= 7), None)
        if not contact or contact.id != message.contact_id:
            raise ValueError('Regenerate outreach for the highest-priority contact with a public work email')
        recipient = contact.work_email.strip().casefold()
        if not re.fullmatch(r'[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+', recipient) or recipient.rsplit('@', 1)[1] in PERSONAL_EMAIL:
            raise ValueError('One publicly sourced professional recipient is required')
        snapshot = {key: getattr(contact, key) for key in ('id', 'name', 'title', 'company', 'work_email', 'linkedin_url', 'source')}
        if message.contact_snapshot != snapshot or message.input_hash != input_fingerprint(job, active.sha256, snapshot, []):
            raise ValueError('CV, job, or contact changed; regenerate the personalized email')
        if '\n' in message.email_subject or '\r' in message.email_subject:
            raise ValueError('Email subject must be a single line')
        path = (self.project_root / active.path).resolve()
        if path.suffix.lower() not in {'.pdf', '.docx'}:
            raise ValueError('Active resume must be PDF or DOCX')
        try:
            content = path.read_bytes()
        except OSError:
            raise ValueError('Active uploaded CV file is unavailable') from None
        if sha256(content).hexdigest() != active.sha256:
            raise ValueError('Active CV file differs from its uploaded hash')
        filename = Path(active.original_filename).name
        filename = re.sub(r'[^\w. -]', '_', filename)
        if Path(filename).suffix.lower() != path.suffix.lower():
            filename = 'Resume' + path.suffix.lower()
        return job, message, contact, active, content, filename, recipient

    def _claim(self, job_id, account_id):
        # A SQLite write reservation serializes the duplicate check and global quota.
        with Session(self.repository.engine, expire_on_commit=False) as session:
            session.execute(text('BEGIN IMMEDIATE'))
            try:
                if session.scalar(select(OutreachEmail.id).where(OutreachEmail.job_id == job_id)) is not None:
                    session.rollback()
                    return 'duplicate', None, None
                job, message, contact, active, content, filename, recipient = self._candidate(session, job_id)
                now = utc(self.clock())
                gate = session.get(OutreachSendGate, 1)
                if gate and gate.next_allowed_at and utc(gate.next_allowed_at) > now:
                    next_at = utc(gate.next_allowed_at).isoformat()
                    session.rollback()
                    return 'rate_limited', next_at, None
                attempts = list(session.scalars(select(OutreachEmail.attempted_at).where(
                    OutreachEmail.attempted_at > now - timedelta(days=1)
                ).order_by(OutreachEmail.attempted_at)))
                if len(attempts) >= self.limits.rolling_day_limit:
                    next_at = (utc(attempts[0]) + timedelta(days=1)).isoformat()
                    session.rollback()
                    return 'rate_limited', next_at, None
                gate = gate or OutreachSendGate(id=1)
                gate.next_allowed_at = now + timedelta(seconds=self.limits.minimum_interval_seconds)
                session.add(gate)
                attempt = OutreachEmail(job_id=job.id, contact_id=contact.id, recipient=recipient,
                    contact_name=contact.name, company=job.company, status='SENDING',
                    subject=message.email_subject, body=message.email_body,
                    resume_sha256=active.sha256, resume_filename=filename,
                    draft_input_hash=message.input_hash, connected_account_id=account_id, attempted_at=now)
                session.add(attempt)
                session.commit()
                return 'claimed', attempt, content
            except Exception:
                session.rollback()
                raise

    def _finish(self, attempt_id, status, *, message_id=None, error=None):
        with Session(self.repository.engine) as session, session.begin():
            attempt = session.get(OutreachEmail, attempt_id)
            attempt.status, attempt.error = status, error
            if status == 'SENT':
                attempt.sent_at = utc(self.clock())
                attempt.provider_message_id = message_id
                job = session.get(Job, attempt.job_id)
                if job:
                    job.outreach_status = OutreachStatus.SENT
                    job.outreach_sent_at = attempt.sent_at
                    job.contact_name = attempt.contact_name
                    job.contact_email = attempt.recipient

    def run(self, job_id=None):
        """Dispatch at most one email. Repeated invocations obey the durable cooldown."""
        summary = SendSummary()
        # Refresh only qualified jobs. An already generated draft can also serve an applied job.
        generator = PersonalizedOutreachService(self.repository, project_root=self.project_root, prefer_email_contact=True)
        job = self.repository.get_job(job_id) if job_id is not None else None
        if job_id is None or (job and job.status in {JobStatus.QUALIFIED, JobStatus.APPLIED}):
            generator.run(job_id, allow_applied=True)
        with Session(self.repository.engine) as session:
            query = select(Job.id).where(Job.match_score > 70, Job.status.in_([JobStatus.QUALIFIED, JobStatus.APPLIED])).order_by(Job.match_score.desc(), Job.id)
            if job_id is not None:
                query = query.where(Job.id == job_id)
            ids = list(session.scalars(query))
        verified_account = None
        for candidate_id in ids:
            summary.considered += 1
            with Session(self.repository.engine) as session:
                if session.scalar(select(OutreachEmail.id).where(OutreachEmail.job_id == candidate_id)) is not None:
                    summary.duplicates += 1
                    continue
                try:
                    self._candidate(session, candidate_id)
                except ValueError as error:
                    summary.skipped.append({'job_id': candidate_id, 'reason': str(error)})
                    continue
            if verified_account is None:
                try:
                    verified_account = self.transport.verify()
                except Exception:
                    summary.configuration_error = 'Local Composio Gmail configuration or active connected account is unavailable'
                    break
            try:
                state, claim, content = self._claim(candidate_id, verified_account)
            except ValueError as error:
                summary.skipped.append({'job_id': candidate_id, 'reason': str(error)})
                continue
            if state == 'duplicate':
                summary.duplicates += 1
                continue
            if state == 'rate_limited':
                summary.rate_limited, summary.next_allowed_at = True, claim
                break
            # Immutable copy of the active CV captured at claim time. Only this directory
            # is allowlisted for Composio's upload; no alternate resume is substituted.
            try:
                self.upload_dir.mkdir(parents=True, exist_ok=True)
                with TemporaryDirectory(dir=self.upload_dir) as temporary:
                    attachment = Path(temporary) / claim.resume_filename
                    attachment.write_bytes(content)
                    message_id = self.transport.send(recipient=claim.recipient, subject=claim.subject,
                        body=claim.body, attachment_path=attachment)
                self._finish(claim.id, 'SENT', message_id=message_id)
                summary.sent += 1
            except ProviderRejected:
                self._finish(claim.id, 'FAILED', error='Provider reported failure; automatic retry disabled')
                summary.failed += 1
            except Exception:
                # Includes a crash-equivalent failure after provider success but before local
                # acknowledgement. Keep the claim; never replay a possibly accepted email.
                self._finish(claim.id, 'UNKNOWN', error='Delivery uncertain; inspect Gmail Sent and provider logs before recovery')
                summary.uncertain += 1
            break
        return summary

    def deliveries(self, job_id=None):
        with Session(self.repository.engine) as session:
            query = select(OutreachEmail).order_by(OutreachEmail.attempted_at.desc())
            if job_id is not None:
                query = query.where(OutreachEmail.job_id == job_id)
            return [{key: (getattr(row, key).isoformat() if isinstance(getattr(row, key), datetime) else getattr(row, key))
                for key in ('id', 'job_id', 'contact_id', 'recipient', 'status', 'attempted_at', 'sent_at',
                            'provider_message_id', 'resume_sha256', 'resume_filename', 'error')}
                for row in session.scalars(query)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=DEFAULT_DATABASE_URL)
    parser.add_argument('--job-id', type=int)
    parser.add_argument('--status', action='store_true', help='Read delivery history without sending')
    parser.add_argument('--watch', action='store_true', help='Process at most one email every ten minutes until stopped')
    args = parser.parse_args()
    if args.watch and args.status:
        parser.error('--watch cannot be combined with --status')
    engine = initialize_database(args.database_url)
    try:
        service = EmailAutomationService(JobRepository(engine))
        while True:
            result = service.deliveries(args.job_id) if args.status else asdict(service.run(args.job_id))
            print(json.dumps(result, indent=2), flush=True)
            if not args.watch:
                return int(bool(isinstance(result, dict) and (result['configuration_error'] or result['failed'] or result['uncertain'])))
            time.sleep(600)
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
