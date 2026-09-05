from __future__ import annotations

import logging

import pytest

from job_automation.normalizer import NormalizedJob
from job_automation.scraper import WorkdayScraper, WorkdaySiteConfig


CAREERS_URL = "https://acme.wd5.myworkdayjobs.com/en-US/External"


def test_workday_common_public_url_builds_explicit_cxs_config() -> None:
    config = WorkdaySiteConfig.from_careers_url(CAREERS_URL, company="Acme")

    assert config.supported
    assert config.tenant == "acme"
    assert config.site_id == "External"
    assert config.locale == "en-US"
    assert config.search_url == "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/jobs"
    assert config.detail_url_template == (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External{external_path}"
    )


@pytest.mark.asyncio
async def test_workday_discovery_returns_normalized_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = WorkdayScraper(CAREERS_URL, company="Acme", request_delay=0)

    async def fake_request(
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
    ) -> dict:
        assert method == "POST"
        assert url.endswith("/wday/cxs/acme/External/jobs")
        assert payload is not None
        assert payload["searchText"] == "python"
        return {
            "total": 2,
            "jobPostings": [
                {
                    "title": " Senior   Python Engineer ",
                    "jobReqId": "REQ-101",
                    "locationsText": "Remote",
                    "postedOn": "2026-08-01",
                    "externalPath": "/job/Remote/Senior-Python-Engineer_REQ-101",
                },
                {
                    "title": "Data Engineer",
                    "jobReqId": "REQ-102",
                    "locationsText": ["London", "Remote"],
                    "externalPath": "/job/London/Data-Engineer_REQ-102",
                },
                "layout-change",
            ],
        }

    monkeypatch.setattr(scraper, "_request_json", fake_request)
    jobs = await scraper.search_jobs(query="python", limit=2)

    assert len(jobs) == 2
    assert all(isinstance(job, NormalizedJob) for job in jobs)
    assert jobs[0].external_id == "REQ-101"
    assert jobs[0].title == "Senior Python Engineer"
    assert jobs[0].company == "Acme"
    assert jobs[0].location == "Remote"
    assert jobs[0].application_url == (
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Remote/"
        "Senior-Python-Engineer_REQ-101"
    )
    assert jobs[0].posted_at is not None
    assert jobs[1].location == "London, Remote"


@pytest.mark.asyncio
async def test_workday_detail_parser_merges_listing_data(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = WorkdayScraper(CAREERS_URL, company="Acme", request_delay=0)
    listing_job = scraper.normalize(
        {
            "title": "Python Engineer",
            "jobReqId": "REQ-101",
            "locationsText": "Remote",
            "postedOn": "2026-08-01",
            "externalPath": "/job/Remote/Python-Engineer_REQ-101",
        }
    )

    async def fake_request(url: str, **kwargs: object) -> dict:
        assert url.endswith("/wday/cxs/acme/External/job/Remote/Python-Engineer_REQ-101")
        return {
            "jobPostingInfo": {
                "jobReqId": "REQ-101",
                "title": "Python Engineer",
                "location": "Remote",
                "jobDescription": "<p>Build reliable <strong>services</strong>.</p>",
            }
        }

    monkeypatch.setattr(scraper, "_request_json", fake_request)
    detailed = await scraper.get_job_details(listing_job)

    assert detailed is not None
    assert detailed.description == "Build reliable services."
    assert detailed.posted_at == listing_job.posted_at
    assert detailed.application_url == listing_job.application_url


@pytest.mark.asyncio
async def test_explicit_config_supports_alternate_payload_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WorkdaySiteConfig(
        careers_url="https://careers.example.test/jobs",
        company="Configured Co",
        search_url="https://careers.example.test/public/search",
        detail_url_template="https://careers.example.test/public/detail{external_path}",
        discovery_method="GET",
        job_list_paths=("results.items",),
        detail_root_paths=("result",),
        page_size=5,
        max_pages=1,
    )
    scraper = WorkdayScraper(config.careers_url, config=config, request_delay=0)

    async def fake_request(url: str, **kwargs: object) -> dict:
        assert url.startswith("https://careers.example.test/public/search?")
        return {
            "results": {
                "items": [
                    {
                        "jobTitle": "Configured Role",
                        "requisitionId": "ALT-1",
                        "location": "Berlin",
                        "jobPath": "/job/Berlin/Configured-Role_ALT-1",
                    }
                ]
            }
        }

    monkeypatch.setattr(scraper, "_request_json", fake_request)
    jobs = await scraper.search_jobs(limit=1)
    assert len(jobs) == 1
    assert jobs[0].title == "Configured Role"
    assert jobs[0].company == "Configured Co"


@pytest.mark.asyncio
async def test_unsupported_workday_variant_is_logged_and_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scraper = WorkdayScraper("https://careers.example.com/jobs", request_delay=0)

    with caplog.at_level(logging.WARNING):
        jobs = await scraper.search_jobs()

    assert jobs == []
    assert "Unsupported Workday variant" in caplog.text
    assert "Supply an explicit WorkdaySiteConfig" in caplog.text
