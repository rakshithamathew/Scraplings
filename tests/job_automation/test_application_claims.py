import asyncio

import pytest

from job_automation.applications.service import ApplicationService, ApplicationSettings
from job_automation.applications.candidate_profile import CandidateProfile
from job_automation.database import JobStatus
from tests.job_automation.test_application_service import ConfirmedAgent, _eligible_job, repository


@pytest.mark.asyncio
async def test_concurrent_workers_and_restart_cannot_repeat_dispatch(repository, tmp_path):
    (tmp_path / 'cv.pdf').write_bytes(b'%PDF-1.4')
    repository.set_active_resume(original_filename='cv.pdf', path='cv.pdf',
        content_type='application/pdf', sha256='a' * 64, parsed_profile={'skills': ['React']})
    job = _eligible_job(repository)
    opened, proceed = asyncio.Event(), asyncio.Event()
    submissions = []
    class SlowAgent(ConfirmedAgent):
        async def open_application(self, job):
            opened.set()
            await proceed.wait()
            return True
        async def submit(self):
            submissions.append(job.id)
            return await super().submit()
    def service():
        return ApplicationService(repository, project_root=tmp_path,
            settings=ApplicationSettings(dry_run=False, headless=True, delay_between_applications_seconds=0),
            candidate_profile=CandidateProfile(first_name='Alex', email='alex@example.test'),
            agent_factory=lambda *args, **kwargs: SlowAgent())
    first = asyncio.create_task(service().run_application_cycle(job_id=job.id))
    try:
        await asyncio.wait_for(opened.wait(), timeout=5)
        second = await service().run_application_cycle(job_id=job.id)
        assert second.skipped_duplicates == 1 and second.attempted == 0
    finally:
        proceed.set()
        result = await first
    assert result.applied == 1 and submissions == [job.id]
    # Simulate a missing local receipt: retained claim still prevents replay.
    repository.update_job(job.id, status=JobStatus.QUALIFIED, application_confirmation=None,
        external_application_id=None, applied_at=None, application_status=None)
    replay = await service().run_application_cycle(job_id=job.id)
    assert replay.skipped_duplicates == 1
    assert submissions == [job.id]
