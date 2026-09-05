"""Run small, read-only checks against public Greenhouse and Lever boards."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_automation.normalizer import NormalizedJob
from job_automation.scraper import GreenhouseScraper, LeverScraper


def print_job(job: NormalizedJob) -> None:
    print(f"source: {job.source or ''}")
    print(f"company: {job.company or ''}")
    print(f"title: {job.title or ''}")
    print(f"location: {job.location or ''}")
    print(f"application URL: {job.application_url or ''}")
    print()


async def run(args: argparse.Namespace) -> int:
    scrapers = (
        GreenhouseScraper(
            args.greenhouse_url,
            company=args.greenhouse_company,
            request_delay=args.delay,
        ),
        LeverScraper(
            args.lever_url,
            company=args.lever_company,
            request_delay=args.delay,
        ),
    )
    total = 0
    for scraper in scrapers:
        jobs = await scraper.search_jobs(limit=args.limit)
        for job in jobs:
            print_job(job)
        total += len(jobs)
    print(f"total jobs printed: {total}")
    return 0 if total else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--greenhouse-url",
        default="https://job-boards.greenhouse.io/greenhouse",
        help="Public Greenhouse board, API, or careers URL",
    )
    parser.add_argument("--greenhouse-company", default=None)
    parser.add_argument(
        "--lever-url",
        default="https://jobs.lever.co/leverdemo",
        help="Public Lever board, API, or careers URL",
    )
    parser.add_argument("--lever-company", default="Lever Demo")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.5)
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(asyncio.run(run(parse_args())))
