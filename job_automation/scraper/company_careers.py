"""Generic adapter for publicly accessible company careers pages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
from typing import Any
from urllib.parse import urljoin

from scrapling.parser import Selector

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper


@dataclass(frozen=True, slots=True)
class CareerPageSelectors:
    """CSS selectors used to adapt the generic scraper to a careers page."""

    job_card: str = ".card-content"
    external_id: str | None = None
    title: str = "h2.title::text"
    company: str = "h3.company::text"
    location: str = "p.location::text"
    description: str | None = "p.description::text"
    posted_at: str | None = "time::attr(datetime)"
    source_url: str | None = "a.card-footer-item::attr(href)"
    application_url: str | None = "a.card-footer-item:last-child::attr(href)"
    detail_description: str | None = ".content p::text"


class GenericCareerScraper(BaseJobScraper):
    """Scrape conventional job cards from an unprotected public careers page."""

    def __init__(
        self,
        start_url: str,
        *,
        company: str | None = None,
        source: str = "company_careers",
        selectors: CareerPageSelectors | None = None,
        timeout: float = 30.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        self.start_url = start_url
        self.company = company
        self.source = source
        self.selectors = selectors or CareerPageSelectors()

    @staticmethod
    def _first(node: Selector, selector: str | None) -> str | None:
        if not selector:
            return None
        try:
            value = node.css(selector).get()
            return str(value) if value is not None else None
        except Exception:
            return None

    @staticmethod
    def _all_text(node: Selector, selector: str | None) -> str | None:
        if not selector:
            return None
        try:
            values = node.css(selector).getall()
        except Exception:
            return None
        text = " ".join(str(value) for value in values if value is not None).strip()
        return text or None

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        return normalize_job(raw_job, source=self.source, base_url=self.start_url)

    def _record_from_card(self, card: Selector, listing_url: str) -> dict[str, Any]:
        selectors = self.selectors
        detail_url = self._first(card, selectors.source_url)
        application_url = self._first(card, selectors.application_url)
        return {
            "external_id": self._first(card, selectors.external_id),
            "title": self._first(card, selectors.title),
            "company": self._first(card, selectors.company) or self.company,
            "location": self._first(card, selectors.location),
            "description": self._all_text(card, selectors.description),
            "skills": None,
            "source": self.source,
            "source_url": urljoin(listing_url, detail_url) if detail_url else listing_url,
            "application_url": urljoin(listing_url, application_url) if application_url else None,
            "posted_at": self._first(card, selectors.posted_at),
        }

    async def search_jobs(
        self,
        query: str | None = None,
        location: str | None = None,
        limit: int | None = None,
    ) -> list[NormalizedJob]:
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0:
            return []

        response = await self._fetch(self.start_url)
        if response is None:
            return []

        try:
            cards = response.css(self.selectors.job_card)
        except Exception:
            self.logger.exception("Invalid job-card selector for %s", self.start_url)
            return []

        query_key = query.casefold().strip() if query else None
        location_key = location.casefold().strip() if location else None
        jobs: list[NormalizedJob] = []
        for card in cards:
            try:
                job = self.normalize(self._record_from_card(card, response.url))
            except Exception:
                self.logger.exception("Skipping malformed job card from %s", response.url)
                continue

            searchable = " ".join(filter(None, (job.title, job.description))).casefold()
            if query_key and query_key not in searchable:
                continue
            if location_key and location_key not in (job.location or "").casefold():
                continue
            jobs.append(job)
            if limit is not None and len(jobs) >= limit:
                break

        self.logger.info("Discovered %d jobs from %s", len(jobs), self.start_url)
        return jobs

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        normalized = job if isinstance(job, NormalizedJob) else self.normalize({"application_url": job})
        details_url = normalized.application_url or normalized.source_url
        if not details_url:
            self.logger.warning("Cannot fetch details for a job without a URL")
            return normalized

        response = await self._fetch(details_url)
        if response is None:
            return None

        values = normalized.model_dump()
        description = self._all_text(response, self.selectors.detail_description)
        if description is not None:
            values["description"] = description
        values["source_url"] = response.url
        return self.normalize(values)
