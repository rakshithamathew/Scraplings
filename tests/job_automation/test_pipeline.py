from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from job_automation.database import JobStatus
from job_automation.pipeline import AutomationPipeline
from job_automation.outreach.email_automation import SendSummary
from job_automation.outreach.linkedin_automation import LinkedInSummary
from job_automation.scraper.service import DiscoverySummary
from tests.job_automation.test_personalized_outreach import context


@pytest.fixture
def pipeline_context(context):
    repo, first, path, _ = context
    second = repo.create_job(title=first.title, company=first.company, source='test',
        application_url='https://example.com/jobs/2', description=first.description,
        description_complete=True, status=JobStatus.QUALIFIED)
    calls, failures = [], set()
    scores = {first.id: 90, second.id: 85}
    sent_email, sent_linkedin = set(), set()
    class Applications:
        settings = SimpleNamespace(dry_run=False, max_applications_per_run=3, delay_between_applications_seconds=0)
        async def run_application_cycle(self, job_id):
            calls.append((job_id, 'apply'))
            if (job_id, 'apply') in failures:
                raise RuntimeError('Application failed')
            if not self.settings.dry_run:
                repo.mark_applied(job_id, resume_used='cv.docx', application_method='BROWSER',
                    applied_at=datetime.now(timezone.utc), application_confirmation='Confirmed')
            return {'applied': int(not self.settings.dry_run)}
    class Contacts:
        async def run(self, job_id):
            calls.append((job_id, 'contacts'))
            return None
    class Drafts:
        def run(self, job_id, **kwargs):
            calls.append((job_id, 'drafts'))
            return None
    class Email:
        def run(self, job_id):
            calls.append((job_id, 'email'))
            if (job_id, 'email') in failures:
                raise RuntimeError('Email unavailable')
            count = int(job_id not in sent_email)
            sent_email.add(job_id)
            return SendSummary(sent=count)
    class LinkedIn:
        async def run(self, job_id):
            calls.append((job_id, 'linkedin'))
            duplicate = job_id in sent_linkedin
            sent_linkedin.add(job_id)
            return LinkedInSummary(job_id, 'DUPLICATE' if duplicate else 'SENT', True)
    class Discovery:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self, sources):
            calls.append((None, 'scrape'))
            return DiscoverySummary(jobs_discovered=2)
    def scorer(repo, profile, resume_path, *, job_ids, **kwargs):
        job_id = job_ids[0]
        calls.append((job_id, 'score'))
        if (job_id, 'score') in failures:
            raise ValueError('Malformed job')
        job = repo.get_job(job_id)
        updated = repo.update_job(job_id, match_score=scores[job_id],
            status=job.status if job.status is JobStatus.APPLIED else
                JobStatus.QUALIFIED if scores[job_id] > 70 else JobStatus.DISCOVERED)
        return [SimpleNamespace(job=updated)]
    pipeline = AutomationPipeline(repo, project_root=path.parent, applications=Applications(),
        contacts=Contacts(), drafts=Drafts(), email=Email(), linkedin=LinkedIn(),
        discovery_factory=Discovery, scorer=scorer)
    return pipeline, repo, first, second, calls, failures, scores


@pytest.mark.asyncio
async def test_pipeline_order_persistence_and_restart(pipeline_context):
    pipeline, _, first, second, calls, _, _ = pipeline_context
    summary = await pipeline.run([])
    assert (summary.jobs_discovered, summary.jobs_ats_over_70, summary.jobs_applied,
        summary.emails_sent, summary.linkedin_messages_sent, summary.jobs_skipped) == (2, 2, 2, 2, 2, 0)
    assert [stage for job_id, stage in calls if job_id == first.id] == ['score', 'contacts', 'drafts', 'apply', 'email', 'linkedin']
    assert pipeline.history()[0]['status'] == 'COMPLETED'
    calls.clear()
    again = await pipeline.run([])
    assert again.jobs_applied == again.emails_sent == again.linkedin_messages_sent == 0
    assert not any(stage == 'apply' for _, stage in calls)


@pytest.mark.asyncio
async def test_application_failure_isolated_and_no_outreach(pipeline_context):
    pipeline, _, first, second, calls, failures, _ = pipeline_context
    failures.add((first.id, 'apply'))
    result = await pipeline.run([])
    assert result.jobs_applied == result.emails_sent == result.linkedin_messages_sent == 1
    assert (first.id, 'email') not in calls and (first.id, 'linkedin') not in calls
    assert (second.id, 'apply') in calls
    assert result.errors == 1 and result.jobs_skipped == 1
    assert pipeline.history()[0]['status'] == 'COMPLETED_WITH_ERRORS'


@pytest.mark.asyncio
async def test_email_failure_does_not_block_linkedin_or_other_jobs(pipeline_context):
    pipeline, _, first, _, calls, failures, _ = pipeline_context
    failures.add((first.id, 'email'))
    result = await pipeline.run([])
    assert result.jobs_applied == 2 and result.emails_sent == 1 and result.linkedin_messages_sent == 2
    assert (first.id, 'linkedin') in calls


@pytest.mark.asyncio
async def test_score_failure_and_strict_threshold(pipeline_context):
    pipeline, _, first, second, calls, failures, scores = pipeline_context
    failures.add((first.id, 'score'))
    scores[second.id] = 70
    result = await pipeline.run([])
    assert result.jobs_ats_over_70 == result.jobs_applied == 0
    assert result.jobs_skipped == 2
    assert not any(stage == 'apply' for _, stage in calls)


@pytest.mark.asyncio
async def test_dry_run_disables_outreach_for_previously_applied_jobs(pipeline_context):
    pipeline, _, _, _, calls, _, _ = pipeline_context
    await pipeline.run([])
    calls.clear()
    pipeline.applications.settings.dry_run = True
    result = await pipeline.run([])
    assert result.dry_run and result.emails_sent == result.linkedin_messages_sent == 0
    assert not any(stage in {'email', 'linkedin', 'apply'} for _, stage in calls)


@pytest.mark.asyncio
async def test_application_limit_defers_remaining_jobs(pipeline_context):
    pipeline, _, _, second, calls, _, _ = pipeline_context
    pipeline.applications.settings.max_applications_per_run = 1
    result = await pipeline.run([])
    assert result.jobs_applied == 1 and result.jobs_skipped == 1
    assert (second.id, 'apply') not in calls and (second.id, 'email') not in calls


@pytest.mark.asyncio
async def test_uncertain_claim_does_not_starve_remaining_jobs(pipeline_context):
    from sqlalchemy.orm import Session
    from job_automation.database.models import ApplicationDispatchClaim
    from job_automation.applications.service import application_dispatch_identity
    pipeline, repo, first, second, calls, _, _ = pipeline_context
    with Session(repo.engine) as session, session.begin():
        session.add(ApplicationDispatchClaim(job_id=first.id, identity=application_dispatch_identity(first)))
    pipeline.applications.settings.max_applications_per_run = 1
    result = await pipeline.run([])
    assert result.jobs_applied == 1
    assert (first.id, 'apply') not in calls and (second.id, 'apply') in calls


def test_pipeline_api_uses_existing_services(pipeline_context):
    from fastapi.testclient import TestClient
    from job_automation.api.main import create_app
    pipeline, repo, _, _, _, _, _ = pipeline_context
    app = create_app(str(repo.engine.url), project_root=pipeline.project_root)
    app.state.automation_pipeline = pipeline
    app.state.sources_loader = lambda: []
    with TestClient(app) as client:
        response = client.post('/automation/run')
        assert response.status_code == 200
        assert response.json()['jobs_applied'] == 2
        history = client.get('/automation/status').json()
        assert history[0]['summary']['run_id'] == response.json()['run_id']
