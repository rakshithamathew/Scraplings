"""Bounded public contact discovery with saved-session job rendering and no email inference."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import re
import socket
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, Field, field_validator
from scrapling.fetchers import AsyncFetcher
from scrapling.parser import Selector

from job_automation.database import DEFAULT_DATABASE_URL, JobRepository, initialize_database
from job_automation.database.repository import normalize_identity_text
from job_automation.outreach.contact_repository import ContactRepository

LOGGER = logging.getLogger(__name__)
PERSONAL_EMAIL = {'gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com', 'live.com', 'icloud.com', 'proton.me', 'protonmail.com', 'aol.com'}


def public_url(value, base=''):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(urljoin(base, value))
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.port not in {None, 80, 443} or parsed.hostname in {'localhost', 'localhost.localdomain'}:
            return None
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:
                return None
        except ValueError:
            pass
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/'), parsed.query, ''))
    except ValueError:
        return None


def linkedin_url(value):
    url = public_url(value)
    if not url:
        return None
    parts = urlsplit(url)
    if (parts.hostname == 'linkedin.com' or parts.hostname.endswith('.linkedin.com')) and re.fullmatch(r'/in/[^/]+', parts.path):
        return 'https://www.linkedin.com' + parts.path
    return None


class PublicContact(BaseModel):
    name: str = Field(min_length=1)
    title: str = Field(min_length=1)
    company: str = Field(min_length=1)
    linkedin_url: str | None = None
    work_email: str | None = None
    source: str
    priority: int = Field(ge=1, le=7)
    evidence: str = Field(min_length=1)

    @field_validator('linkedin_url')
    @classmethod
    def clean_linkedin(cls, value):
        if value is not None and not linkedin_url(value):
            raise ValueError('Expected a public LinkedIn person URL')
        return linkedin_url(value)

    @field_validator('source')
    @classmethod
    def clean_source(cls, value):
        result = public_url(value)
        if not result:
            raise ValueError('A public evidence URL is required')
        return result

    @field_validator('work_email')
    @classmethod
    def clean_email(cls, value):
        if value is None:
            return None
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value) or value.rsplit('@', 1)[1].lower() in PERSONAL_EMAIL:
            raise ValueError('Expected a published work email')
        return value.lower()


def role_priority(title, context, job_title, *, posted_job=False, employee_count=None):
    title = normalize_identity_text(title)
    recruiter = bool(re.search(r'\b(?:recruiter|recruiting)\b', title))
    if posted_job and (recruiter or 'talent acquisition' in title or title in {'job poster', 'hr', 'hr recruiter', 'human resources'}):
        return 1
    if recruiter and 'technical' not in title:
        return 2
    if 'talent acquisition' in title:
        return 2
    if 'technical recruiter' in title or 'technical recruiting' in title:
        return 3
    # An engineering/hiring manager must have evidence of responsibility for this role/team.
    tokens = set(re.findall(r'\w+', job_title.lower())) - {'senior', 'junior', 'developer', 'engineer', 'engineering', 'lead', 'manager', 'full', 'stack'}
    relevant = posted_job or bool(tokens & set(re.findall(r'\w+', context.lower())))
    if 'hiring manager' in title and relevant:
        return 4
    if 'engineering manager' in title and relevant:
        return 5
    if 'head of engineering' in title:
        return 6
    if (re.search(r'\b(?:cto|founder)\b', title) or 'chief technology officer' in title) and employee_count is not None and 0 < employee_count <= 50 and relevant:
        return 7
    return None


def text(node, selector):
    return ' '.join(node.css(selector).getall()).strip()


def structured_nodes(page):
    for script in page.css('script[type="application/ld+json"]::text').getall():
        try:
            pending = [json.loads(script)]
        except (ValueError, TypeError):
            continue
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
            elif isinstance(node, dict):
                yield node
                pending.extend(value for value in node.values() if isinstance(value, (dict, list)))


class AccessBlocked(RuntimeError):
    pass


class PublicContactScraper:
    def __init__(self, *, request_delay=2, max_pages=6, timeout=25, company_sources=None, portal_reader=None):
        self.request_delay, self.max_pages, self.timeout = request_delay, max_pages, timeout
        self.cache = {}
        self.blocked_hosts = set()
        self.portal_reader = portal_reader
        if company_sources is None:
            path = Path(__file__).resolve().parents[2] / 'config' / 'company_contact_sources.json'
            company_sources = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        self.company_sources = {normalize_identity_text(name): urls for name, urls in company_sources.items()}

    async def fetch(self, url):
        if url in self.cache:
            return self.cache[url]
        host = urlsplit(url).hostname
        if not public_url(url):
            raise ValueError('Not a public URL')
        if host in self.blocked_hosts:
            raise AccessBlocked('Host previously required authorization or verification')
        # Refuse private network destinations, including hostnames resolving to them.
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise ValueError('Non-public network destination')
        await asyncio.sleep(self.request_delay)
        if self.portal_reader and self.portal_reader.available(url):
            try:
                page = await self.portal_reader.read(url, self.timeout)
            except AccessBlocked:
                self.blocked_hosts.add(host)
                raise
            self.cache[url] = page
            return page
        page = await asyncio.wait_for(AsyncFetcher.get(
            url, timeout=self.timeout, retries=1, follow_redirects=False,
            impersonate=None, stealthy_headers=False,
        ), timeout=self.timeout + 1)
        body = page.get_all_text(separator=' ', strip=True).lower()
        if page.status in {401, 403, 429, 999} or 300 <= page.status < 400 or any(
            marker in body for marker in ('captcha', 'security verification', 'sign in to view', 'authwall', 'verify you are human')
        ) or any(marker in urlsplit(page.url).path for marker in ('/login', '/checkpoint', '/authwall', '/challenge')):
            self.blocked_hosts.add(host)
            raise AccessBlocked('Public page requires sign-in, verification, or access approval')
        if page.status >= 400:
            raise RuntimeError(f'Public page returned HTTP {page.status}')
        self.cache[url] = page
        return page

    def parse(self, job, page, url, *, official=False, job_page=False, employee_count=None):
        contacts = []
        nodes = list(structured_nodes(page))
        company_key = normalize_identity_text(job.company)
        for node in nodes:
            if node.get('@type') == 'Organization' and normalize_identity_text(str(node.get('name', ''))) == company_key:
                count = node.get('numberOfEmployees')
                if isinstance(count, dict):
                    count = count.get('maxValue', count.get('value'))
                if isinstance(count, int) and not isinstance(count, bool):
                    employee_count = count

        def add(name, title, company, links, email, context, posted=False, company_url=None):
            if not name or not title or normalize_identity_text(company) != company_key:
                return
            priority = role_priority(title, context, job.title, posted_job=posted, employee_count=employee_count)
            if priority is None:
                return
            profile = next((linkedin_url(link) for link in links if linkedin_url(link)), None)
            # Only accept an address literally published for this person on the
            # employer's site, or matching their explicitly published employer URL.
            work_email = None
            if isinstance(email, str):
                email = unquote(email.removeprefix('mailto:').split('?')[0]).strip()
                domain = email.rsplit('@', 1)[-1].lower()
                employer_host = urlsplit(company_url or '').hostname
                if official or posted or (employer_host and domain == employer_host.removeprefix('www.')):
                    if re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email) and domain not in PERSONAL_EMAIL:
                        work_email = email
            contacts.append(PublicContact(name=name.strip(), title=title.strip(), company=job.company,
                linkedin_url=profile, work_email=work_email, source=url, priority=priority,
                evidence=('Job poster. ' if posted else '') + context.strip()))

        for node in nodes:
            if node.get('@type') != 'Person':
                continue
            employer = node.get('worksFor', {})
            if not isinstance(employer, dict):
                continue
            links = node.get('sameAs', [])
            if isinstance(links, str):
                links = [links]
            if not isinstance(links, list):
                links = []
            links = links + [node.get('url')]
            name, title = node.get('name'), node.get('jobTitle')
            if isinstance(name, str) and isinstance(title, str):
                context = title + '. ' + str(node.get('description') or '')
                add(name, title, str(employer.get('name', '')), links, node.get('email'), context, company_url=public_url(employer.get('url')))

        if job_page:
            for card in page.css('.hirer-card, .jobs-poster, .recruiter-info, .recruiter-details, [data-testid="recruiter-card"]'):
                name = text(card, '.hirer-card__hirer-name::text, .hirer-card__hirer-name ::text, .jobs-poster__name::text, .recruiter-name::text, [itemprop="name"]::text')
                title = text(card, '.hirer-card__hirer-job-title::text, .jobs-poster__headline::text, .recruiter-designation::text, [itemprop="jobTitle"]::text') or 'Job poster'
                emails = set(re.findall(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', card.get_all_text(separator=' ', strip=True)))
                emails.update(unquote(link[7:].split('?')[0]) for link in card.css('a[href^="mailto:"]::attr(href)').getall())
                add(name, title, job.company, [urljoin(url, link) for link in card.css('a::attr(href)').getall()], next(iter(emails)) if len(emails) == 1 else None,
                    text(card, '::text, *::text') or title, posted=True)
        # Shared hiring inboxes are contacts, not invented people. Only use
        # addresses explicitly published in recruiting content, never site footers.
        if official or job_page:
            scopes = list(page.css('#job-details, .jobs-description__content, .show-more-less-html__markup, [class*="JDC__dang-inner-html"], .job-desc, [itemprop="description"]')) if job_page else list(page.css('main, article'))
            if job_page:
                for node in nodes:
                    if node.get('@type') == 'JobPosting' and isinstance(node.get('description'), str):
                        employer = node.get('hiringOrganization') or {}
                        if isinstance(employer, dict) and normalize_identity_text(str(employer.get('name', ''))) == company_key:
                            scopes.append(Selector(node['description']))
            if official and not scopes:
                scopes = [page]
            for scope in scopes:
                content = scope.get_all_text(separator=' ', strip=True)
                for match in re.finditer(r'[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}', content):
                    email = match.group().lower()
                    local, domain = email.rsplit('@', 1)
                    if domain in PERSONAL_EMAIL or not re.fullmatch(r'(?:hr|careers?|jobs|recruitment|recruiting|talent)(?:[._-][a-z]+)?', local):
                        continue
                    context = content[max(0, match.start()-180):match.end()+120]
                    if not re.search(r'\b(?:apply|resume|cv|candidates|hiring|recruitment|careers)\b', context, re.I):
                        continue
                    if official and domain != (urlsplit(url).hostname or '').removeprefix('www.'):
                        continue
                    contacts.append(PublicContact(name='Recruitment team', title='Public recruitment mailbox',
                        company=job.company, work_email=email, source=url, priority=7,
                        evidence='Shared hiring inbox, not a named person. ' + context.strip()))
        if official:
            for card in page.css('.team-member, .person-card, [itemtype="https://schema.org/Person"]'):
                name = text(card, '[itemprop="name"]::text, .name::text, h3::text')
                title = text(card, '[itemprop="jobTitle"]::text, .title::text, .role::text')
                emails = card.css('a[href^="mailto:"]::attr(href)').getall()
                add(name, title, job.company, card.css('a::attr(href)').getall(), emails[0] if len(emails) == 1 else None,
                    text(card, '::text, *::text') or title)
        return sorted(contacts, key=lambda contact: contact.priority), employee_count

    async def discover(self, job):
        seed = public_url(job.source_url or job.application_url)
        if not seed:
            return [], 'FAILED', 'Job has no public source URL'
        pending = [(seed, False, True)]
        application = public_url(job.application_url)
        if application and application != seed:
            pending.append((application, False, True))
        for company_url in self.company_sources.get(normalize_identity_text(job.company), []):
            if public_url(company_url):
                pending.append((public_url(company_url), True, False))
        visited, contacts, official_hosts = set(), [], set()
        blocked, failed = [], []
        employee_count = None
        while pending and len(visited) < self.max_pages:
            url, official, job_page = pending.pop(0)
            if url in visited:
                continue
            visited.add(url)
            try:
                page = await self.fetch(url)
            except AccessBlocked as error:
                blocked.append(f'{urlsplit(url).hostname}: {error}')
                continue
            except Exception as error:
                failed.append(f'{urlsplit(url).hostname}: {type(error).__name__}: {error}')
                continue
            host = urlsplit(url).hostname or ''
            if job_page and (host.endswith('naukri.com') or host.endswith('linkedin.com')):
                has_job = any(node.get('@type') == 'JobPosting' for node in structured_nodes(page)) or bool(page.css(
                    '#job-details, .jobs-description__content, .show-more-less-html__markup, [class*="JDC__dang-inner-html"], .job-desc, .hirer-card, .recruiter-info, .recruiter-details, [data-testid="recruiter-card"]'))
                if not has_job:
                    failed.append(f'{host}: No readable job description or recruiter content; empty JavaScript page or unsupported layout')
                    continue
            if official:
                official_hosts.add(host)
            found, employee_count = self.parse(job, page, url, official=official, job_page=job_page, employee_count=employee_count)
            contacts.extend(found)
            # Follow only company URLs explicitly identified by the job publisher.
            for node in structured_nodes(page):
                if node.get('@type') == 'Organization' and normalize_identity_text(str(node.get('name', ''))) == normalize_identity_text(job.company):
                    employer_url = public_url(node.get('url'), url)
                    if employer_url:
                        host = urlsplit(employer_url).hostname
                        is_linkedin = host == 'linkedin.com' or host.endswith('.linkedin.com')
                        if not is_linkedin:
                            official_hosts.add(host)
                        pending.append((employer_url, not is_linkedin, False))
            if job_page:
                for href in page.css('a.topcard__org-name-link::attr(href)').getall():
                    company_url = public_url(href, url)
                    if company_url:
                        pending.append((company_url, False, False))
            if official:
                for link in page.css('a[href]'):
                    href = public_url(link.attrib.get('href'), url)
                    label = link.get_all_text(separator=' ', strip=True).lower()
                    if href and urlsplit(href).hostname in official_hosts and re.search(r'\b(?:team|leadership|about|careers|people)\b', label):
                        pending.append((href, True, False))
        email_count = len({contact.work_email for contact in contacts if contact.work_email})
        detail = f'{len(visited)} public pages checked; {len(contacts)} relevant records; {email_count} published work emails.'
        detail += ' ' + '; '.join(blocked + failed)
        if not contacts and not blocked and not failed:
            detail += ' No relevant public contact was published in the supported page content.'
        return contacts, 'BLOCKED' if blocked else 'FAILED' if failed else 'COMPLETE', detail.strip()


@dataclass
class ContactDiscoverySummary:
    jobs_checked: int = 0
    contacts_saved: int = 0
    duplicates: int = 0
    blocked: int = 0
    failed: int = 0


class ContactDiscoveryService:
    def __init__(self, repository, *, scraper=None):
        self.jobs = repository
        self.contacts = ContactRepository(repository.engine)
        if scraper is None:
            from job_automation.outreach.portal_reader import PortalContactReader
            scraper = PublicContactScraper(portal_reader=PortalContactReader())
        self.scraper = scraper

    async def run(self, job_id=None):
        summary = ContactDiscoverySummary()
        for task in self.contacts.tasks(job_id):
            job = self.jobs.get_job(task.job_id)
            if job is None:
                continue
            summary.jobs_checked += 1
            try:
                contacts, status, detail = await self.scraper.discover(job)
                for contact in sorted(contacts, key=lambda item: item.priority):
                    if self.contacts.save(job.id, contact):
                        summary.contacts_saved += 1
                    else:
                        summary.duplicates += 1
                self.contacts.finish(job.id, status, detail)
                summary.blocked += status == 'BLOCKED'
                summary.failed += status == 'FAILED'
            except Exception:
                LOGGER.exception('Contact discovery failed for job %s', job.id)
                self.contacts.finish(job.id, 'FAILED', 'Contact discovery or persistence failed')
                summary.failed += 1
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=DEFAULT_DATABASE_URL)
    parser.add_argument('--job-id', type=int, help='Explicitly run or retry one job; otherwise process pending tasks')
    parser.add_argument('--list', action='store_true', help='List saved contacts for --job-id without fetching')
    args = parser.parse_args()
    if args.list and args.job_id is None:
        parser.error('--list requires --job-id')
    engine = initialize_database(args.database_url)
    try:
        if args.list:
            contacts = ContactRepository(engine).list(args.job_id)
            print(json.dumps([{key: getattr(contact, key) for key in ('job_id', 'name', 'title', 'company', 'linkedin_url', 'work_email', 'source', 'priority', 'evidence')} for contact in contacts], indent=2))
            return 0
        summary = asyncio.run(ContactDiscoveryService(JobRepository(engine)).run(args.job_id))
        from dataclasses import asdict
        print(json.dumps(asdict(summary), indent=2))
        return int(bool(summary.blocked or summary.failed))
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
