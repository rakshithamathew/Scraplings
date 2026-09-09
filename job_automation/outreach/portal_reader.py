"""Read rendered job pages with saved authorization; never solve access checks."""
import asyncio
import sys
from urllib.parse import urlsplit

from scrapling.engines.toolbelt.custom import Response

from job_automation.auth import SessionManager
from job_automation.outreach.discovery import AccessBlocked


class PortalContactReader:
    def __init__(self, manager=None):
        self.manager = manager or SessionManager()

    def platform(self, url):
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        for platform, prefix in [('naukri', '/job-listings-'), ('linkedin', '/jobs/view/')]:
            if (host == platform + '.com' or host.endswith('.' + platform + '.com')) and parsed.path.startswith(prefix):
                return platform
        return None

    def available(self, url):
        platform = self.platform(url)
        return bool(platform and self.manager.is_connected(platform))

    async def read(self, url, timeout):
        if sys.platform == 'win32' and isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop):
            return await asyncio.to_thread(lambda: asyncio.run(self._read(url, timeout)))
        return await self._read(url, timeout)

    async def _read(self, url, timeout):
        from playwright.async_api import async_playwright
        platform = self.platform(url)
        if not platform or not self.manager.is_connected(platform):
            raise AccessBlocked('Saved portal authorization is unavailable')
        async with async_playwright() as playwright:
            try:
                context = await playwright.chromium.launch_persistent_context(
                    str(self.manager.profile_directory(platform)), channel=self.manager.browser_channel,
                    headless=True)
            except Exception:
                raise RuntimeError('Saved browser profile is unavailable or in use; it was not copied or reset') from None
            try:
                page = await context.new_page()
                response = await page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
                if response and response.status in {401, 403, 429, 999}:
                    raise AccessBlocked(f'{platform} returned HTTP {response.status}; no alternate transport retry')
                if self.platform(page.url) != platform or urlsplit(page.url).path.rstrip('/') != urlsplit(url).path.rstrip('/'):
                    raise AccessBlocked('Portal redirected away from the requested job; login or access approval may be required')
                if response and response.status >= 400:
                    raise RuntimeError(f'Job page returned HTTP {response.status}')
                await page.wait_for_timeout(2000)
                challenge = page.locator('input[type="password"]:visible, input[autocomplete="one-time-code"]:visible, iframe[src*="captcha"]:visible')
                body = (await page.locator('body').inner_text()).casefold()
                if await challenge.count() or any(marker in body for marker in (
                    'access denied', 'captcha', 'security verification', 'verify your identity',
                    'verify you are human', 'enter the verification code', 'sign in to view')):
                    raise AccessBlocked('Portal requires login, CAPTCHA, OTP or access verification')
                return Response(url=page.url, content=await page.content(), status=response.status if response else 200,
                    reason='Rendered authorized job page', cookies={}, headers={}, request_headers={})
            finally:
                await context.close()
