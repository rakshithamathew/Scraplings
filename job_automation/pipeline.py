"""One persisted, failure-isolated discovery/application/outreach cycle."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
from pathlib import Path
from threading import Lock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database import DEFAULT_DATABASE_URL, JobRepository, JobStatus, initialize_database
from job_automation.database.models import PipelineRun
from job_automation.applications.service import ApplicationService, load_application_settings, application_dispatch_claimed
from job_automation.matching.ranker import rank_jobs
from job_automation.outreach.discovery import ContactDiscoveryService
from job_automation.outreach.personalized import PersonalizedOutreachService
from job_automation.outreach.email_automation import EmailAutomationService
from job_automation.outreach.linkedin_automation import LinkedInOutreachService
from job_automation.resume import parse_resume
from job_automation.scraper.service import JobDiscoveryService, load_sources, DEFAULT_CONFIG_PATH

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def serializable(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return value


@dataclass
class PipelineSummary:
    run_id: str = field(default_factory=lambda: uuid4().hex)
    jobs_discovered: int = 0
    jobs_ats_over_70: int = 0
    jobs_applied: int = 0
    emails_sent: int = 0
    linkedin_messages_sent: int = 0
    jobs_skipped: int = 0
    dry_run: bool = False
    errors: int = 0


class AutomationPipeline:
    def __init__(self, repository, *, project_root=PROJECT_ROOT, applications=None,
                 contacts=None, drafts=None, email=None, linkedin=None,
                 discovery_factory=JobDiscoveryService, scorer=rank_jobs):
        self.repository = repository
        self.project_root = Path(project_root).resolve()
        self.applications = applications or ApplicationService(repository, project_root=self.project_root)
        self.contacts = contacts or ContactDiscoveryService(repository)
        self.drafts = drafts or PersonalizedOutreachService(repository, project_root=self.project_root, prefer_email_contact=True)
        self.email = email or EmailAutomationService(repository, project_root=self.project_root)
        self.linkedin = linkedin or LinkedInOutreachService(repository, project_root=self.project_root,
            session_manager=self.applications.session_manager)
        self.discovery_factory, self.scorer = discovery_factory, scorer
        self._lock = Lock()

    def _cv(self, expected=None):
        active = self.repository.get_active_resume()
        if not active or not active.active:
            raise ValueError('Upload an active CV before running the pipeline')
        path = self.project_root / active.path
        if sha256(path.read_bytes()).hexdigest() != active.sha256 or (expected and active.sha256 != expected):
            raise ValueError('Active CV changed; start a new cycle to rescore jobs')
        return active, path

    def _save(self, summary, results, status='RUNNING'):
        with Session(self.repository.engine) as session, session.begin():
            row = session.get(PipelineRun, summary.run_id) or PipelineRun(id=summary.run_id)
            row.summary, row.results, row.status = asdict(summary), list(results), status
            if status != 'RUNNING':
                row.completed_at = datetime.now(timezone.utc)
            session.add(row)

    def history(self):
        with Session(self.repository.engine) as session:
            return [{'run_id': row.id, 'status': row.status, 'summary': row.summary, 'results': row.results}
                for row in session.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(20))]

    async def run(self, sources):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('An automation pipeline is already running')
        try:
            summary = PipelineSummary(dry_run=self.applications.settings.dry_run)
            results = []
            self._save(summary, results)
            async def stage(job_id, name, action, *, asynchronous=False):
                try:
                    result = await action() if asynchronous else action()
                    results.append({'job_id': job_id, 'stage': name, 'result': serializable(result)})
                    return result
                except Exception as error:
                    summary.errors += 1
                    results.append({'job_id': job_id, 'stage': name, 'error': type(error).__name__ + ': ' + str(error)[:500]})
                    LOGGER.exception('pipeline_stage_failed job_id=%s stage=%s', job_id, name)
                    return None
                finally:
                    self._save(summary, results)
            try:
                active, path = self._cv()
                profile = parse_resume(path).to_user_profile()
                discovery = await stage(None, 'discovery',
                    lambda: self.discovery_factory(self.repository, profile=profile).run(sources), asynchronous=True)
                if discovery:
                    summary.jobs_discovered = discovery.jobs_discovered
                # Score separately so malformed data for one job cannot abort others.
                candidates = []
                for job in self.repository.get_jobs():
                    if job.status is not JobStatus.SKIPPED:
                        scored = await stage(job.id, 'score', lambda: [item.job.id for item in
                            self.scorer(self.repository, profile, active.path, job_ids=[job.id], rescore_applied=True)])
                        if not scored:
                            summary.jobs_skipped += 1
                            continue
                    fresh = self.repository.get_job(job.id)
                    if fresh.status in {JobStatus.QUALIFIED, JobStatus.APPLIED} and fresh.match_score is not None and fresh.match_score > 70 and fresh.is_open is not False:
                        candidates.append(fresh)
                    else:
                        summary.jobs_skipped += 1
                summary.jobs_ats_over_70 = len(candidates)
                attempts = 0
                for job in sorted(candidates, key=lambda item: (-item.match_score, item.id)):
                    before = (summary.jobs_applied, summary.emails_sent, summary.linkedin_messages_sent)
                    try:
                        self._cv(active.sha256)
                        await stage(job.id, 'contacts', lambda: self.contacts.run(job.id), asynchronous=True)
                        await stage(job.id, 'personalized_email_and_linkedin', lambda: self.drafts.run(job.id, allow_applied=True))
                        # Missing outreach inputs do not block an otherwise valid application.
                        was_applied = self.repository.already_applied(job.id)
                        if not was_applied:
                            if application_dispatch_claimed(self.repository, job):
                                results.append({'job_id': job.id, 'stage': 'application', 'result': 'EXISTING_DISPATCH_CLAIM'})
                                continue
                            if attempts >= self.applications.settings.max_applications_per_run:
                                results.append({'job_id': job.id, 'stage': 'application', 'result': 'DEFERRED_APPLICATION_LIMIT'})
                                continue
                            if attempts:
                                await asyncio.sleep(self.applications.settings.delay_between_applications_seconds)
                            self._cv(active.sha256)
                            attempts += 1
                            applied = await stage(job.id, 'application', lambda: self.applications.run_application_cycle(job_id=job.id), asynchronous=True)
                        fresh = self.repository.get_job(job.id)
                        # An attempted click or an adapter's return value is insufficient.
                        if fresh.status is not JobStatus.APPLIED or not (fresh.application_confirmation or fresh.external_application_id) or not self.repository.already_applied(job.id):
                            results.append({'job_id': job.id, 'stage': 'outreach', 'result': 'WAITING_CONFIRMED_APPLICATION'})
                            continue
                        if not was_applied:
                            summary.jobs_applied += 1
                        if summary.dry_run:
                            results.append({'job_id': job.id, 'stage': 'outreach', 'result': 'DRY_RUN'})
                            continue
                        self._cv(active.sha256)
                        mail = await stage(job.id, 'email', lambda: self.email.run(job.id))
                        if mail:
                            summary.emails_sent += mail.sent
                        # Email failure or missing work email must not suppress permitted LinkedIn outreach.
                        self._cv(active.sha256)
                        message = await stage(job.id, 'linkedin', lambda: self.linkedin.run(job.id), asynchronous=True)
                        if message and message.status == 'SENT':
                            summary.linkedin_messages_sent += 1
                    except Exception as error:
                        summary.errors += 1
                        results.append({'job_id': job.id, 'stage': 'job', 'error': str(error)[:500]})
                        LOGGER.exception('pipeline_job_failed job_id=%s', job.id)
                    finally:
                        if before == (summary.jobs_applied, summary.emails_sent, summary.linkedin_messages_sent):
                            summary.jobs_skipped += 1
                        self._save(summary, results)
                return summary
            except Exception as error:
                summary.errors += 1
                results.append({'job_id': None, 'stage': 'pipeline', 'error': str(error)[:500]})
                return summary
            finally:
                self._save(summary, results, 'COMPLETED_WITH_ERRORS' if summary.errors else 'COMPLETED')
                LOGGER.info('pipeline_summary %s', json.dumps(asdict(summary)))
        finally:
            self._lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=DEFAULT_DATABASE_URL)
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--dry-run', action='store_true', help='Use Auto Apply dry-run and disable both outreach sends')
    parser.add_argument('--status', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    engine = initialize_database(args.database_url)
    try:
        repository = JobRepository(engine)
        settings = load_application_settings()
        if args.dry_run:
            settings = settings.model_copy(update={'dry_run': True})
        pipeline = AutomationPipeline(repository, applications=ApplicationService(repository, settings=settings))
        result = pipeline.history() if args.status else asdict(asyncio.run(pipeline.run(load_sources(args.config))))
        print(json.dumps(result, indent=2))
        return int(not args.status and bool(result['errors']))
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
