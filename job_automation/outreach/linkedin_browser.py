"""Visible LinkedIn UI actions using the project's saved authorized Chrome profile."""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlsplit

from scrapling.parser import Selector
from job_automation.outreach.discovery import PublicContact, linkedin_url
from job_automation.outreach.personalized import clean


class LinkedInCheckpoint(RuntimeError):
    pass


class LinkedInUnavailable(RuntimeError):
    pass


def profile_url(value):
    return linkedin_url(urljoin('https://www.linkedin.com', value)) if isinstance(value, str) else None


@dataclass
class JobPageDetails:
    description: str | None = None
    poster: PublicContact | None = None


def job_url(value):
    try:
        parsed = urlsplit(value or '')
        host = parsed.hostname or ''
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in {None, 443}:
            return None
        if host != 'linkedin.com' and not host.endswith('.linkedin.com'):
            return None
        match = re.fullmatch(r'/jobs/view/(?:[^/]*-)?(\d+)/?', parsed.path)
        return f'https://www.linkedin.com/jobs/view/{match.group(1)}' if match else None
    except ValueError:
        return None


class LinkedInMessagingBrowser:
    def __init__(self, manager, *, headless=True):
        self.manager, self.headless = manager, headless
        self.playwright = self.context = self.page = self.composer = None
        self.recipient_url = None

    async def start(self):
        if not self.manager.is_connected('linkedin'):
            raise LinkedInCheckpoint('Saved LinkedIn authentication is unavailable; complete normal authorization')
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        options = {'user_data_dir': str(self.manager.profile_directory('linkedin')), 'headless': self.headless}
        if self.manager.browser_channel:
            options['channel'] = self.manager.browser_channel
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(**options)
            self.page = await self.context.new_page()
            self.page.set_default_timeout(5000)
        except Exception:
            await self.close()
            raise LinkedInUnavailable('Saved browser profile is unavailable or in use; no profile was copied or reset') from None

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.playwright:
                await self.playwright.stop()
            self.context = self.playwright = self.page = None

    async def guard(self):
        parsed = urlsplit(self.page.url)
        host = parsed.hostname or ''
        if host != 'linkedin.com' and not host.endswith('.linkedin.com'):
            raise LinkedInUnavailable('Navigation left LinkedIn')
        if any(marker in parsed.path.casefold() for marker in ('/login', '/checkpoint', '/authwall', '/challenge', '/uas/', '/signup')):
            raise LinkedInCheckpoint('LinkedIn requires login or account verification')
        challenge = self.page.locator('input[type="password"]:visible, input[autocomplete="one-time-code"]:visible, input[name="pin"]:visible, iframe[src*="captcha"]:visible, iframe[title*="CAPTCHA"]:visible')
        if await challenge.count():
            raise LinkedInCheckpoint('LinkedIn requires login, CAPTCHA, or OTP')
        body = (await self.page.locator('body').inner_text()).casefold()
        if any(marker in body for marker in ('security verification', 'verify your identity', 'verify you are human',
            'enter the verification code', 'account temporarily restricted', 'account has been restricted', 'unusual activity', 'complete the captcha')):
            raise LinkedInCheckpoint('LinkedIn account security verification is required')
        authenticated = self.page.locator('#global-nav:visible, .global-nav__me:visible, button[aria-label="Me"]:visible')
        if not await authenticated.count():
            raise LinkedInCheckpoint('Authenticated LinkedIn navigation could not be verified')

    async def navigate(self, url):
        response = await self.page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response and response.status in {401, 403, 429, 999}:
            raise LinkedInCheckpoint(f'LinkedIn access limited (HTTP {response.status})')
        if response and response.status >= 400:
            raise LinkedInUnavailable(f'LinkedIn page unavailable (HTTP {response.status})')
        # Wait only for normal rendered account navigation, never solve challenges.
        try:
            await self.page.locator('#global-nav, .global-nav__me, input[type="password"], input[autocomplete="one-time-code"]').first.wait_for(state='visible', timeout=5000)
        except Exception:
            pass
        await self.guard()

    @staticmethod
    def first_text(page, selectors):
        for selector in selectors:
            nodes = page.css(selector)
            if nodes:
                value = clean(nodes[0].get_all_text(separator=' ', strip=True))
                if value:
                    return value
        return None

    async def open_job(self, url, company):
        canonical = job_url(url)
        if not canonical:
            raise LinkedInUnavailable('No canonical LinkedIn job URL is saved')
        await self.navigate(canonical)
        if job_url(self.page.url) != canonical:
            raise LinkedInUnavailable('LinkedIn did not open the expected job')
        description_root = self.page.locator('#job-details, .jobs-description__content, .show-more-less-html__markup').first
        more = description_root.get_by_role('button', name=re.compile(r'^(?:Show|See) more', re.I))
        if await more.count() == 1 and await more.is_visible() and await more.is_enabled():
            await self.guard()
            await more.click()
            await self.guard()
        parsed = Selector(await self.page.content())
        observed_company = self.first_text(parsed, ('.job-details-jobs-unified-top-card__company-name', 'a.topcard__org-name-link'))
        if not observed_company or clean(observed_company).casefold() != clean(company).casefold():
            raise LinkedInUnavailable('Job company could not be verified on the opened page')
        description = self.first_text(parsed, ('#job-details', '.jobs-description__content .jobs-box__html-content', '.show-more-less-html__markup'))
        posters = {}
        for card in parsed.css('.hirer-card, .jobs-poster, .job-details-jobs-unified-top-card__job-poster'):
            profiles = {profile for href in card.css('a::attr(href)').getall() if (profile := profile_url(href))}
            if len(profiles) != 1:
                continue
            name = self.first_text(card, ('.hirer-card__hirer-name', '.jobs-poster__name', 'h3', 'h4'))
            title = self.first_text(card, ('.hirer-card__hirer-job-title', '.jobs-poster__headline')) or 'Job poster'
            if name:
                profile = profiles.pop()
                posters[profile] = PublicContact(name=name, title=title, company=company, linkedin_url=profile,
                    source=canonical, priority=1, evidence='Shown as the job poster on the authenticated job page')
        if len(posters) > 1:
            raise LinkedInUnavailable('More than one job poster is shown; recipient is ambiguous')
        return JobPageDetails(description, next(iter(posters.values()), None))

    async def open_profile(self, contact):
        expected = linkedin_url(contact.linkedin_url)
        if not expected:
            raise LinkedInUnavailable('Contact has no canonical LinkedIn profile')
        await self.navigate(expected)
        if linkedin_url(self.page.url) != expected:
            raise LinkedInUnavailable('Profile URL does not match the selected contact')
        headings = self.page.locator('main h1:visible')
        if await headings.count() != 1 or clean(await headings.inner_text()).casefold() != clean(contact.name).casefold():
            raise LinkedInUnavailable('Profile name does not match the selected contact')
        top = headings.locator('xpath=ancestor::section[1]')
        controls = top.get_by_role('button', name=re.compile(r'^Message(?: ' + re.escape(contact.name) + r')?$', re.I))
        if await controls.count() == 0:
            controls = top.get_by_role('link', name=re.compile(r'^Message(?: ' + re.escape(contact.name) + r')?$', re.I))
        if await controls.count() != 1 or not await controls.is_visible() or not await controls.is_enabled():
            raise LinkedInUnavailable('Messaging is not available to this account for the selected profile')
        await self.guard()
        await controls.click()
        await self.guard()
        self.recipient_url = expected
        await self.page.locator('.msg-overlay-conversation-bubble:visible, .msg-convo-wrapper:visible').first.wait_for(state='visible', timeout=5000)
        roots = self.page.locator('.msg-overlay-conversation-bubble:visible, .msg-convo-wrapper:visible')
        matches = []
        for index in range(await roots.count()):
            root = roots.nth(index)
            headers = root.locator('.msg-overlay-bubble-header a[href*="/in/"], .msg-thread__link-to-profile, .msg-entity-lockup__entity-title[href*="/in/"]')
            profiles = {profile_url(await headers.nth(n).get_attribute('href')) for n in range(await headers.count())}
            if profiles == {expected}:
                matches.append(root)
        if len(matches) != 1:
            raise LinkedInUnavailable('A single-recipient conversation could not be verified')
        self.composer = matches[0]

    async def check_recipient(self):
        await self.guard()
        headers = self.composer.locator('.msg-overlay-bubble-header a[href*="/in/"], .msg-thread__link-to-profile, .msg-entity-lockup__entity-title[href*="/in/"]')
        profiles = {profile_url(await headers.nth(n).get_attribute('href')) for n in range(await headers.count())}
        if profiles != {self.recipient_url}:
            raise LinkedInUnavailable('Conversation recipient changed or includes multiple people')

    def editor(self):
        return self.composer.locator('[contenteditable="true"][role="textbox"]:visible, .msg-form__contenteditable[contenteditable="true"]:visible')

    def own_messages(self):
        return self.composer.locator('.msg-s-event-listitem--self .msg-s-event-listitem__body')

    async def prepare_message(self, message):
        await self.check_recipient()
        if await self.composer.locator('input[name="subject"]:visible, input[placeholder="Subject"]:visible').count():
            raise LinkedInUnavailable('This messaging flow requires an InMail subject or additional account capability')
        own = await self.own_messages().all_text_contents()
        if any(clean(value) == clean(message) for value in own):
            return True
        editor = self.editor()
        if await editor.count() != 1 or not await editor.is_editable():
            raise LinkedInUnavailable('No permitted message editor is available')
        existing = clean(await editor.inner_text())
        if existing and existing != clean(message):
            raise LinkedInUnavailable('An unrelated message draft is already in the composer')
        if not existing:
            await editor.fill(message)
        await self.check_recipient()
        send = self.composer.get_by_role('button', name='Send', exact=True)
        if await send.count() != 1 or not await send.is_enabled():
            raise LinkedInUnavailable('Send is unavailable; no connection request or upgrade will be attempted')
        return False

    async def send_prepared(self, message):
        await self.check_recipient()
        if clean(await self.editor().inner_text()) != clean(message):
            raise LinkedInUnavailable('Message changed before dispatch')
        before = len(await self.own_messages().all_text_contents())
        # Exactly one click, only after the service commits its durable send claim.
        await self.composer.get_by_role('button', name='Send', exact=True).click()
        for _ in range(40):
            await self.check_recipient()
            if await self.composer.locator('.msg-s-event-listitem__error:visible, .msg-form__error:visible').count():
                raise RuntimeError('LinkedIn reported a messaging error; automatic retry is disabled')
            contents = await self.own_messages().all_text_contents()
            if len(contents) > before and clean(contents[-1]) == clean(message) and not clean(await self.editor().inner_text()):
                return 'Exact outgoing message appeared in the verified conversation'
            await self.page.wait_for_timeout(250)
        raise RuntimeError('Message delivery was not confirmed; automatic retry is disabled')
