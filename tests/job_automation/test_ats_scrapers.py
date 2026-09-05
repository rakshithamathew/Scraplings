from __future__ import annotations

import json

import pytest

from job_automation.normalizer import NormalizedJob
from job_automation.scraper import GreenhouseScraper, LeverScraper
from scrapling.engines.toolbelt.custom import Response


def json_response(url: str, payload: object) -> Response:
    return Response(
        url=url,
        content=json.dumps(payload),
        status=200,
        reason="OK",
        cookies={},
        headers={"content-type": "application/json"},
        request_headers={},
    )


@pytest.mark.asyncio
async def test_greenhouse_search_uses_public_api_and_isolates_bad_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = GreenhouseScraper(
        "https://job-boards.greenhouse.io/example",
        request_delay=0,
    )

    async def fake_fetch(url: str) -> Response:
        if url.endswith("/example"):
            return json_response(url, {"name": "Example Corp"})
        return json_response(
            url,
            {
                "jobs": [
                    {
                        "id": 101,
                        "title": " Platform   Engineer ",
                        "location": {"name": "Remote"},
                        "content": "<p>Build <strong>Python</strong> services.</p>",
                        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/101",
                        "first_published": "2026-01-02T03:04:05Z",
                    },
                    42,
                    {
                        "id": 102,
                        "title": "Accountant",
                        "location": {"name": "London"},
                    },
                ]
            },
        )

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    jobs = await scraper.search_jobs(query="python")

    assert len(jobs) == 1
    assert isinstance(jobs[0], NormalizedJob)
    assert jobs[0].external_id == "101"
    assert jobs[0].company == "Example Corp"
    assert jobs[0].title == "Platform Engineer"
    assert jobs[0].description == "Build Python services."
    assert jobs[0].application_url == "https://job-boards.greenhouse.io/example/jobs/101"
    assert jobs[0].posted_at is not None


@pytest.mark.asyncio
async def test_greenhouse_detail_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = GreenhouseScraper(
        "https://boards.greenhouse.io/example",
        company="Example Corp",
        request_delay=0,
    )

    async def fake_fetch(url: str) -> Response:
        assert url.endswith("/example/jobs/101")
        return json_response(
            url,
            {
                "id": 101,
                "title": "Platform Engineer",
                "company_name": "Example Corp",
                "location": {"name": "Remote"},
                "content": "<p>Complete description</p>",
                "absolute_url": "https://boards.greenhouse.io/example/jobs/101",
                "first_published": "2026-01-02T03:04:05Z",
            },
        )

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    job = await scraper.get_job_details("https://boards.greenhouse.io/example/jobs/101")
    assert job is not None
    assert job.description == "Complete description"


def test_greenhouse_legacy_embed_url_extracts_for_token() -> None:
    scraper = GreenhouseScraper(
        "https://boards.greenhouse.io/embed/job_board?for=example-company",
    )
    assert scraper.board_token == "example-company"


@pytest.mark.asyncio
async def test_lever_search_uses_public_api_and_normalizes_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scraper = LeverScraper(
        "https://jobs.lever.co/example",
        company="Example Labs",
        request_delay=0,
    )

    async def fake_fetch(url: str) -> Response:
        assert url == "https://api.lever.co/v0/postings/example?mode=json"
        return json_response(
            url,
            [
                {
                    "id": "lever-1",
                    "text": " Data   Engineer ",
                    "categories": {"location": "Remote, US"},
                    "descriptionPlain": "Build data systems.",
                    "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
                    "hostedUrl": "https://jobs.lever.co/example/lever-1",
                    "applyUrl": "https://jobs.lever.co/example/lever-1/apply",
                    "createdAt": 1_700_000_000_000,
                },
                "bad record",
            ],
        )

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    jobs = await scraper.search_jobs(location="remote")

    assert len(jobs) == 1
    assert isinstance(jobs[0], NormalizedJob)
    assert jobs[0].external_id == "lever-1"
    assert jobs[0].company == "Example Labs"
    assert jobs[0].title == "Data Engineer"
    assert jobs[0].description == "Build data systems. Requirements Python"
    assert jobs[0].source_url == "https://jobs.lever.co/example/lever-1"
    assert jobs[0].application_url == "https://jobs.lever.co/example/lever-1/apply"
    assert jobs[0].posted_at is not None


@pytest.mark.asyncio
async def test_lever_detail_and_eu_url_support(monkeypatch: pytest.MonkeyPatch) -> None:
    scraper = LeverScraper(
        "https://jobs.eu.lever.co/example",
        company="Example EU",
        request_delay=0,
    )

    async def fake_fetch(url: str) -> Response:
        assert url == "https://api.eu.lever.co/v0/postings/example/lever-1?mode=json"
        return json_response(
            url,
            {
                "id": "lever-1",
                "text": "Engineer",
                "categories": {"location": "Paris"},
                "descriptionPlain": "Details",
                "hostedUrl": "https://jobs.eu.lever.co/example/lever-1",
                "applyUrl": "https://jobs.eu.lever.co/example/lever-1/apply",
            },
        )

    monkeypatch.setattr(scraper, "_fetch", fake_fetch)
    job = await scraper.get_job_details("https://jobs.eu.lever.co/example/lever-1/apply")
    assert job is not None
    assert job.company == "Example EU"
    assert job.location == "Paris"
