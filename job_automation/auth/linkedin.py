"""LinkedIn authentication-page detection (credentials are never handled here)."""

from __future__ import annotations

LOGIN_URL = "https://www.linkedin.com/login"
HOME_URL = "https://www.linkedin.com/jobs/"


async def is_authenticated(page: object) -> bool:
    url = str(getattr(page, "url", "")).casefold()
    if "linkedin.com" not in url or any(part in url for part in ("/login", "/checkpoint", "/authwall")):
        return False
    try:
        return await page.locator(  # type: ignore[attr-defined]
            "a[href*='/feed/'], a[href*='/jobs/'], button[aria-label*='Me']"
        ).count() > 0
    except Exception:
        return "/feed" in url or "/jobs" in url
