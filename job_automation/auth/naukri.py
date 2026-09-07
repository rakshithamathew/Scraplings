"""Naukri authentication-page detection (credentials are never handled here)."""

from __future__ import annotations

LOGIN_URL = "https://www.naukri.com/"
HOME_URL = "https://www.naukri.com/mnjuser/homepage"


async def is_authenticated(page: object) -> bool:
    url = str(getattr(page, "url", "")).casefold()
    if "naukri.com" not in url or "login.naukri.com" in url:
        return False
    if "/mnjuser/" in url:
        return True
    try:
        return await page.locator(  # type: ignore[attr-defined]
            "[aria-label='Open profile menu'], a[href*='/mnjuser/']"
        ).count() > 0
    except Exception:
        return False
