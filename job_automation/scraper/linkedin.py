"""Conservative scraper for LinkedIn's public guest job-search pages.

This adapter does not log in, use private APIs, or attempt to defeat access
controls. LinkedIn may limit guest results or require sign-in at any time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from scrapling.parser import Selector
from scrapling.fetchers import AsyncFetcher

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper


DEFAULT_TITLES = (
    "Frontend Developer",
    "Senior Frontend Developer",
    "React.js Developer",
    "React Engineer",
    "Software Engineer",
    "Senior Software Engineer",
    "Full Stack Developer",
    "Frontend Lead",
)
_JOB_ID = re.compile(r"(?:jobPosting:|/jobs/view/(?:[^/?#]*-)?)(\d+)", re.I)
_BLOCK_MARKERS = ("captcha", "security verification", "authwall", "sign in to view")


class LinkedInScraper(BaseJobScraper):
    """Discover jobs exposed on LinkedIn's unauthenticated guest pages."""

    source = "linkedin"

    def __init__(
        self,
        careers_url: str = "https://www.linkedin.com/jobs/search/",
        *,
        company: str | None = None,
        queries: Sequence[str] = DEFAULT_TITLES,
        searches: Sequence[Mapping[str, Any]] | None = None,
        date_posted_seconds: int | None = None,
        max_pages: int = 1,
        page_size: int = 25,
        timeout: float = 30.0,
        request_delay: float = 2.0,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        if request_delay < 0 or max_pages < 1 or page_size < 1:
            raise ValueError("request_delay must be non-negative and page limits must be positive")
        self.careers_url = careers_url
        self.company = company
        self.queries = tuple(str(value).strip() for value in queries if str(value).strip())
        self.searches = tuple(searches or ({"location": "Bengaluru, Karnataka, India"}, {"location": "Worldwide", "remote": True}))
        self.date_posted_seconds = date_posted_seconds
        self.max_pages = max_pages
        self.page_size = page_size
        self.request_delay = request_delay
        self.access_limited = False

    async def _fetch(self, url: str) -> Selector | None:
        # One ordinary public request. Never retry a challenge or follow a login redirect.
        host = urlsplit(url).hostname or ""
        if host != "linkedin.com" and not host.endswith(".linkedin.com"):
            raise ValueError("LinkedIn discovery only fetches LinkedIn pages")
        if self.access_limited:
            return None
        try:
            response = await asyncio.wait_for(
                AsyncFetcher.get(url, timeout=self.timeout, retries=1,
                                 follow_redirects=False, impersonate=None, stealthy_headers=False),
                timeout=self.timeout + 1,
            )
            if response.status in {401, 403, 429, 999} or 300 <= response.status < 400 or self._blocked(response):
                self.access_limited = True
                self.logger.warning("LinkedIn access stopped: HTTP %s at %s", response.status, url)
                return None
            return response if response.status < 400 else None
        except Exception:
            self.logger.exception("Unable to fetch LinkedIn public page: %s", url)
            raise RuntimeError("LinkedIn public page request failed") from None

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
            values = node.css(selector).getall()
            value = " ".join(str(item).strip() for item in values if item is not None).strip()
            return value or None
        except Exception:
            return None

    @staticmethod
    def _external_id(*values: str | None) -> str | None:
        for value in values:
            if value and (match := _JOB_ID.search(value)):
                return match.group(1)
        return None

    def _search_url(self, query: str, search: Mapping[str, Any], page: int) -> str:
        params: dict[str, str | int] = {
            "keywords": query,
            "location": str(search.get("location") or "Worldwide"),
            "start": page * self.page_size,
        }
        if bool(search.get("remote")):
            params["f_WT"] = "2"
        if self.date_posted_seconds:
            params["f_TPR"] = f"r{self.date_posted_seconds}"
        return f"{self.careers_url.rstrip('/')}/?{urlencode(params)}"

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        return normalize_job(raw_job, source=self.source, base_url=self.careers_url)

    def _record(self, card: Selector, listing_url: str, *, remote: bool) -> dict[str, Any]:
        href = self._first(card, "a.base-card__full-link::attr(href)")
        urn = self._first(card, "div.base-search-card::attr(data-entity-urn)") or self._first(
            card, "::attr(data-entity-urn)"
        )
        external_id = self._external_id(urn, href)
        application_url = f"https://www.linkedin.com/jobs/view/{external_id}" if external_id else href
        return {
            "external_id": external_id,
            "title": self._first(card, "h3.base-search-card__title::text"),
            "company": self._first(card, "h4.base-search-card__subtitle a::text")
            or self._first(card, "h4.base-search-card__subtitle::text")
            or self.company,
            "location": self._first(card, "span.job-search-card__location::text"),
            "workplace_type": "REMOTE" if remote else None,
            "description": None,
            "source": self.source,
            "source_url": application_url or listing_url,
            "application_url": application_url,
            "posted_at": self._first(card, "time::attr(datetime)"),
            "is_open": True,
        }

    @staticmethod
    def _blocked(response: Selector) -> bool:
        if any(marker in str(getattr(response, "url", "")).casefold() for marker in ("/authwall", "/login", "/checkpoint", "/challenge")):
            return True
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
        access_limited = False

        for title in queries:
            for search in searches:
                for page in range(self.max_pages):
                    if self.access_limited or access_limited:
                        break
                    if self.request_delay:
                        await asyncio.sleep(self.request_delay)
                    url = self._search_url(title, search, page)
                    response = await self._fetch(url)
                    if response is None:
                        break
                    if self._blocked(response):
                        access_limited = True
                        break
                    try:
                        cards = response.css("div.base-card")
                    except Exception:
                        self.logger.exception("LinkedIn listing layout could not be parsed: %s", url)
                        break
                    if not cards:
                        if self._blocked(response):
                            access_limited = True
                            self.logger.warning("LinkedIn guest access requires verification or sign-in: %s", url)
                            break
                        self.logger.info("No accessible LinkedIn cards at %s", url)
                        break
                    for card in cards:
                        try:
                            job = self.normalize(self._record(card, response.url, remote=bool(search.get("remote"))))
                            key = job.external_id or job.application_url
                            if not key or key in seen or not job.title or not job.company:
                                continue
                            seen.add(key)
                            if self.request_delay:
                                await asyncio.sleep(self.request_delay)
                            detailed = await self.get_job_details(job)
                            if self.access_limited:
                                break
                            if detailed is not None and detailed.description:
                                jobs.append(detailed)
                        except Exception:
                            self.logger.exception("Skipping one malformed LinkedIn guest job")
                        if limit is not None and len(jobs) >= limit:
                            return jobs
        if not jobs and (access_limited or self.access_limited):
            raise RuntimeError("LinkedIn guest search is currently requiring verification or sign-in")
        self.logger.info("Discovered %d unique LinkedIn guest jobs", len(jobs))
        return jobs

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        existing = job if isinstance(job, NormalizedJob) else self.normalize({"application_url": job})
        url = existing.source_url or existing.application_url
        if not url:
            return existing
        response = await self._fetch(url)
        if response is None:
            return None
        if self._blocked(response):
            self.access_limited = True
            return None
        values = existing.model_dump()
        structured: dict[str, Any] = {}
        for script in response.css('script[type="application/ld+json"]::text').getall():
            try:
                payload = json.loads(script)
            except (ValueError, TypeError):
                continue
            nodes = payload if isinstance(payload, list) else [payload]
            for node in nodes:
                if isinstance(node, dict):
                    nodes.extend(node.get("@graph", []))
                    if node.get("@type") == "JobPosting":
                        structured = node
        description = self._all_text(response, "div.show-more-less-html__markup ::text")
        if not description and structured.get("description"):
            description = Selector(str(structured["description"])).get_all_text(separator=" ", strip=True)
        workplace = self._all_text(response, ".topcard__flavor--workplace-type ::text")
        for item in response.css("li.description__job-criteria-item"):
            label = self._all_text(item, "h3 ::text") or ""
            if "workplace" in label.casefold():
                workplace = self._all_text(item, "span ::text")
        if not workplace and structured.get("jobLocationType") == "TELECOMMUTE":
            workplace = "REMOTE"
        eligibility = structured.get("applicantLocationRequirements", [])
        if isinstance(eligibility, dict):
            eligibility = [eligibility]
        eligible_locations = ", ".join(
            item["name"] for item in eligibility
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ) if isinstance(eligibility, list) else ""
        apply_url = self._first(response, "a.apply-button::attr(href)") or self._first(
            response, "a.top-card-layout__cta::attr(href)")
        if apply_url:
            apply_url = urljoin(url, apply_url)
            parsed = urlsplit(apply_url)
            if parsed.hostname and parsed.hostname.endswith("linkedin.com") and parsed.path == "/jobs/view/externalApply/":
                apply_url = parse_qs(parsed.query).get("url", [None])[0]
            if not apply_url or urlsplit(apply_url).scheme not in {"https", "http"} or any(
                marker in urlsplit(apply_url).path for marker in ("/login", "/signup", "/authwall")
            ):
                apply_url = None
        values.update(
            title=self._first(response, "h1.top-card-layout__title::text") or existing.title,
            company=self._first(response, "a.topcard__org-name-link::text") or existing.company,
            location=eligible_locations or self._first(response, "span.topcard__flavor--bullet::text") or existing.location,
            description=description or existing.description,
            description_complete=bool(description),
            workplace_type=workplace or existing.workplace_type,
            source_url=existing.source_url or url,
            application_url=apply_url or existing.application_url,
            posted_at=structured.get("datePosted") or self._first(response, "time::attr(datetime)") or existing.posted_at,
        )
        return self.normalize(values)
