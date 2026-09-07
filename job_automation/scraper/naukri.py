"""Browser-rendered adapter for Naukri's public search result pages."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import logging
import re
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from scrapling.fetchers import DynamicFetcher
from scrapling.parser import Selector

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper
from job_automation.scraper.linkedin import DEFAULT_TITLES


_BLOCK_MARKERS = ("captcha", "verify you are human", "access denied", "unusual traffic")


class NaukriScraper(BaseJobScraper):
    """Discover only jobs visible on Naukri without authentication."""

    source = "naukri"

    def __init__(
        self,
        careers_url: str = "https://www.naukri.com/jobs-in-india",
        *,
        company: str | None = None,
        queries: Sequence[str] = DEFAULT_TITLES,
        searches: Sequence[Mapping[str, Any]] | None = None,
        max_pages: int = 1,
        browser_wait_ms: int = 2500,
        timeout: float = 30.0,
        request_delay: float = 3.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        if request_delay < 0 or max_pages < 1 or browser_wait_ms < 0:
            raise ValueError("request delay/wait must be non-negative and max_pages must be positive")
        self.careers_url = careers_url
        self.company = company
        self.queries = tuple(str(value).strip() for value in queries if str(value).strip())
        self.searches = tuple(searches or ({"location": "Bengaluru"}, {"location": "Remote", "remote": True}))
        self.max_pages = max_pages
        self.browser_wait_ms = browser_wait_ms
        self.request_delay = request_delay

    @staticmethod
    def _first(node: Selector, selector: str) -> str | None:
        try:
            value = node.css(selector).get()
            return str(value).strip() if value is not None else None
        except Exception:
            return None

    @staticmethod
    def _all_text(node: Selector, selector: str) -> str | None:
        try:
            value = " ".join(str(item).strip() for item in node.css(selector).getall() if item is not None).strip()
            return value or None
        except Exception:
            return None

    @staticmethod
    def _all(node: Selector, selector: str) -> list[str] | None:
        try:
            values = [str(item).strip() for item in node.css(selector).getall() if str(item).strip()]
            return values or None
        except Exception:
            return None

    @staticmethod
    def _canonical_url(value: str | None) -> str | None:
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        except ValueError:
            return value

    @staticmethod
    def _external_id(url: str | None) -> str | None:
        if not url:
            return None
        tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
        match = re.search(r"-(\d{8,})$", tail)
        return match.group(1) if match else tail or None

    def _search_url(self, query: str, search: Mapping[str, Any], page: int) -> str:
        location = str(search.get("location") or "India")
        params = urlencode({"k": query, "l": location})
        separator = "&" if "?" in self.careers_url else "?"
        url = f"{self.careers_url}{separator}{params}"
        return f"{url}&pageNo={page + 1}" if page else url

    async def _render(self, url: str) -> Selector | None:
        if self.request_delay:
            await asyncio.sleep(self.request_delay)
        try:
            response = await asyncio.wait_for(
                DynamicFetcher.async_fetch(
                    url,
                    headless=True,
                    timeout=int(self.timeout * 1000),
                    wait=self.browser_wait_ms,
                    wait_selector="div.srp-jobtuple-wrapper",
                    wait_selector_state="attached",
                ),
                timeout=self.timeout + (self.browser_wait_ms / 1000) + 5,
            )
            if response.status >= 400:
                self.logger.warning("Naukri returned HTTP %s for %s", response.status, url)
                return None
            return response
        except TimeoutError:
            self.logger.warning("Naukri rendering timed out: %s", url)
        except Exception:
            self.logger.exception("Unable to render public Naukri search: %s", url)
        return None

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        return normalize_job(raw_job, source=self.source, base_url=self.careers_url)

    def _record(self, card: Selector, listing_url: str, *, remote: bool) -> dict[str, Any]:
        application_url = self._canonical_url(self._first(card, "a.title::attr(href)"))
        description = self._all_text(card, "span.job-desc::text, .job-desc::text")
        experience = self._first(card, "span.expwdth::text")
        if experience:
            description = " ".join(filter(None, (description, f"Experience: {experience}")))
        skills = self._all(card, "ul.tags-gt li::text, .tags-gt li::text")
        return {
            "external_id": self._external_id(application_url),
            "title": self._first(card, "a.title::text"),
            "company": self._first(card, "a.comp-name::text") or self.company,
            "location": self._first(card, "span.locWdth::text"),
            "workplace_type": "REMOTE" if remote else None,
            "description": description,
            "skills": skills,
            "source": self.source,
            "source_url": application_url or listing_url,
            "application_url": application_url,
            # Relative labels such as "2 days ago" are deliberately not invented as dates.
            "posted_at": None,
            "is_open": True,
        }

    @staticmethod
    def _blocked(response: Selector) -> bool:
        try:
            text = str(response.get_all_text(separator=" ", strip=True)).casefold()
        except Exception:
            return False
        return any(marker in text for marker in _BLOCK_MARKERS)

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
        queries = (query,) if query else self.queries
        searches: Sequence[Mapping[str, Any]] = ({"location": location},) if location else self.searches
        jobs: list[NormalizedJob] = []
        seen: set[str] = set()
        for title in queries:
            for search in searches:
                for page in range(self.max_pages):
                    url = self._search_url(title, search, page)
                    response = await self._render(url)
                    if response is None:
                        break
                    if self._blocked(response):
                        self.logger.warning("Naukri requires human verification at %s", url)
                        break
                    try:
                        cards = response.css("div.srp-jobtuple-wrapper")
                    except Exception:
                        self.logger.exception("Naukri result layout could not be parsed: %s", url)
                        break
                    if not cards:
                        self.logger.warning("No public Naukri job cards found; layout or access may have changed: %s", url)
                        break
                    for card in cards:
                        try:
                            job = self.normalize(self._record(card, response.url, remote=bool(search.get("remote"))))
                            key = job.external_id or job.application_url
                            if not key or key in seen or not job.title or not job.company:
                                continue
                            seen.add(key)
                            jobs.append(job)
                        except Exception:
                            self.logger.exception("Skipping one malformed Naukri job")
                        if limit is not None and len(jobs) >= limit:
                            return jobs
        self.logger.info("Discovered %d unique public Naukri jobs", len(jobs))
        return jobs

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        existing = job if isinstance(job, NormalizedJob) else self.normalize({"application_url": job})
        url = existing.application_url or existing.source_url
        if not url:
            return existing
        response = await self._render(url)
        if response is None or self._blocked(response):
            return None
        values = existing.model_dump()
        values.update(
            title=self._first(response, "h1::text") or existing.title,
            description=self._all_text(response, "div.styles_JDC__dang-inner-html__h0K4t ::text, .job-desc ::text")
            or existing.description,
            source_url=response.url,
        )
        return self.normalize(values)
