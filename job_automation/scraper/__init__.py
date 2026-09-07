"""Job-source scraper adapters built on the local Scrapling installation."""

from job_automation.scraper.base import BaseJobScraper
from job_automation.scraper.company_careers import CareerPageSelectors, GenericCareerScraper
from job_automation.scraper.greenhouse import GreenhouseScraper
from job_automation.scraper.lever import LeverScraper
from job_automation.scraper.linkedin import LinkedInScraper
from job_automation.scraper.naukri import NaukriScraper
from job_automation.scraper.workday import (
    WorkdayDetailParser,
    WorkdayDiscoveryParser,
    WorkdayScraper,
    WorkdaySiteConfig,
)

__all__ = [
    "BaseJobScraper",
    "CareerPageSelectors",
    "GenericCareerScraper",
    "GreenhouseScraper",
    "LeverScraper",
    "LinkedInScraper",
    "NaukriScraper",
    "WorkdayDetailParser",
    "WorkdayDiscoveryParser",
    "WorkdayScraper",
    "WorkdaySiteConfig",
]
