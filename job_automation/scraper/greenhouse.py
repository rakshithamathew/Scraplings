"""Read-only adapter for Greenhouse's public Job Board API."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from html import unescape
import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from scrapling.parser import Selector

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper


_GREENHOUSE_LINK = re.compile(
    r"https?://(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?!embed/)([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_GREENHOUSE_API_LINK = re.compile(
    r"https?://boards-api(?:\.eu)?\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def _html_to_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    decoded = unescape(unescape(value))
    try:
        return str(Selector(decoded).get_all_text(separator=" ", strip=True)) or None
    except Exception:
        return decoded


class GreenhouseScraper(BaseJobScraper):
    """Discover published jobs through Greenhouse's unauthenticated GET API."""

    source = "greenhouse"

    def __init__(
        self,
        careers_url: str,
        *,
        company: str | None = None,
        board_token: str | None = None,
        timeout: float = 30.0,
        request_delay: float = 0.25,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        if request_delay < 0:
            raise ValueError("request_delay cannot be negative")
        self.careers_url = careers_url
        self.company = company
        self.board_token = board_token or self._token_from_url(careers_url)
        self.request_delay = request_delay
        host = urlsplit(careers_url).netloc.casefold()
        api_host = "boards-api.eu.greenhouse.io" if ".eu.greenhouse.io" in host else "boards-api.greenhouse.io"
        self.api_root = f"https://{api_host}/v1/boards"

    @staticmethod
    def _token_from_url(url: str) -> str | None:
        try:
            parsed = urlsplit(url)
            if "greenhouse.io" in parsed.netloc.casefold():
                query = parse_qs(parsed.query)
                token = query.get("for") or query.get("board_token")
                if token:
                    return token[0]
        except ValueError:
            pass
        query_match = re.search(r"[?&](?:for|board_token)=([A-Za-z0-9_-]+)", url, re.IGNORECASE)
        if query_match:
            return query_match.group(1)
        for pattern in (_GREENHOUSE_API_LINK, _GREENHOUSE_LINK):
            match = pattern.search(url)
            if match:
                return match.group(1)
        return None

    async def _resolve_board_token(self) -> str | None:
        if self.board_token:
            return self.board_token
        response = await self._fetch(self.careers_url)
        if response is None:
            return None
        body = response.body.decode(response.encoding or "utf-8", errors="replace")
        self.board_token = self._token_from_url(body)
        if not self.board_token:
            self.logger.warning("No Greenhouse board token found at %s", self.careers_url)
        return self.board_token

    async def _get_json(self, url: str) -> Any:
        if self.request_delay:
            await asyncio.sleep(self.request_delay)
        response = await self._fetch(url)
        if response is None:
            return None
        try:
            return response.json()
        except Exception:
            self.logger.exception("Greenhouse returned invalid JSON from %s", url)
            return None

    async def _resolve_company(self, token: str) -> str | None:
        if self.company:
            return self.company
        payload = await self._get_json(f"{self.api_root}/{quote(token, safe='')}")
        if isinstance(payload, Mapping) and isinstance(payload.get("name"), str):
            self.company = payload["name"]
        return self.company

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        raw = dict(raw_job) if isinstance(raw_job, Mapping) else {}
        absolute_url = raw.get("absolute_url")
        values = {
            "external_id": raw.get("id"),
            "title": raw.get("title"),
            "company": raw.get("company_name") or self.company,
            "location": raw.get("location"),
            "workplace_type": raw.get("workplace_type") or raw.get("workplaceType"),
            "description": _html_to_text(raw.get("content")),
            "skills": None,
            "source": self.source,
            "source_url": absolute_url,
            "application_url": absolute_url,
            "posted_at": raw.get("first_published"),
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
        token = await self._resolve_board_token()
        if not token:
            return []

        await self._resolve_company(token)
        endpoint = f"{self.api_root}/{quote(token, safe='')}/jobs?content=true"
        payload = await self._get_json(endpoint)
        records = payload.get("jobs") if isinstance(payload, Mapping) else None
        if not isinstance(records, list):
            self.logger.warning("Greenhouse jobs payload changed shape for board %s", token)
            return []

        query_key = query.casefold().strip() if query else None
        location_key = location.casefold().strip() if location else None
        jobs: list[NormalizedJob] = []
        for record in records:
            if not isinstance(record, Mapping):
                self.logger.warning("Skipping malformed Greenhouse job record")
                continue
            try:
                job = self.normalize(record)
            except Exception:
                self.logger.exception("Skipping Greenhouse job that could not be normalized")
                continue
            searchable = " ".join(filter(None, (job.title, job.description))).casefold()
            if query_key and query_key not in searchable:
                continue
            if location_key and location_key not in (job.location or "").casefold():
                continue
            jobs.append(job)
            if limit is not None and len(jobs) >= limit:
                break
        self.logger.info("Discovered %d Greenhouse jobs for %s", len(jobs), self.company or token)
        return jobs

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        token = await self._resolve_board_token()
        if not token:
            return None
        existing = job if isinstance(job, NormalizedJob) else None
        identifier = existing.external_id if existing else self._job_id_from_value(job)
        if not identifier:
            self.logger.warning("No Greenhouse job ID available for detail lookup")
            return existing
        endpoint = f"{self.api_root}/{quote(token, safe='')}/jobs/{quote(identifier, safe='')}"
        payload = await self._get_json(endpoint)
        if not isinstance(payload, Mapping):
            return None
        try:
            detail = self.normalize(payload)
            if existing and not detail.company:
                return detail.model_copy(update={"company": existing.company})
            return detail
        except Exception:
            self.logger.exception("Unable to normalize Greenhouse job %s", identifier)
            return None

    @staticmethod
    def _job_id_from_value(value: NormalizedJob | str) -> str | None:
        if not isinstance(value, str):
            return None
        parsed = urlsplit(value)
        query_id = parse_qs(parsed.query).get("gh_jid")
        if query_id:
            return query_id[0]
        if value.isdigit():
            return value
        match = re.search(r"/jobs/(\d+)", parsed.path)
        return match.group(1) if match else None
