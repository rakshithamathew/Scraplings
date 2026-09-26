from urllib.parse import parse_qs, urlsplit

import pytest

from job_automation.database import JobRepository, JobStatus, initialize_database
from job_automation.matching import UserProfile
from job_automation.matching.clinical import clinical_queries
from job_automation.matching.ranker import rank_jobs
from job_automation.matching.requirements import evaluate_hard_constraints
from job_automation.normalizer import NormalizedJob, WorkplaceType
from job_automation.scraper.linkedin import LinkedInScraper
from job_automation.scraper.service import JobDiscoveryService, SourceConfig


def profile():
    return UserProfile(target_titles=["Frontend Engineer"], skills=["React.js", "TypeScript", "Angular"],
                       clinical_systems_only=True)


def job(**changes):
    return NormalizedJob(**{
        "title": "Frontend Engineer", "company": "Health Co", "source": "greenhouse",
        "application_url": "https://example.test/jobs/1", "location": "Worldwide",
        "workplace_type": WorkplaceType.REMOTE,
        "description": "Build electronic health records with ReactJS and TypeScript.",
        **changes,
    })


@pytest.mark.parametrize("changes,eligible", [
    ({}, True),
    ({"location": "Greater Bangalore", "workplace_type": WorkplaceType.HYBRID}, True),
    ({"location": "Bengaluru Urban", "workplace_type": WorkplaceType.HYBRID}, True),
    ({"location": "Hyderabad", "workplace_type": WorkplaceType.HYBRID}, False),
    ({"location": "Bangalore", "workplace_type": WorkplaceType.ONSITE}, False),
    ({"location": "US only"}, False),
    ({"description": "React and TypeScript e-commerce. Healthcare insurance benefits."}, False),
    ({"description": "React and TypeScript marketing site for a healthcare company."}, False),
    ({"description": "Build EHR software with Java and Spring."}, False),
    ({"description": "Build EHR software with React and React.js."}, False),
    ({"description": None, "title": "Clinical Frontend Engineer"}, False),
    ({"description": "Build clinical dashboards with Angular and TypeScript."}, True),
    ({"description": "Build patient records software using React and TypeScript. US only."}, False),
])
def test_clinical_scope(changes, eligible):
    candidate = profile()
    result = evaluate_hard_constraints(job(**changes), target_titles=candidate.target_titles,
        candidate_skills=candidate.skills, clinical_systems_only=True)
    assert result.eligible is eligible
    if not eligible:
        assert result.reason


def test_queries_use_only_cv_core_stack_and_hybrid_url():
    queries = clinical_queries(profile().skills)
    assert "React clinical software developer" in queries
    assert "Angular EHR developer" in queries
    assert not any("Python" in query or "Java " in query for query in queries)
    scraper = LinkedInScraper()
    params = parse_qs(urlsplit(scraper._search_url(queries[0], {"location": "Bengaluru", "hybrid": True}, 0)).query)
    assert params["f_WT"] == ["3"]


@pytest.mark.asyncio
async def test_discovery_filters_before_scoring_and_preserves_applied(tmp_path):
    engine = initialize_database(f"sqlite:///{(tmp_path / 'clinical.db').as_posix()}")
    try:
        repo = JobRepository(engine)
        applied = repo.create_job(title="Frontend Engineer", company="Previous", source="test",
            application_url="https://example.test/previous", status=JobStatus.APPLIED)
        seen = []

        class FakeScraper:
            async def search_jobs(self, **kwargs):
                return [job(), job(application_url="https://example.test/other",
                    description="Build React and TypeScript retail software.")]

        def factory(source):
            seen.append(source)
            return FakeScraper()

        source = SourceConfig(type="linkedin", url="https://www.linkedin.com/jobs/search/",
                              options={"queries": ["Generic engineer"]})
        summary = await JobDiscoveryService(repo, profile=profile(), scraper_factory=factory).run([source])
        assert summary.remote_eligible == 1 and summary.filtered == 1
        assert seen[0].options["queries"] == clinical_queries(profile().skills)
        assert seen[0].options["searches"][0]["hybrid"]
        assert source.options["queries"] == ["Generic engineer"]
        assert len(rank_jobs(repo, profile())) == 1
        assert repo.get_job(applied.id).status is JobStatus.APPLIED
        skipped = [row for row in repo.get_jobs() if row.status is JobStatus.SKIPPED]
        assert len(skipped) == 1 and "clinical" in skipped[0].skip_reason
    finally:
        engine.dispose()
