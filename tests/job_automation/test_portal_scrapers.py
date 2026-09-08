from __future__ import annotations

import pytest

from job_automation.normalizer import WorkplaceType
from job_automation.scraper.linkedin import LinkedInScraper
from job_automation.scraper.naukri import NaukriScraper
from scrapling.engines.toolbelt.custom import Response


def make_response(url: str, html: str) -> Response:
    return Response(
        url=url,
        content=html,
        status=200,
        reason="OK",
        cookies={},
        headers={},
        request_headers={},
    )


@pytest.mark.asyncio
async def test_linkedin_parses_and_deduplicates_public_guest_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <div class="base-card">
      <div class="base-search-card" data-entity-urn="urn:li:jobPosting:1234567890">
        <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/frontend-1234567890?trk=test"></a>
        <h3 class="base-search-card__title"> React JS   Developer </h3>
        <h4 class="base-search-card__subtitle"><a>Example Co</a></h4>
        <span class="job-search-card__location">Bengaluru, Karnataka, India</span>
        <time datetime="2026-09-05"></time>
      </div>
    </div>
    """
    scraper = LinkedInScraper(
        queries=("React JS Developer",), searches=({"location": "Bengaluru"},), request_delay=0
    )

    async def fake_fetch(url: str) -> Response:
        if "/jobs/view/" in url:
            return make_response(url, '<div class="show-more-less-html__markup">Build React applications.</div>')
        return make_response(url, html)

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    jobs = await scraper.search_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "1234567890"
    assert jobs[0].title == "React JS Developer"
    assert jobs[0].company == "Example Co"
    assert jobs[0].application_url == "https://www.linkedin.com/jobs/view/1234567890"
    assert jobs[0].workplace_type is WorkplaceType.ONSITE


@pytest.mark.asyncio
async def test_naukri_parses_rendered_public_cards(monkeypatch: pytest.MonkeyPatch) -> None:
    html = """
    <div class="srp-jobtuple-wrapper">
      <a class="title" href="https://www.naukri.com/job/react-developer-123456789012">React Developer</a>
      <a class="comp-name">Example India</a>
      <span class="locWdth">Remote</span>
      <span class="expwdth">2-4 Yrs</span>
      <span class="job-desc">Build accessible React applications.</span>
      <ul class="tags-gt"><li>React</li><li>TypeScript</li></ul>
    </div>
    """
    scraper = NaukriScraper(
        queries=("React JS Developer",),
        searches=({"location": "Remote", "remote": True},),
        request_delay=0,
    )

    async def fake_render(url: str) -> Response:
        return make_response(url, html)

    monkeypatch.setattr(scraper, "_render", fake_render)
    jobs = await scraper.search_jobs()

    assert len(jobs) == 1
    assert jobs[0].external_id == "123456789012"
    assert jobs[0].company == "Example India"
    assert jobs[0].workplace_type is WorkplaceType.REMOTE
    assert jobs[0].skills == ["React", "TypeScript"]
    assert "Experience: 2-4 Yrs" in (jobs[0].description or "")


@pytest.mark.asyncio
async def test_portal_scrapers_fail_closed_when_no_public_results(monkeypatch: pytest.MonkeyPatch) -> None:
    linkedin = LinkedInScraper(queries=("Frontend Developer",), request_delay=0)
    naukri = NaukriScraper(queries=("Frontend Developer",), request_delay=0)

    async def no_response(url: str) -> None:
        return None

    monkeypatch.setattr(linkedin, "_fetch", no_response)
    monkeypatch.setattr(naukri, "_render", no_response)
    assert await linkedin.search_jobs() == []
    assert await naukri.search_jobs() == []
