"""Open the dedicated application browser profile for one-time portal login."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from job_automation.applications.service import PROJECT_ROOT, load_application_settings


PORTALS = {
    "linkedin": "https://www.linkedin.com/login",
    "naukri": "https://www.naukri.com/",
}
LOGGER = logging.getLogger(__name__)


async def authenticate(portals: tuple[str, ...]) -> None:
    """Open selected portals and persist normal browser authentication state."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise RuntimeError("Playwright is not installed in the active environment") from error

    settings = load_application_settings()
    raw_profile = Path(settings.browser_profile_directory)
    profile = (raw_profile if raw_profile.is_absolute() else PROJECT_ROOT / raw_profile).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    playwright = await async_playwright().start()
    launch_options: dict[str, object] = {
        "user_data_dir": str(profile),
        "headless": False,
    }
    if settings.browser_channel:
        launch_options["channel"] = settings.browser_channel
    context = await playwright.chromium.launch_persistent_context(**launch_options)
    try:
        pages = context.pages
        for index, portal in enumerate(portals):
            page = pages[0] if index == 0 and pages else await context.new_page()
            try:
                await page.goto(PORTALS[portal], wait_until="domcontentloaded", timeout=60_000)
            except Exception as error:
                # A temporary portal/network failure must not destroy the login
                # browser. The user can retry or type the URL in the visible tab.
                LOGGER.warning("Unable to open %s automatically: %s", portal, error)
        print("Sign in to each portal in the visible browser. Do not share passwords or OTPs.", flush=True)
        print(
            "Close the browser when finished; its authenticated session will be reused by Auto Apply.",
            flush=True,
        )
        while context.pages:
            await asyncio.sleep(1)
    except Exception as error:
        # Closing the browser normally terminates the persistent context.
        if "closed" not in str(error).casefold():
            raise
    finally:
        try:
            await context.close()
        except Exception:
            pass
        await playwright.stop()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "portals",
        nargs="*",
        choices=tuple(PORTALS),
        default=None,
        help="Portals to open (default: linkedin and naukri)",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    asyncio.run(authenticate(tuple(args.portals or PORTALS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
