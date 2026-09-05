from __future__ import annotations

from inspect import isabstract

import pytest

from job_automation.normalizer import NormalizedJob
from job_automation.scraper import BaseJobScraper, GenericCareerScraper
from scrapling.engines.toolbelt.custom import Response


LISTING_URL = "https://realpython.github.io/fake-jobs/"
LISTING_HTML = """
<html><body>
  <div class="card-content">
    <h2 class="title"> Senior   Python Developer </h2>
    <h3 class="company"> Payne, Roberts and Davis </h3>
    <p class="location"> Stewartbury, AA </p>
    <footer class="card-footer">
      <a class="card-footer-item" href="jobs/senior-python-developer-0.html">Learn</a>
      <a class="card-footer-item" href="jobs/senior-python-developer-0.html#apply">Apply</a>
    </footer>
  </div>
  <div class="card-content">
    <h2 class="title">Energy Engineer</h2>
    <h3 class="company">Vasquez-Davidson</h3>
    <p class="location">Christopherville, AP</p>
    <footer class="card-footer">
      <a class="card-footer-item" href="jobs/energy-engineer-1.html">Learn</a>
      <a class="card-footer-item" href="jobs/energy-engineer-1.html">Apply</a>
    </footer>
  </div>
</body></html>
"""
DETAIL_HTML = """
<html><body><div class="content">
  <p>Build reliable Python services.</p>
  <p>Work with a collaborative team.</p>
</div></body></html>
"""


def make_response(url: str, html: str, *, status: int = 200) -> Response:
    return Response(
        url=url,
        content=html,
        status=status,
        reason="OK",
        cookies={},
        headers={},
        request_headers={},
    )


def test_base_scraper_defines_an_abstract_contract() -> None:
    assert isabstract(BaseJobScraper)
    assert BaseJobScraper.__abstractmethods__ == {"search_jobs", "get_job_details", "normalize"}


@pytest.mark.asyncio
async def test_generic_scraper_returns_normalized_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = GenericCareerScraper(LISTING_URL, timeout=5)

    async def fake_fetch(url: str) -> Response:
        assert url == LISTING_URL
        return make_response(url, LISTING_HTML)

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    jobs = await scraper.search_jobs(query="python", location="Stewartbury")

    assert len(jobs) == 1
    assert all(isinstance(job, NormalizedJob) for job in jobs)
    assert jobs[0].title == "Senior Python Developer"
    assert jobs[0].company == "Payne, Roberts and Davis"
    assert jobs[0].application_url == (
        "https://realpython.github.io/fake-jobs/jobs/senior-python-developer-0.html"
    )
    assert jobs[0].external_id is None
    assert jobs[0].posted_at is None


@pytest.mark.asyncio
async def test_generic_scraper_gets_public_job_details(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = GenericCareerScraper(LISTING_URL, timeout=5)
    job = scraper.normalize(
        {
            "title": "Senior Python Developer",
            "application_url": "jobs/senior-python-developer-0.html",
        }
    )

    async def fake_fetch(url: str) -> Response:
        return make_response(url, DETAIL_HTML)

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    detailed = await scraper.get_job_details(job)

    assert isinstance(detailed, NormalizedJob)
    assert detailed.description == "Build reliable Python services. Work with a collaborative team."
    assert detailed.title == "Senior Python Developer"


@pytest.mark.asyncio
async def test_generic_scraper_handles_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = GenericCareerScraper(LISTING_URL)

    async def failed_fetch(url: str) -> None:
        return None

    monkeypatch.setattr(scraper, "_fetch", failed_fetch)
    assert await scraper.search_jobs() == []
