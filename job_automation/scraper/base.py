"""Shared contracts and guarded HTTP fetching for job-source scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from scrapling.fetchers import AsyncFetcher
from scrapling.engines.toolbelt.custom import Response

from job_automation.normalizer.jobs import NormalizedJob


class BaseJobScraper(ABC):
    """Base contract for read-only job discovery adapters.

    Scrapers fetch and normalize public job records only. Persistence belongs to
    a separate application/repository layer.
    """

    def __init__(self, *, timeout: float = 30.0, logger: logging.Logger | None = None) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = float(timeout)
        self.logger = logger or logging.getLogger(f"{__name__}.{type(self).__name__}")

    async def _fetch(self, url: str) -> Response | None:
        """Fetch a public page with both client and coroutine time limits."""
        try:
            response = await asyncio.wait_for(
                AsyncFetcher.get(url, timeout=self.timeout),
                timeout=self.timeout + 1,
            )
            if response.status >= 400:
                self.logger.warning("Request returned HTTP %s for %s", response.status, url)
                return None
            return response
        except TimeoutError:
            self.logger.warning("Request timed out after %.1fs: %s", self.timeout, url)
        except Exception:
            self.logger.exception("Unable to fetch public jobs page: %s", url)
        return None

    @abstractmethod
    async def search_jobs(
        self,
        query: str | None = None,
        location: str | None = None,
        limit: int | None = None,
    ) -> list[NormalizedJob]:
        """Discover jobs and return canonical normalized records."""

    @abstractmethod
    async def get_job_details(self, job: NormalizedJob | str) -> NormalizedJob | None:
        """Fetch a public job-details page and return a canonical record."""

    @abstractmethod
    def normalize(self, raw_job: Mapping[str, Any] | None) -> NormalizedJob:
        """Convert one source-specific record to the canonical job schema."""
