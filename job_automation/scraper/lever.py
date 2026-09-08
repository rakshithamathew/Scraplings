"""Read-only adapter for Lever's public Postings API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from html import unescape
import logging
import re
from typing import Any
from urllib.parse import quote, urlsplit

from scrapling.parser import Selector

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper


_LEVER_LINK = re.compile(
    r"https?://(?:jobs|api)(\.eu)?\.lever\.co/(?:v0/postings/)?([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _html_to_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    decoded = unescape(value)
    try:
        return str(Selector(decoded).get_all_text(separator=" ", strip=True)) or None
    except Exception:
        return decoded


class LeverScraper(BaseJobScraper):
    """Discover published jobs through Lever's unauthenticated Postings API."""

    source = "lever"

    def __init__(
        self,
        careers_url: str,
        *,
        company: str | None = None,
        site: str | None = None,
        timeout: float = 30.0,
        request_delay: float = 0.25,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        if request_delay < 0:
            raise ValueError("request_delay cannot be negative")
        self.careers_url = careers_url
        discovered = self._site_from_url(careers_url)
        self.site = site or (discovered[0] if discovered else None)
        is_eu = bool(discovered and discovered[1]) or ".eu.lever.co" in urlsplit(careers_url).netloc.casefold()
        self.api_root = "https://api.eu.lever.co/v0/postings" if is_eu else "https://api.lever.co/v0/postings"
        self.company = company
        self.request_delay = request_delay

    @staticmethod
    def _site_from_url(url: str) -> tuple[str, bool] | None:
        match = _LEVER_LINK.search(url)
        return (match.group(2), bool(match.group(1))) if match else None

    async def _resolve_site(self) -> str | None:
        if self.site:
            return self.site
        response = await self._fetch(self.careers_url)
        if response is None:
            return None
        body = response.body.decode(response.encoding or "utf-8", errors="replace")
        discovered = self._site_from_url(body)
        if discovered:
            self.site = discovered[0]
            if discovered[1]:
                self.api_root = "https://api.eu.lever.co/v0/postings"
        else:
            self.logger.warning("No Lever site name found at %s", self.careers_url)
        return self.site

    async def _get_json(self, url: str) -> Any:
        if self.request_delay:
            await asyncio.sleep(self.request_delay)
        response = await self._fetch(url)
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            self.logger.exception("Lever returned invalid JSON from %s", url)
            return None

    @staticmethod
    def _description(raw: Mapping[str, Any]) -> str | None:
        parts: list[str] = []
        primary = raw.get("descriptionPlain") or _html_to_text(raw.get("description"))
        if isinstance(primary, str) and primary.strip():
            parts.append(primary)
        lists = raw.get("lists")
        if isinstance(lists, list):
            for item in lists:
                if not isinstance(item, Mapping):
                    continue
                heading = item.get("text")
                content = _html_to_text(item.get("content"))
                part = " ".join(str(value) for value in (heading, content) if value)
                if part:
                    parts.append(part)
        additional = raw.get("additionalPlain") or _html_to_text(raw.get("additional"))
        if isinstance(additional, str) and additional.strip():
            parts.append(additional)
        return " ".join(parts) or None

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        raw = dict(raw_job) if isinstance(raw_job, Mapping) else {}
        values = {
            "external_id": raw.get("id"),
            "title": raw.get("text"),
            "company": self.company,
            "location": raw.get("categories", {}).get("location")
            if isinstance(raw.get("categories"), Mapping)
            else None,
            "workplace_type": raw.get("workplaceType") or raw.get("workplace_type"),
            "description": self._description(raw),
            "description_complete": bool(raw.get("descriptionPlain") or raw.get("description")),
            "skills": None,
            "source": self.source,
            "source_url": raw.get("hostedUrl"),
            "application_url": raw.get("applyUrl"),
            "posted_at": raw.get("createdAt"),
            "is_open": True,
        }
        return normalize_job(values, source=self.source, base_url=self.careers_url)

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
        site = await self._resolve_site()
        if not site:
            return []
        endpoint = f"{self.api_root}/{quote(site, safe='')}?mode=json"
        payload = await self._get_json(endpoint)
        if not isinstance(payload, list):
            self.logger.warning("Lever postings payload changed shape for site %s", site)
            return []

        query_key = query.casefold().strip() if query else None
        location_key = location.casefold().strip() if location else None
        jobs: list[NormalizedJob] = []
        for record in payload:
            if not isinstance(record, Mapping):
                self.logger.warning("Skipping malformed Lever job record")
                continue
            try:
                job = self.normalize(record)
            except Exception:
                self.logger.exception("Skipping Lever job that could not be normalized")
                continue
            searchable = " ".join(filter(None, (job.title, job.description))).casefold()
            if query_key and query_key not in searchable:
                continue
            if location_key and location_key not in (job.location or "").casefold():
                continue
            jobs.append(job)
            if limit is not None and len(jobs) >= limit:
                break
        self.logger.info("Discovered %d Lever jobs for %s", len(jobs), self.company or site)
        return jobs

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        site = await self._resolve_site()
        if not site:
            return None
        existing = job if isinstance(job, NormalizedJob) else None
        identifier = existing.external_id if existing else self._job_id_from_value(job)
        if not identifier:
            self.logger.warning("No Lever posting ID available for detail lookup")
            return existing
        endpoint = f"{self.api_root}/{quote(site, safe='')}/{quote(identifier, safe='')}?mode=json"
        payload = await self._get_json(endpoint)
        if not isinstance(payload, Mapping):
            return None
        try:
            detail = self.normalize(payload)
            if existing and not detail.company:
                return detail.model_copy(update={"company": existing.company})
            return detail
        except Exception:
            self.logger.exception("Unable to normalize Lever job %s", identifier)
            return None

    @staticmethod
    def _job_id_from_value(value: NormalizedJob | str) -> str | None:
        if not isinstance(value, str):
            return None
        if "/" not in value:
            return value or None
        segments = [segment for segment in urlsplit(value).path.split("/") if segment]
        return segments[-2] if segments and segments[-1] == "apply" and len(segments) > 1 else (segments[-1] if segments else None)
