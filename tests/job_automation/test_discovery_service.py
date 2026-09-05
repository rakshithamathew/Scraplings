from __future__ import annotations

from pathlib import Path

import pytest

from job_automation.database import JobRepository, initialize_database
from job_automation.normalizer import NormalizedJob
from job_automation.scraper.service import (
    JobDiscoveryService,
    SourceConfig,
    UnsupportedSourceError,
    build_scraper,
    load_sources,
)


@pytest.fixture
def repository(tmp_path: Path) -> JobRepository:
    engine = initialize_database(f"sqlite:///{(tmp_path / 'discovery.db').as_posix()}")
    try:
        yield JobRepository(engine)
    finally:
        engine.dispose()


def test_load_sources_validates_json_config(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    path.write_text(
        """{
          "sources": [
            {"type": " GreenHouse ", "company": "Example", "url": "https://example.test/jobs"},
            {"type": "lever", "company": "Other", "url": "https://jobs.lever.co/other", "enabled": false}
          ]
        }""",
        encoding="utf-8",
    )

    sources = load_sources(path)

    assert len(sources) == 2
    assert sources[0].type == "greenhouse"
    assert not sources[1].enabled


@pytest.mark.asyncio
async def test_service_continues_deduplicates_updates_and_saves(
    repository: JobRepository,
) -> None:
    existing = repository.create_job(
        external_id="old-id",
        title="Platform Engineer",
        company="Example",
        location="Old location",
        description="Old description",
        skills=[],
        source="greenhouse",
        source_url="https://boards.example.test/jobs/1-old",
        application_url="https://boards.example.test/jobs/1",
    )
    sources = [
        SourceConfig(type="greenhouse", company="Example", url="https://example.test/greenhouse"),
        SourceConfig(type="lever", company="Other", url="https://example.test/lever"),
        SourceConfig(type="broken", company="Broken", url="https://example.test/broken"),
        SourceConfig(
            type="lever",
            company="Disabled",
            url="https://example.test/disabled",
            enabled=False,
        ),
    ]

    class FakeScraper:
        def __init__(self, jobs: list[NormalizedJob] | None = None, *, fail: bool = False) -> None:
            self.jobs = jobs or []
            self.fail = fail

        async def search_jobs(self, **kwargs: object) -> list[NormalizedJob]:
            if self.fail:
                raise RuntimeError("source unavailable")
            return self.jobs

    def factory(source: SourceConfig) -> FakeScraper:
        if source.type == "greenhouse":
            return FakeScraper(
                [
                    NormalizedJob(
                        external_id="new-id",
                        title=" Platform   Engineer ",
                        company="example",
                        location="Remote",
                        description="Updated description",
                        skills=["Python"],
                        source="greenhouse",
                        source_url="https://boards.example.test/jobs/1-new",
                        application_url="https://boards.example.test/jobs/1/",
                    ),
                    NormalizedJob(title="Incomplete", source="greenhouse"),
                ]
            )
        if source.type == "lever":
            return FakeScraper(
                [
                    NormalizedJob(
                        external_id="lever-2",
                        title="Data Engineer",
                        company="Other",
                        location="London",
                        source="lever",
                        source_url="https://jobs.lever.co/other/2",
                        application_url="https://jobs.lever.co/other/2/apply",
                    )
                ]
            )
        return FakeScraper(fail=True)

    summary = await JobDiscoveryService(repository, scraper_factory=factory).run(sources)  # type: ignore[arg-type]

    assert summary.sources_checked == 3
    assert summary.jobs_discovered == 3
    assert summary.new_jobs == 1
    assert summary.duplicates == 1
    assert summary.failed_sources == 1

    updated = repository.get_job(existing.id)
    assert updated is not None
    assert updated.external_id == "new-id"
    assert updated.location == "Remote"
    assert updated.description == "Updated description"
    assert updated.skills == ["Python"]
    assert updated.source_url == "https://boards.example.test/jobs/1-new"
    assert len(repository.get_jobs()) == 2


def test_build_scraper_rejects_unknown_source() -> None:
    source = SourceConfig(type="linkedin", company="Example", url="https://example.test")

    with pytest.raises(UnsupportedSourceError, match="unsupported source type"):
        build_scraper(source)
