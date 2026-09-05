"""Defensive, configurable adapter for public Workday career sites."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from html import unescape
import logging
import re
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlsplit

from scrapling.fetchers import AsyncFetcher
from scrapling.parser import Selector

from job_automation.normalizer.jobs import NormalizedJob, normalize_job
from job_automation.scraper.base import BaseJobScraper


_WORKDAY_HOST = re.compile(
    r"^(?P<tenant>[A-Za-z0-9_-]+)\.wd\d+\.(?:myworkdayjobs|myworkdaysite)\.com$",
    re.IGNORECASE,
)
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


def _value_at_path(payload: Any, path: str) -> Any:
    value = payload
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _first_value(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None:
            return value
    return None


def _html_to_text(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    decoded = unescape(value)
    try:
        text = str(Selector(decoded).get_all_text(separator=" ", strip=True))
        return re.sub(r"\s+([,.;:!?])", r"\1", text) or None
    except Exception:
        return decoded


@dataclass(frozen=True, slots=True)
class WorkdaySiteConfig:
    """All tenant-specific assumptions needed to talk to one Workday site."""

    careers_url: str
    company: str | None = None
    tenant: str | None = None
    site_id: str | None = None
    locale: str | None = None
    search_url: str | None = None
    detail_url_template: str | None = None
    discovery_method: Literal["GET", "POST"] = "POST"
    job_list_paths: tuple[str, ...] = ("jobPostings", "data.jobPostings")
    detail_root_paths: tuple[str, ...] = ("jobPostingInfo", "data.jobPostingInfo")
    search_payload: Mapping[str, Any] = field(default_factory=dict)
    page_size: int = 20
    max_pages: int = 3
    unsupported_reason: str | None = None

    @property
    def supported(self) -> bool:
        return bool(self.search_url) and self.unsupported_reason is None

    @classmethod
    def from_careers_url(
        cls,
        careers_url: str,
        *,
        company: str | None = None,
    ) -> "WorkdaySiteConfig":
        """Infer only the common public CXS variant; never guess unknown sites."""
        try:
            parsed = urlsplit(careers_url)
        except ValueError:
            return cls(careers_url, company=company, unsupported_reason="invalid careers URL")
        host_match = _WORKDAY_HOST.fullmatch(parsed.netloc)
        if parsed.scheme not in {"http", "https"} or not host_match:
            return cls(
                careers_url,
                company=company,
                unsupported_reason="host is not a recognized public Workday careers host",
            )

        parts = [part for part in parsed.path.split("/") if part]
        locale = parts.pop(0) if parts and _LOCALE.fullmatch(parts[0]) else None
        site_id = parts[0] if parts else None
        if not site_id:
            return cls(
                careers_url,
                company=company,
                tenant=host_match.group("tenant"),
                locale=locale,
                unsupported_reason="the Workday site identifier is missing from the URL",
            )

        tenant = host_match.group("tenant")
        api_root = f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{quote(tenant, safe='')}/{quote(site_id, safe='')}"
        return cls(
            careers_url=careers_url.rstrip("/"),
            company=company,
            tenant=tenant,
            site_id=site_id,
            locale=locale,
            search_url=f"{api_root}/jobs",
            detail_url_template=f"{api_root}{{external_path}}",
        )


class WorkdayDiscoveryParser:
    """Locate listing records without assigning source-specific field meanings."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths

    def parse(self, payload: Any) -> list[Mapping[str, Any]] | None:
        for path in self.paths:
            records = _value_at_path(payload, path)
            if isinstance(records, list):
                return [record for record in records if isinstance(record, Mapping)]
        return None

    @staticmethod
    def total(payload: Any) -> int | None:
        if not isinstance(payload, Mapping):
            return None
        for name in ("total", "totalResults", "totalCount"):
            value = payload.get(name)
            if isinstance(value, int):
                return value
        return None


class WorkdayDetailParser:
    """Unwrap a configured Workday detail response."""

    def __init__(self, paths: tuple[str, ...]) -> None:
        self.paths = paths

    def parse(self, payload: Any) -> Mapping[str, Any] | None:
        for path in self.paths:
            record = _value_at_path(payload, path)
            if isinstance(record, Mapping):
                return record
        return payload if isinstance(payload, Mapping) else None


class WorkdayScraper(BaseJobScraper):
    """Discover jobs from a configured, publicly accessible Workday variant."""

    source = "workday"

    def __init__(
        self,
        careers_url: str,
        *,
        config: WorkdaySiteConfig | None = None,
        company: str | None = None,
        timeout: float = 30.0,
        request_delay: float = 0.5,
        logger: logging.Logger | None = None,
    ) -> None:
        super().__init__(timeout=timeout, logger=logger)
        if request_delay < 0:
            raise ValueError("request_delay cannot be negative")
        self.config = config or WorkdaySiteConfig.from_careers_url(careers_url, company=company)
        self.company = company or self.config.company
        self.request_delay = request_delay
        self.discovery_parser = WorkdayDiscoveryParser(self.config.job_list_paths)
        self.detail_parser = WorkdayDetailParser(self.config.detail_root_paths)

    def _unsupported(self) -> bool:
        if self.config.supported:
            return False
        self.logger.warning(
            "Unsupported Workday variant for %s: %s. Supply an explicit WorkdaySiteConfig.",
            self.config.careers_url,
            self.config.unsupported_reason or "no public search endpoint configured",
        )
        return True

    async def _request_json(
        self,
        url: str,
        *,
        method: Literal["GET", "POST"] = "GET",
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        if self.request_delay:
            await asyncio.sleep(self.request_delay)
        try:
            if method == "POST":
                request = AsyncFetcher.post(
                    url,
                    json=dict(payload or {}),
                    timeout=self.timeout,
                    headers={"accept": "application/json", "content-type": "application/json"},
                )
                response = await asyncio.wait_for(request, timeout=self.timeout + 1)
                if response.status >= 400:
                    self.logger.warning("Workday returned HTTP %s for %s", response.status, url)
                    return None
            else:
                response = await self._fetch(url)
                if response is None:
                    return None
            return response.json()
        except TimeoutError:
            self.logger.warning("Workday request timed out after %.1fs: %s", self.timeout, url)
        except Exception:
            self.logger.exception("Workday request or JSON parsing failed: %s", url)
        return None

    def _public_job_url(self, external_path: Any) -> str | None:
        if not isinstance(external_path, str) or not external_path.strip():
            return None
        if urlsplit(external_path).scheme:
            return external_path
        return f"{self.config.careers_url.rstrip('/')}/{external_path.lstrip('/')}"

    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        raw = dict(raw_job) if isinstance(raw_job, Mapping) else {}
        external_path = _first_value(raw, "externalPath", "external_path", "jobPath")
        public_url = self._public_job_url(external_path)
        locations = _first_value(raw, "locationsText", "location", "locations")
        description = _html_to_text(_first_value(raw, "jobDescription", "description", "descriptionPlain"))
        values = {
            "external_id": _first_value(raw, "jobReqId", "external_id", "requisitionId", "id"),
            "title": _first_value(raw, "title", "jobTitle", "text"),
            "company": _first_value(raw, "company", "companyName") or self.company,
            "location": locations,
            "description": description,
            "skills": raw.get("skills"),
            "source": self.source,
            "source_url": _first_value(raw, "sourceUrl", "externalUrl", "source_url") or public_url,
            "application_url": _first_value(raw, "applicationUrl", "applyUrl", "application_url") or public_url,
            "posted_at": _first_value(raw, "postedOn", "postedAt", "datePosted", "startDate", "posted_at"),
        }
        return normalize_job(values, source=self.source, base_url=self.config.careers_url)

    def _search_payload(self, query: str | None, offset: int, limit: int) -> dict[str, Any]:
        payload = {
            "appliedFacets": {},
            "limit": limit,
            "offset": offset,
            "searchText": query or "",
        }
        payload.update(self.config.search_payload)
        payload["limit"] = limit
        payload["offset"] = offset
        if query is not None:
            payload["searchText"] = query
        return payload

    async def search_jobs(
        self,
        query: str | None = None,
        location: str | None = None,
        limit: int | None = None,
    ) -> list[NormalizedJob]:
        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0 or self._unsupported():
            return []

        page_size = max(1, self.config.page_size)
        target = limit if limit is not None else page_size * max(1, self.config.max_pages)
        jobs: list[NormalizedJob] = []
        offset = 0
        location_key = location.casefold().strip() if location else None
        for _ in range(max(1, self.config.max_pages)):
            fetch_size = min(page_size, max(1, target - len(jobs)))
            payload = self._search_payload(query, offset, fetch_size)
            if self.config.discovery_method == "GET":
                url = f"{self.config.search_url}?{urlencode(payload, doseq=True)}"
                response_payload = await self._request_json(url)
            else:
                response_payload = await self._request_json(
                    self.config.search_url or "",
                    method="POST",
                    payload=payload,
                )
            records = self.discovery_parser.parse(response_payload)
            if records is None:
                self.logger.warning(
                    "Unsupported Workday discovery payload for %s; expected one of %s",
                    self.config.careers_url,
                    self.config.job_list_paths,
                )
                break
            if not records:
                break
            for record in records:
                try:
                    job = self.normalize(record)
                except Exception:
                    self.logger.exception("Skipping Workday job that could not be normalized")
                    continue
                if location_key and location_key not in (job.location or "").casefold():
                    continue
                jobs.append(job)
                if len(jobs) >= target:
                    break
            if len(jobs) >= target:
                break
            offset += len(records)
            total = self.discovery_parser.total(response_payload)
            if len(records) < fetch_size or (total is not None and offset >= total):
                break
        self.logger.info("Discovered %d Workday jobs from %s", len(jobs), self.config.careers_url)
        return jobs[:limit] if limit is not None else jobs

    @staticmethod
    def _external_path(job: NormalizedJob | str) -> str | None:
        value = job.source_url if isinstance(job, NormalizedJob) else job
        if not value:
            return None
        parsed = urlsplit(value)
        match = re.search(r"(/job/.*)$", parsed.path)
        return match.group(1) if match else None

    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        existing = job if isinstance(job, NormalizedJob) else None
        if self._unsupported() or not self.config.detail_url_template:
            self.logger.warning("Workday detail lookup is unsupported for %s", self.config.careers_url)
            return existing
        external_path = self._external_path(job)
        if not external_path:
            self.logger.warning("No Workday external job path available for detail lookup")
            return existing
        endpoint = self.config.detail_url_template.format(
            external_path=external_path,
            external_id=quote(existing.external_id or "", safe="") if existing else "",
        )
        payload = await self._request_json(endpoint)
        record = self.detail_parser.parse(payload)
        if record is None:
            self.logger.warning("Unsupported Workday detail payload from %s", endpoint)
            return existing
        merged = existing.model_dump() if existing else {}
        merged.update({key: value for key, value in record.items() if value is not None})
        merged.setdefault("externalPath", external_path)
        try:
            return self.normalize(merged)
        except Exception:
            self.logger.exception("Unable to normalize Workday detail response from %s", endpoint)
            return existing
