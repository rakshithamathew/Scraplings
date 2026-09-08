from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database import JobStatus, OutreachStatus
from job_automation.database.models import JobContact, OutreachEmail, OutreachMessage
from job_automation.integrations.composio import ComposioConfig, ComposioConfigurationError
from job_automation.integrations.gmail_sender import ComposioGmailTransport, DeliveryUncertain, ProviderRejected
from job_automation.outreach.contact_repository import ContactRepository
from job_automation.outreach.discovery import PublicContact
from job_automation.outreach.email_automation import EmailAutomationService, EmailLimits
from job_automation.outreach.personalized import PersonalizedOutreachService
from tests.job_automation.test_personalized_outreach import context


class FakeTransport:
    def __init__(self, error=None):
        self.calls, self.verifications, self.error = [], 0, error
    def verify(self):
        self.verifications += 1
        return 'gmail-account'
    def send(self, **kwargs):
        content = kwargs.pop('attachment_path').read_bytes()
        self.calls.append({**kwargs, 'attachment': content})
        if self.error:
            raise self.error
        return 'gmail-message-123'


@pytest.fixture
def ready(context):
    repo, job, path, _ = context
    repo.update_job(job.id, match_score=85)
    with Session(repo.engine) as session, session.begin():
        contact = session.scalar(select(JobContact).where(JobContact.job_id == job.id))
        contact.work_email = 'riley@example.com'
    transport = FakeTransport()
    now = datetime(2026, 9, 7, 9, tzinfo=timezone.utc)
    service = EmailAutomationService(repo, project_root=path.parent, transport=transport, clock=lambda: now)
    return repo, job, path, service, transport


def copy_job(repo, job, suffix):
    second = repo.create_job(title=job.title, company=job.company, source='test',
        application_url=f'https://example.com/jobs/{suffix}', match_score=85, status=JobStatus.QUALIFIED,
        description=job.description, description_complete=True)
    ContactRepository(repo.engine).save(second.id, PublicContact(name='Riley Recruiter', title='Talent Acquisition',
        company=job.company, work_email='riley@example.com', source='https://example.com/team', priority=2, evidence='Talent Acquisition'))
    return second


def test_send_active_cv_store_receipt_and_no_replay(ready):
    repo, job, path, service, transport = ready
    result = service.run()
    assert result.sent == 1
    assert len(transport.calls) == 1
    assert transport.calls[0]['recipient'] == 'riley@example.com'
    assert transport.calls[0]['attachment'] == path.read_bytes()
    assert '5+ years' in transport.calls[0]['body']
    history = service.deliveries(job.id)
    assert history[0]['status'] == 'SENT'
    assert history[0]['sent_at'] is not None
    assert history[0]['job_id'] == job.id
    assert history[0]['provider_message_id'] == 'gmail-message-123'
    assert history[0]['resume_sha256'] == sha256(path.read_bytes()).hexdigest()
    assert repo.get_job(job.id).outreach_status is OutreachStatus.SENT
    assert service.run().duplicates == 1
    assert len(transport.calls) == 1
    assert list(service.upload_dir.iterdir()) == []


@pytest.mark.parametrize('score', [70, 69, None])
def test_strict_ats_threshold(ready, score):
    repo, job, _, service, transport = ready
    repo.update_job(job.id, match_score=score)
    assert service.run().sent == 0
    assert transport.verifications == 0


@pytest.mark.parametrize('error,status', [(TimeoutError(), 'UNKNOWN'), (ProviderRejected('failure'), 'FAILED')])
def test_failure_never_replays(ready, error, status):
    _, job, _, service, transport = ready
    transport.error = error
    service.run()
    assert service.deliveries(job.id)[0]['status'] == status
    assert service.deliveries(job.id)[0]['sent_at'] is None
    assert service.run().duplicates == 1
    assert len(transport.calls) == 1


def test_chooses_one_high_priority_emailable_contact(ready):
    repo, job, _, service, transport = ready
    contacts = ContactRepository(repo.engine)
    contacts.save(job.id, PublicContact(name='Morgan Manager', title='Engineering Manager', company=job.company,
        work_email='morgan@example.com', source='https://example.com/team', priority=5, evidence='Frontend engineering manager'))
    contacts.save(job.id, PublicContact(name='Pat Poster', title='Technical Recruiter', company=job.company,
        source='https://example.com/job', priority=1, evidence='Job poster'))
    assert service.run().sent == 1
    assert transport.calls[0]['recipient'] == 'riley@example.com'
    assert 'Riley Recruiter' in transport.calls[0]['body']
    contacts.save(job.id, PublicContact(name='Sam Poster', title='Technical Recruiter', company=job.company,
        work_email='sam@example.com', source='https://example.com/job', priority=1, evidence='Job poster'))
    service.run()
    assert len(transport.calls) == 1


def test_global_cooldown_survives_worker_restart(ready):
    repo, job, path, service, transport = ready
    second = copy_job(repo, job, 'second')
    assert service.run().sent == 1
    new = EmailAutomationService(repo, project_root=path.parent, transport=transport, clock=service.clock)
    blocked = new.run(second.id)
    assert blocked.rate_limited
    assert blocked.sent == 0
    new.clock = lambda: service.clock() + timedelta(minutes=10)
    assert new.run(second.id).sent == 1


def test_rolling_day_limit(ready):
    repo, job, _, service, transport = ready
    for number in range(2, 12):
        copy_job(repo, job, str(number))
    start = service.clock()
    for number in range(10):
        service.clock = lambda n=number: start + timedelta(minutes=10 * n)
        assert service.run().sent == 1
    service.clock = lambda: start + timedelta(hours=2)
    assert service.run().rate_limited
    assert len(transport.calls) == 10


def test_stale_cv_never_sends(ready):
    _, _, path, service, transport = ready
    path.write_bytes(b'not the uploaded CV')
    result = service.run()
    assert result.sent == 0
    assert transport.verifications == 0


def test_claim_is_durable_without_provider_call(ready):
    repo, job, path, service, transport = ready
    PersonalizedOutreachService(repo, project_root=path.parent, prefer_email_contact=True).run()
    assert service._claim(job.id, 'gmail-account')[0] == 'claimed'
    # Simulate a process crash after the durable claim and restart.
    restarted = EmailAutomationService(repo, project_root=path.parent, transport=transport)
    assert restarted.run().duplicates == 1
    assert transport.calls == []
    assert restarted.deliveries(job.id)[0]['status'] == 'SENDING'


def test_limits_cannot_enable_a_burst():
    with pytest.raises(ValueError):
        EmailLimits(minimum_interval_seconds=0)
    with pytest.raises(ValueError):
        EmailLimits(rolling_day_limit=100)


def test_composio_explicit_account_and_attachment(tmp_path):
    calls = []
    class Tools:
        def execute(self, **kwargs):
            calls.append(kwargs)
            return {'successful': True, 'data': {'id': 'gmail-1'}}
    client = SimpleNamespace(tools=Tools(), connected_accounts=SimpleNamespace(list=lambda **_: SimpleNamespace(
        items=[{'id': 'gmail-account', 'status': 'ACTIVE', 'toolkit': {'slug': 'gmail'}}])))
    config = ComposioConfig(enabled=True, api_key='test-secret', user_id='user', gmail_connected_account_id='gmail-account')
    transport = ComposioGmailTransport(config=config, upload_dir=tmp_path, client_factory=lambda _: client)
    assert transport.verify() == 'gmail-account'
    path = tmp_path / 'cv.pdf'
    path.write_bytes(b'test CV')
    assert transport.send(recipient='riley@example.com', subject='Subject', body='Body', attachment_path=path) == 'gmail-1'
    assert calls[0]['connected_account_id'] == 'gmail-account'
    assert calls[0]['arguments']['attachment'] == str(path)
    assert calls[0]['arguments']['recipient_email'] == 'riley@example.com'
    assert set(calls[0]['arguments']).isdisjoint({'cc', 'bcc', 'extra_recipients'})
    assert calls[0]['version'] != 'latest'


@pytest.mark.parametrize('toolkit,status', [('browser', 'ACTIVE'), ('gmail', 'EXPIRED')])
def test_wrong_or_expired_account_rejected(tmp_path, toolkit, status):
    client = SimpleNamespace(connected_accounts=SimpleNamespace(list=lambda **_: SimpleNamespace(
        items=[{'id': 'account', 'status': status, 'toolkit': {'slug': toolkit}}])))
    config = ComposioConfig(enabled=True, api_key='secret', user_id='user', gmail_connected_account_id='account')
    transport = ComposioGmailTransport(config=config, upload_dir=tmp_path, client_factory=lambda _: client)
    with pytest.raises(ComposioConfigurationError):
        transport.verify()


def test_concurrent_claims_allow_only_one_attempt(ready):
    from concurrent.futures import ThreadPoolExecutor
    repo, job, path, service, _ = ready
    PersonalizedOutreachService(repo, project_root=path.parent, prefer_email_contact=True).run()
    with ThreadPoolExecutor(max_workers=2) as workers:
        states = list(workers.map(lambda _: service._claim(job.id, 'gmail-account')[0], range(2)))
    assert sorted(states) == ['claimed', 'duplicate']
    assert len(service.deliveries(job.id)) == 1


def test_api_uses_same_rate_limited_sender(ready):
    from fastapi.testclient import TestClient
    from job_automation.api.main import create_app
    repo, job, path, service, transport = ready
    app = create_app(str(repo.engine.url), project_root=path.parent)
    app.state.outreach_email_service = service
    with TestClient(app) as client:
        assert client.post(f'/jobs/{job.id}/outreach/send').json()['sent'] == 1
        assert client.post('/outreach/send').json()['duplicates'] == 1
        history = client.get('/outreach/email-status', params={'job_id': job.id}).json()
        assert history[0]['recipient'] == 'riley@example.com'
        assert history[0]['status'] == 'SENT'
        assert client.post('/jobs/99999/outreach/send').status_code == 404
    assert len(transport.calls) == 1


@pytest.mark.parametrize('response,expected', [
    ({'successful': True, 'data': {}}, DeliveryUncertain),
    ({'successful': False, 'error': 'Rejected'}, ProviderRejected),
])
def test_no_confirmed_message_id_never_becomes_sent(tmp_path, response, expected):
    client = SimpleNamespace(tools=SimpleNamespace(execute=lambda **_: response),
        connected_accounts=SimpleNamespace(list=lambda **_: SimpleNamespace(items=[{
            'id': 'gmail', 'status': 'ACTIVE', 'toolkit': {'slug': 'gmail'}}])))
    config = ComposioConfig(enabled=True, api_key='secret', user_id='user', gmail_connected_account_id='gmail')
    transport = ComposioGmailTransport(config=config, upload_dir=tmp_path, client_factory=lambda _: client)
    transport.verify()
    path = tmp_path / 'cv.pdf'
    path.write_bytes(b'CV')
    with pytest.raises(expected):
        transport.send(recipient='riley@example.com', subject='Subject', body='Body', attachment_path=path)
