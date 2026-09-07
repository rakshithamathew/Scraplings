"""Indeed authentication-page detection (credentials are never handled here)."""

from __future__ import annotations

LOGIN_URL = "https://secure.indeed.com/auth"
HOME_URL = "https://www.indeed.com/myjobs"


async def is_authenticated(page: object) -> bool:
    url = str(getattr(page, "url", "")).casefold()
    if "indeed." not in url or "secure.indeed.com/auth" in url:
        return False
    try:
        signed_in = await page.locator(  # type: ignore[attr-defined]
            "a[href*='/myjobs'], a[href*='/account'], button[aria-label*='Account']"
        ).count()
        signed_out = await page.locator("a[href*='auth'], button:has-text('Sign in')").count()  # type: ignore[attr-defined]
        return signed_in > 0 and signed_out == 0
    except Exception:
        return "/myjobs" in url
