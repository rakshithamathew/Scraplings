"""Application-provider detection using URL first and rendered-page hints second."""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlsplit


class ApplicationProvider(str, Enum):
    GREENHOUSE = "GREENHOUSE"
    LEVER = "LEVER"
    WORKDAY = "WORKDAY"
    ASHBY = "ASHBY"
    SMARTRECRUITERS = "SMARTRECRUITERS"
    GENERIC = "GENERIC"


_HOST_PROVIDERS = (
    ("greenhouse.io", ApplicationProvider.GREENHOUSE),
    ("lever.co", ApplicationProvider.LEVER),
    ("myworkdayjobs.com", ApplicationProvider.WORKDAY),
    ("workdayjobs.com", ApplicationProvider.WORKDAY),
    ("ashbyhq.com", ApplicationProvider.ASHBY),
    ("smartrecruiters.com", ApplicationProvider.SMARTRECRUITERS),
)


def detect_provider_from_url(url: str | None) -> ApplicationProvider:
    try:
        host = urlsplit(url or "").netloc.casefold()
    except ValueError:
        return ApplicationProvider.GENERIC
    return next((provider for marker, provider in _HOST_PROVIDERS if marker in host), ApplicationProvider.GENERIC)


async def detect_provider(url: str | None, page: object | None = None) -> ApplicationProvider:
    """Detect an ATS without assuming a universal DOM."""
    detected = detect_provider_from_url(url)
    if detected is not ApplicationProvider.GENERIC or page is None:
        return detected
    try:
        content = (await page.content()).casefold()  # type: ignore[attr-defined]
    except Exception:
        return detected
    hints = (
        ("greenhouse", ApplicationProvider.GREENHOUSE),
        ("lever-job-application", ApplicationProvider.LEVER),
        ("workday", ApplicationProvider.WORKDAY),
        ("ashby", ApplicationProvider.ASHBY),
        ("smartrecruiters", ApplicationProvider.SMARTRECRUITERS),
    )
    return next((provider for marker, provider in hints if marker in content), detected)
