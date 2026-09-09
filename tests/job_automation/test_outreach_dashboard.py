from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.api.main import create_app
from job_automation.database.models import JobContact, LinkedInOutreach
from job_automation.outreach.dashboard import outreach_dashboard
from job_automation.outreach.contact_repository import ContactRepository
from job_automation.outreach.discovery import PublicContact
from job_automation.outreach.personalized import PersonalizedOutreachService
from tests.job_automation.test_email_automation import ready, context


def test_contact_priority_does_not_invent_email(ready):
    repo, job, _, _, _ = ready
    ContactRepository(repo.engine).save(job.id, PublicContact(name='Pat Poster', title='Recruiter',
        company=job.company, source=job.application_url, priority=1, evidence='Job poster',
        linkedin_url='https://www.linkedin.com/in/pat'))
    data = outreach_dashboard(repo)
    row = data['jobs'][0]
    assert row['contact_name'] == 'Pat Poster'
    assert row['public_work_email'] is None  # Never borrow another person's email.
    assert row['email_status'] == 'NOT_FOUND' and row['linkedin_status'] == 'FOUND'
    assert data['contacts_found'] == 1


def test_found_drafted_sent_and_delivery_counters(ready):
    repo, job, path, service, _ = ready
    assert outreach_dashboard(repo)['jobs'][0]['email_status'] == 'FOUND'
    PersonalizedOutreachService(repo, project_root=path.parent).run(job.id)
    assert outreach_dashboard(repo)['jobs'][0]['email_status'] == 'DRAFTED'
    assert service.run(job.id).sent == 1
    with Session(repo.engine) as session, session.begin():
        session.add(LinkedInOutreach(job_id=job.id, job_key=str(job.id),
            linkedin_message_sent=True, status='SENT', sent_at=datetime.now(timezone.utc)))
    data = outreach_dashboard(repo)
    assert data['emails_sent'] == data['linkedin_messages_sent'] == 1
    assert data['jobs'][0]['email_status'] == data['jobs'][0]['linkedin_status'] == 'SENT'
    assert data['jobs'][0]['email_attempted']


def test_dashboard_send_requires_applied_and_prevents_repeat(ready):
    repo, job, path, service, transport = ready
    app = create_app(str(repo.engine.url), project_root=path.parent)
    app.state.outreach_email_service = service
    with TestClient(app) as client:
        assert client.post(f'/dashboard/jobs/{job.id}/send-email').status_code == 400
        assert transport.calls == []
        repo.mark_applied(job.id, resume_used='cv.docx', application_method='BROWSER',
            applied_at=datetime.now(timezone.utc), application_confirmation='Submission confirmed')
        assert client.post(f'/jobs/{job.id}/outreach/generate').status_code == 200
        assert client.post(f'/dashboard/jobs/{job.id}/send-email').json()['sent'] == 1
        assert client.post('/dashboard/outreach/send').json()['sent'] == 0
        assert client.get('/dashboard/outreach').json()['emails_sent'] == 1
        assert len(transport.calls) == 1
        assert client.post('/dashboard/jobs/99999/send-email').status_code == 404


def test_uncertain_delivery_is_not_reported_sent(ready):
    repo, job, _, service, transport = ready
    transport.error = TimeoutError()
    service.run(job.id)
    row = outreach_dashboard(repo)['jobs'][0]
    assert row['email_status'] == 'DRAFTED'
    assert row['email_attempted'] and row['email_detail']
    assert outreach_dashboard(repo)['emails_sent'] == 0
