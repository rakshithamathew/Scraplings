"""Browser smoke test against built React assets and intercepted API fixtures."""
from pathlib import Path
from urllib.parse import urlsplit
import mimetypes

import pytest

DIST = Path(__file__).resolve().parents[2] / 'job_automation/frontend/dist'


@pytest.mark.asyncio
@pytest.mark.skipif(not (DIST / 'index.html').exists(), reason='Build the React frontend before this browser test')
async def test_single_screen_discovery_filters_draft_send(tmp_path):
    from playwright.async_api import async_playwright, expect
    job = {'id': 1, 'title': 'React Engineer', 'company': 'Example', 'location': 'Bengaluru',
        'status': 'APPLIED', 'match_score': 92, 'application_url': 'https://example.test/jobs/1',
        'applied_at': '2026-09-08T05:00:00Z'}
    contact = {'job_id': 1, 'contact_id': None, 'contact_name': None, 'contact_role': None,
        'public_work_email': None, 'linkedin_url': None, 'email_status': 'NOT_FOUND',
        'linkedin_status': 'NOT_FOUND', 'application_confirmed': True, 'email_attempted': False}
    posts = []
    async def route(request):
        parsed = urlsplit(request.request.url)
        path = parsed.path
        if parsed.hostname == 'dashboard.test':
            file = (DIST / (path.lstrip('/') or 'index.html')).resolve()
            if not file.is_relative_to(DIST.resolve()) or not file.is_file():
                await request.fulfill(status=404)
                return
            await request.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')
            return
        if request.request.method == 'OPTIONS':
            await request.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*',
                'Access-Control-Allow-Methods': 'GET, POST', 'Access-Control-Allow-Headers': 'Content-Type'})
            return
        data = {}
        if request.request.method == 'POST':
            posts.append(path)
            if path.endswith('/contacts/discover'):
                contact.update(contact_id=1, contact_name='Riley Recruiter', contact_role='Talent Acquisition',
                    public_work_email='riley@example.test', linkedin_url='https://www.linkedin.com/in/riley',
                    email_status='FOUND', linkedin_status='FOUND')
                data = {'contacts_saved': 1, 'duplicates': 0, 'blocked': 0, 'failed': 0}
            elif path.endswith('/outreach/generate'):
                contact['email_status'] = 'DRAFTED'
            elif path.endswith('/send-email'):
                contact.update(email_status='SENT', email_attempted=True)
                data = {'sent': 1}
        elif path == '/jobs':
            data = [job]
        elif path == '/stats':
            data = {'total_jobs': 1, 'qualified': 0, 'applied': 1}
        elif path == '/resume':
            data = {'filename': 'active-cv.pdf'}
        elif path == '/connections':
            data = []
        elif path == '/dashboard/outreach':
            data = {'jobs': [contact], 'contacts_found': int(bool(contact['contact_id'])),
                'emails_sent': int(contact['email_status'] == 'SENT'), 'linkedin_messages_sent': 0}
        elif path.endswith('/outreach'):
            data = {'status': 'DRAFTED', 'email_subject': 'React Engineer at Example',
                'email_body': 'Personalized draft from the uploaded CV.'}
        await request.fulfill(json=data, headers={'Access-Control-Allow-Origin': '*'})
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1500, 'height': 900})
        await page.route('**/*', route)
        try:
            await page.goto('http://dashboard.test')
            await expect(page.get_by_role('cell', name='React Engineer', exact=True)).to_be_visible()
            for heading in ['Recruiter / Contact', 'Role', 'Email', 'LinkedIn', 'Email Status', 'LinkedIn Status', 'Applied Date']:
                await expect(page.get_by_role('columnheader', name=heading, exact=True)).to_have_count(1)
            await page.get_by_role('button', name='Find Contact', exact=True).click()
            await expect(page.get_by_role('cell', name='riley@example.test', exact=True)).to_be_visible()
            await page.get_by_role('button', name='EMAIL FOUND', exact=True).click()
            await expect(page.get_by_role('cell', name='Riley Recruiter')).to_be_visible()
            await page.get_by_role('button', name='Draft Email', exact=True).click()
            await expect(page.get_by_role('region', name='Email draft')).to_contain_text('Personalized draft')
            await page.get_by_role('button', name='Close draft').click()
            await page.get_by_role('button', name='Send Email', exact=True).click()
            await expect(page.get_by_role('button', name='Send Email', exact=True)).to_be_disabled()
            await page.get_by_role('button', name='EMAIL SENT', exact=True).click()
            await expect(page.get_by_role('cell', name='SENT', exact=True)).to_be_visible()
            assert posts.count('/dashboard/jobs/1/send-email') == 1
            await expect(page.get_by_role('link', name='Open LinkedIn')).to_have_attribute('href', 'https://www.linkedin.com/in/riley')
            await page.screenshot(path=str(tmp_path / 'dashboard.png'), full_page=True)
        finally:
            await browser.close()
