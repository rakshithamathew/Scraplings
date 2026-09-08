import json

import pytest
from sqlalchemy import inspect

from job_automation.database import JobRepository, initialize_database
from job_automation.outreach.contact_repository import ContactRepository
from job_automation.outreach.discovery import (
    AccessBlocked, ContactDiscoveryService, PublicContact, PublicContactScraper, role_priority,
)
from tests.job_automation.test_scraper import make_response


@pytest.fixture
def repository(tmp_path):
    engine = initialize_database(f"sqlite:///{(tmp_path / 'contacts.db').as_posix()}")
    yield JobRepository(engine)
    engine.dispose()


def job(repository, suffix='1'):
    return repository.create_job(title='Frontend Developer', company='Example Labs', source='linkedin',
        source_url='https://www.linkedin.com/jobs/view/' + suffix,
        application_url='https://www.linkedin.com/jobs/view/' + suffix)


def person(name='Robin Recruiter', title='Technical Recruiter', **extra):
    return {'@type': 'Person', 'name': name, 'jobTitle': title,
        'worksFor': {'@type': 'Organization', 'name': 'Example Labs', 'url': 'https://example.com'},
        'sameAs': 'https://www.linkedin.com/in/robin/?trk=test', **extra}


def page(url, nodes):
    return make_response(url, '<script type="application/ld+json">' + json.dumps(nodes) + '</script>')


@pytest.mark.parametrize('title,posted,context,count,expected', [
    ('Technical Recruiter', True, '', None, 1), ('Talent Acquisition Partner', False, '', None, 2),
    ('Technical Recruiter', False, '', None, 3), ('Hiring Manager', False, 'Frontend team', None, 4),
    ('Engineering Manager', False, 'Frontend team', None, 5), ('Head of Engineering', False, '', None, 6),
    ('CTO', False, 'Hiring frontend engineers', 20, 7), ('Founder', False, 'Frontend hiring', 300, None),
    ('Founder', False, 'Frontend hiring', None, None), ('Founder', False, '', 20, None),
    ('Sales Manager', False, 'Frontend team', None, None), ('Engineering Manager', False, 'Embedded firmware', None, None),
])
def test_priority_and_relevance(title, posted, context, count, expected):
    assert role_priority(title, context, 'Frontend Developer', posted_job=posted, employee_count=count) == expected


def test_public_person_evidence_and_email(repository):
    current = job(repository)
    scraper = PublicContactScraper(request_delay=0)
    url = 'https://example.com/team'
    nodes = [person(email='robin@example.com'), person(name='Wrong', worksFor={'name': 'Other'}),
             person(name='Unrelated', title='Account Executive'), person(name='Private', email='private@gmail.com')]
    contacts, _ = scraper.parse(current, page(url, nodes), url, official=True)
    saved = {c.name: c for c in contacts}
    assert set(saved) == {'Robin Recruiter', 'Private'}
    assert saved['Robin Recruiter'].work_email == 'robin@example.com'
    assert saved['Robin Recruiter'].linkedin_url == 'https://www.linkedin.com/in/robin'
    assert saved['Robin Recruiter'].source == url
    assert saved['Private'].work_email is None


def test_missing_email_never_inferred_and_poster_first(repository):
    current = job(repository)
    html = '''<div class="hirer-card"><h3 class="hirer-card__hirer-name">Robin</h3>
    <div class="hirer-card__hirer-job-title">Technical Recruiter</div>
    <a href="https://www.linkedin.com/in/robin">Profile</a></div>'''
    contacts, _ = PublicContactScraper().parse(current, make_response(current.source_url, html), current.source_url, job_page=True)
    assert len(contacts) == 1
    assert contacts[0].priority == 1
    assert contacts[0].work_email is None


@pytest.mark.asyncio
async def test_queue_company_pages_dedup_and_job_associations(repository):
    first, second = job(repository), job(repository, '2')
    contacts = ContactRepository(repository.engine)
    assert len(contacts.tasks()) == 2
    scraper = PublicContactScraper(request_delay=0)
    fetched = []
    async def fetch(url):
        fetched.append(url)
        if 'linkedin.com/jobs' in url:
            return page(url, {'@type': 'JobPosting', 'hiringOrganization': {'@type': 'Organization', 'name': 'Example Labs', 'url': 'https://example.com'}})
        return page(url, [person(email='robin@example.com'), person(email='robin@example.com')])
    scraper.fetch = fetch
    service = ContactDiscoveryService(repository, scraper=scraper)
    summary = await service.run()
    assert summary.jobs_checked == 2
    assert summary.contacts_saved == 2
    assert len(contacts.list(first.id)) == len(contacts.list(second.id)) == 1
    assert contacts.tasks() == []
    repeated = await service.run(first.id)
    assert repeated.contacts_saved == 0
    assert len(contacts.list(first.id)) == 1
    assert len(repository.get_jobs()) == 2
    assert {'job_contacts', 'contact_discovery_tasks'} <= set(inspect(repository.engine).get_table_names())


@pytest.mark.asyncio
async def test_block_persists_state_without_contact_guessing(repository):
    current = job(repository)
    scraper = PublicContactScraper(request_delay=0)
    calls = []
    async def fetch(url):
        calls.append(url)
        raise AccessBlocked('Sign in required')
    scraper.fetch = fetch
    service = ContactDiscoveryService(repository, scraper=scraper)
    summary = await service.run()
    assert summary.blocked == 1
    assert calls == [current.source_url]
    assert service.contacts.list(current.id) == []
    assert service.contacts.tasks(current.id)[0].status == 'BLOCKED'


def test_identity_enrichment_and_company_guard(repository):
    current = job(repository)
    repo = ContactRepository(repository.engine)
    contact = PublicContact(name='Robin', title='Technical Recruiter', company=current.company,
        source='https://example.com/team', evidence='Technical Recruiter at Example Labs', priority=3)
    assert repo.save(current.id, contact)
    assert not repo.save(current.id, contact.model_copy(update={'work_email': 'robin@example.com'}))
    assert repo.list(current.id)[0].work_email == 'robin@example.com'
    with pytest.raises(ValueError, match='company'):
        repo.save(current.id, contact.model_copy(update={'company': 'Other'}))

@pytest.mark.asyncio
@pytest.mark.parametrize('status,html', [(403, ''), (429, ''), (302, ''), (200, 'Verify you are human')])
async def test_fetch_stops_blocked_host_without_retry(monkeypatch, status, html):
    import socket
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs['retries'] == 1
        assert kwargs['follow_redirects'] is False
        assert kwargs['impersonate'] is None
        return make_response(url, html, status=status)
    monkeypatch.setattr('job_automation.outreach.discovery.AsyncFetcher.get', fetch)
    scraper = PublicContactScraper(request_delay=0)
    for url in ('https://example.com/team', 'https://example.com/about'):
        with pytest.raises(AccessBlocked):
            await scraper.fetch(url)
    assert len(calls) == 1


def test_delete_job_cleans_contact_associations(repository):
    current = job(repository)
    repo = ContactRepository(repository.engine)
    repo.save(current.id, PublicContact(name='Robin', title='Technical Recruiter', company=current.company,
        source='https://example.com/team', evidence='Technical Recruiter', priority=3))
    assert repository.delete_job(current.id)
    assert repo.list(current.id) == []
    assert repo.tasks() == []


def test_contact_api_lists_and_validates_job(repository):
    from fastapi.testclient import TestClient
    from job_automation.api.main import create_app
    current = job(repository)
    ContactRepository(repository.engine).save(current.id, PublicContact(name='Robin', title='Technical Recruiter',
        company=current.company, source='https://example.com/team', evidence='Technical Recruiter', priority=3))
    app = create_app(str(repository.engine.url))
    with TestClient(app) as client:
        response = client.get(f'/jobs/{current.id}/contacts')
        assert response.status_code == 200
        assert response.json()[0]['work_email'] is None
        assert response.json()[0]['job_id'] == current.id
        assert client.get('/jobs/99999/contacts').status_code == 404
        assert client.post('/jobs/99999/contacts/discover').status_code == 404
