from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database import JobStatus, OutreachStatus
from job_automation.database.models import LinkedInOutreach, LinkedInOutreachGate
from job_automation.outreach.discovery import PublicContact
from job_automation.outreach.linkedin_automation import LinkedInOutreachService
from job_automation.outreach.linkedin_browser import JobPageDetails, LinkedInCheckpoint, LinkedInUnavailable, job_url, profile_url
from tests.job_automation.test_personalized_outreach import context


class FakeManager:
    def __init__(self, connected=True):
        self.connected = connected
        self.expired = []
    def is_connected(self, platform):
        assert platform == 'linkedin'
        return self.connected
    def mark_disconnected(self, platform, reason):
        self.connected = False
        self.expired.append(reason)


class FakeBrowser:
    def __init__(self, details, *, error_at=None, already=False):
        self.details, self.error_at, self.already = details, error_at, already
        self.calls = []
    async def start(self):
        self.calls.append('start')
    async def open_job(self, url, company):
        self.calls.append('job')
        if self.error_at == 'job':
            raise LinkedInCheckpoint('OTP required')
        return self.details
    async def open_profile(self, contact):
        self.calls.append('profile')
        self.contact = contact
        if self.error_at == 'profile':
            raise LinkedInUnavailable('Message is unavailable')
    async def prepare_message(self, message):
        self.calls.append('prepare')
        self.message = message
        return self.already
    async def send_prepared(self, message):
        self.calls.append('send')
        if self.error_at == 'send':
            raise TimeoutError('Unknown outcome')
        return 'Exact outgoing message confirmed'
    async def close(self):
        self.calls.append('close')


@pytest.fixture
def ready(context):
    repo, job, path, _ = context
    job = repo.update_job(job.id, source='linkedin', source_url='https://www.linkedin.com/jobs/view/123', match_score=85)
    poster = PublicContact(name='Pat Poster', title='Technical Recruiter', company=job.company,
        linkedin_url='https://www.linkedin.com/in/pat', source=job.source_url, priority=1, evidence='Job poster')
    browser = FakeBrowser(JobPageDetails(job.description, poster))
    manager = FakeManager()
    now = datetime(2026, 9, 7, 9, tzinfo=timezone.utc)
    service = LinkedInOutreachService(repo, project_root=path.parent, session_manager=manager,
        browser_factory=lambda _: browser, clock=lambda: now)
    return repo, job, path, service, browser, manager


@pytest.mark.asyncio
async def test_job_poster_profile_send_record_and_dedup(ready):
    repo, job, _, service, browser, _ = ready
    result = await service.run()
    assert result.status == 'SENT'
    assert result.linkedin_message_sent
    assert browser.calls == ['start', 'job', 'profile', 'prepare', 'send', 'close']
    assert browser.contact.name == 'Pat Poster'
    assert 'Pat Poster' in browser.message
    row = service.deliveries(job.id)[0]
    assert row['linkedin_message_sent'] is True
    assert row['sent_at'] is not None
    assert row['contact']['linkedin_url'] == 'https://www.linkedin.com/in/pat'
    assert row['job_id'] == job.id
    assert (await service.run(job.id)).status == 'DUPLICATE'
    assert browser.calls.count('send') == 1
    assert repo.get_job(job.id).outreach_status is OutreachStatus.DRAFTED  # email status is independent


@pytest.mark.asyncio
@pytest.mark.parametrize('score', [70, 69, None])
async def test_strict_score_gate_before_opening_browser(ready, score):
    repo, job, _, service, browser, _ = ready
    repo.update_job(job.id, match_score=score)
    assert (await service.run()).status == 'NO_ELIGIBLE_JOB'
    assert browser.calls == []


@pytest.mark.asyncio
async def test_checkpoint_stops_and_invalidates_auth_without_send(ready):
    _, job, _, service, browser, manager = ready
    browser.error_at = 'job'
    result = await service.run()
    assert result.status == 'BLOCKED'
    assert manager.expired
    assert 'send' not in browser.calls
    assert service.deliveries(job.id)[0]['sent_at'] is None
    await service.run(job.id)
    assert browser.calls.count('start') == 1


@pytest.mark.asyncio
async def test_missing_saved_auth_does_not_start_login(ready):
    _, _, _, service, browser, manager = ready
    manager.connected = False
    assert (await service.run()).status == 'BLOCKED'
    assert browser.calls == []


@pytest.mark.asyncio
async def test_message_unavailable_does_not_send_connection_request(ready):
    _, job, _, service, browser, _ = ready
    browser.error_at = 'profile'
    assert (await service.run()).status == 'UNAVAILABLE'
    assert 'send' not in browser.calls
    assert service.deliveries(job.id)[0]['attempted_at'] is None


@pytest.mark.asyncio
async def test_unknown_send_never_replayed(ready):
    _, job, _, service, browser, _ = ready
    browser.error_at = 'send'
    assert (await service.run()).status == 'UNKNOWN'
    assert service.deliveries(job.id)[0]['linkedin_message_sent'] is False
    assert (await service.run(job.id)).status == 'DUPLICATE'
    assert browser.calls.count('send') == 1


@pytest.mark.asyncio
async def test_existing_conversation_message_is_not_sent_again(ready):
    _, job, _, service, browser, _ = ready
    browser.already = True
    assert (await service.run()).status == 'ALREADY_PRESENT'
    assert 'send' not in browser.calls
    row = service.deliveries(job.id)[0]
    assert row['linkedin_message_sent'] is True
    assert row['sent_at'] is None  # no invented original send time


@pytest.mark.asyncio
async def test_gate_is_shared_and_durable(ready):
    repo, job, _, service, browser, _ = ready
    token, _ = service._acquire()
    assert (await service.run()).status == 'RATE_LIMITED'
    assert browser.calls == []
    service._release(token)
    assert (await service.run()).status == 'SENT'
    with Session(repo.engine) as session:
        gate = session.get(LinkedInOutreachGate, 1)
        assert gate.lease_token is None
        assert gate.next_allowed_at is not None


@pytest.mark.asyncio
async def test_resume_change_before_claim_stops_send(ready):
    _, job, path, service, browser, _ = ready
    original = browser.prepare_message
    async def change_cv(message):
        await original(message)
        path.write_bytes(b'changed CV')
        return False
    browser.prepare_message = change_cv
    assert (await service.run()).status == 'UNAVAILABLE'
    assert 'send' not in browser.calls


def test_only_linkedin_urls_and_canonical_person_urls():
    assert job_url('https://www.linkedin.com/jobs/view/frontend-123/?trk=test') == 'https://www.linkedin.com/jobs/view/123'
    assert job_url('https://linkedin.com.evil.test/jobs/view/123') is None
    assert job_url('https://user:password@linkedin.com/jobs/view/123') is None
    assert profile_url('/in/pat/?trk=1') == 'https://www.linkedin.com/in/pat'

@pytest.mark.asyncio
async def test_real_browser_ui_scope_and_send_confirmation(tmp_path):
    from job_automation.auth import SessionManager
    from job_automation.outreach.linkedin_browser import LinkedInMessagingBrowser
    manager = SessionManager(tmp_path / 'auth', project_root=tmp_path)
    manager.profile_directory('linkedin').mkdir(parents=True)
    manager.save_session('linkedin', status='connected')
    browser = LinkedInMessagingBrowser(manager)
    await browser.start()
    job_html = '''<nav id="global-nav">Me</nav><main>
      <div class="job-details-jobs-unified-top-card__company-name">Health Company</div>
      <div id="job-details">Build React and TypeScript clinical dashboards.</div>
      <div class="hirer-card"><h3 class="hirer-card__hirer-name">Pat Poster</h3>
      <p class="hirer-card__hirer-job-title">Technical Recruiter</p><a href="/in/pat/">Profile</a></div></main>'''
    profile_html = '''<nav id="global-nav">Me</nav><main><section><h1>Pat Poster</h1><button id="open">Message</button></section></main>
      <section class="msg-overlay-conversation-bubble" style="display:none">
      <header class="msg-overlay-bubble-header"><a href="/in/pat/">Pat Poster</a></header>
      <div id="history"></div><div class="msg-form__contenteditable" contenteditable="true" role="textbox"></div>
      <button id="send">Send</button></section><script>
      window.sendCount = 0;
      document.querySelector('#open').onclick = () => document.querySelector('section.msg-overlay-conversation-bubble').style.display = 'block';
      document.querySelector('#send').onclick = () => {
        window.sendCount++;
        let item = document.createElement('div'); item.className = 'msg-s-event-listitem--self';
        let body = document.createElement('p'); body.className = 'msg-s-event-listitem__body';
        body.textContent = document.querySelector('[contenteditable]').innerText;
        item.appendChild(body); document.querySelector('#history').appendChild(item);
        document.querySelector('[contenteditable]').innerText = '';
      };</script>'''
    async def route(request):
        if '/jobs/view/123' in request.request.url:
            await request.fulfill(body=job_html, content_type='text/html')
        elif '/in/pat' in request.request.url:
            await request.fulfill(body=profile_html, content_type='text/html')
        else:
            await request.abort()
    await browser.context.route('**/*', route)
    try:
        details = await browser.open_job('https://www.linkedin.com/jobs/view/123', 'Health Company')
        assert details.poster.name == 'Pat Poster'
        assert details.description == 'Build React and TypeScript clinical dashboards.'
        await browser.open_profile(details.poster)
        message = 'Hello Pat, I am interested in this frontend position.'
        assert await browser.prepare_message(message) is False
        assert 'Exact outgoing' in await browser.send_prepared(message)
        assert await browser.page.evaluate('window.sendCount') == 1
        assert await browser.prepare_message(message) is True
        assert await browser.page.evaluate('window.sendCount') == 1
        # Even a changed header after preparation must stop, without another send.
        await browser.composer.locator('header a').evaluate("node => node.href = '/in/other'")
        with pytest.raises(LinkedInUnavailable, match='recipient'):
            await browser.prepare_message('Another message')
        await browser.page.locator('body').evaluate("node => node.innerHTML = '<input autocomplete=one-time-code>'")
        with pytest.raises(LinkedInCheckpoint, match='OTP'):
            await browser.guard()
    finally:
        await browser.close()


def test_api_linkedin_status_route(ready):
    from fastapi.testclient import TestClient
    from job_automation.api.main import create_app
    repo, job, path, service, browser, _ = ready
    app = create_app(str(repo.engine.url), project_root=path.parent)
    app.state.linkedin_outreach_service = service
    with TestClient(app) as client:
        assert client.post(f'/jobs/{job.id}/outreach/linkedin/send').json()['status'] == 'SENT'
        assert client.post(f'/jobs/{job.id}/outreach/linkedin/send').json()['status'] == 'DUPLICATE'
        saved = client.get('/outreach/linkedin/status', params={'job_id': job.id}).json()
        assert saved[0]['linkedin_message_sent'] is True
        assert client.post('/jobs/99999/outreach/linkedin/send').status_code == 404
