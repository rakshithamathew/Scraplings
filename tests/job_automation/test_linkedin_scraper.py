from urllib.parse import parse_qs, urlsplit

import pytest

from job_automation.database import JobRepository, JobStatus, initialize_database
from job_automation.matching.requirements import remote_allows_india
from job_automation.scraper.linkedin import DEFAULT_TITLES, LinkedInScraper
from job_automation.scraper.service import JobDiscoveryService, SourceConfig, build_scraper
from tests.job_automation.test_scraper import make_response

CARD = '''<div class="base-card"><a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/react-engineer-123?trackingId=a"></a>
<h3 class="base-search-card__title">React Engineer</h3><h4 class="base-search-card__subtitle"><a>Example</a></h4>
<span class="job-search-card__location">Bengaluru, India</span><time datetime="2026-09-01"></time></div>'''
DETAIL = '''<h1 class="top-card-layout__title">React Engineer</h1>
<div class="show-more-less-html__markup"><p>Build React interfaces.</p></div>
<li class="description__job-criteria-item"><h3>Workplace type</h3><span>Hybrid</span></li>
<a class="apply-button" href="https://careers.example.com/jobs/123">Apply</a>'''

@pytest.mark.asyncio
async def test_details_filter_and_repeat_run_persistence(tmp_path, monkeypatch):
    engine = initialize_database(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    repo = JobRepository(engine)
    calls = []
    def factory(source):
        scraper = LinkedInScraper(queries=DEFAULT_TITLES, request_delay=0)
        async def fetch(url):
            calls.append(url)
            return make_response(url, CARD if '/search/' in url else DETAIL)
        monkeypatch.setattr(scraper, '_fetch', fetch)
        return scraper
    service = JobDiscoveryService(repo, scraper_factory=factory)
    source = SourceConfig(type='linkedin', url='https://www.linkedin.com/jobs/search/')
    try:
        first = await service.run([source])
        second = await service.run([source])
        assert first.new_jobs == 1
        assert second.duplicates == 1
        jobs = repo.get_jobs()
        assert len(jobs) == 1
        job = jobs[0]
        assert job.status == JobStatus.DISCOVERED
        assert job.description == 'Build React interfaces.'
        assert job.workplace_type.value == 'HYBRID'
        assert job.source_url == 'https://www.linkedin.com/jobs/view/123'
        assert job.application_url == 'https://careers.example.com/jobs/123'
        assert job.posted_at.date().isoformat() == '2026-09-01'
        assert len([url for url in calls if '/view/' in url]) == 2
        assert {parse_qs(urlsplit(url).query)['keywords'][0] for url in calls if '/search/' in url} == set(DEFAULT_TITLES)
    finally:
        engine.dispose()

@pytest.mark.asyncio
@pytest.mark.parametrize('status,html', [(403, ''), (429, ''), (999, ''), (302, ''), (200, 'Security verification' + CARD)])
async def test_stops_all_requests_at_access_control(monkeypatch, status, html):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['retries'] == 1
        assert kwargs['follow_redirects'] is False
        assert kwargs['impersonate'] is None
        return make_response(url, html, status=status)
    monkeypatch.setattr('job_automation.scraper.linkedin.AsyncFetcher.get', fetch)
    with pytest.raises(RuntimeError, match='verification'):
        await LinkedInScraper(request_delay=0).search_jobs()
    assert len(calls) == 1

@pytest.mark.parametrize('location,description,expected', [
    ('Remote', '', False), ('India', '', True), ('Worldwide', '', True),
    ('Remote', 'Visa sponsorship provided', False),
    ('Worldwide', 'US only', False), ('Worldwide', 'Worldwide except India', False),
    ('Germany', '', False), ('Remote', 'Remote from India', True),
])
def test_india_requires_evidence(location, description, expected):
    assert remote_allows_india(location, description, require_evidence=True) is expected


def test_factory_uses_all_requested_titles():
    scraper = build_scraper(SourceConfig(type='linkedin', url='https://www.linkedin.com/jobs/search/'))
    assert scraper.queries == DEFAULT_TITLES
