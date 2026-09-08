from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
import threading
import time

import pytest

from job_automation.applications.candidate_profile import CandidateProfile
from job_automation.applications.generic import GenericApplicationAgent
from job_automation.database import Job, JobStatus, WorkplaceType


@pytest.mark.asyncio
async def test_real_playwright_dry_run_fills_local_form(tmp_path: Path) -> None:
    html = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form>
        <label for="first">First Name</label><input id="first" name="first_name" required>
        <label for="last">Last Name</label><input id="last" name="last_name" required>
        <label for="email">Email</label><input id="email" name="email" type="email" required>
        <label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
        <button type="submit">Submit application</button>
      </form>
    </body></html>
    """
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    resume = resumes / "frontend_resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n% test fixture\n")

    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    job = Job(
        id=1,
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="test",
        application_url=f"http://127.0.0.1:{server.server_port}/index.html",
        recommended_resume="resumes/frontend_resume.pdf",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    headed = os.getenv("APPLICATION_BROWSER_TEST_HEADLESS", "true").casefold() == "false"
    agent = GenericApplicationAgent(project_root=tmp_path, headless=not headed, dry_run=True)
    try:
        assert await agent.open_application(job) is True
        details = await agent.fill_candidate_details(
            CandidateProfile(
                first_name="Rakshitha",
                last_name="M",
                email="candidate@example.test",
            )
        )
        assert set(details.fields_filled) == {"first_name", "last_name", "email"}
        assert await agent.upload_resume(resume) is True
        assert (await agent.validate_before_submit()).valid is True

        result = await agent.submit()

        assert result.ready_to_submit is True
        assert result.submitted is False
        assert "DRY_RUN" in result.message
    finally:
        await agent.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_real_playwright_requires_confirmation_after_submit(tmp_path: Path) -> None:
    html = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form id="application">
        <label for="first">First Name</label><input id="first" name="first_name" required>
        <label for="last">Last Name</label><input id="last" name="last_name" required>
        <label for="email">Email</label><input id="email" type="email" required>
        <label for="resume">Resume</label><input id="resume" type="file" required>
        <button type="submit">Submit application</button>
      </form>
      <script>
        document.querySelector('#application').addEventListener('submit', event => {
          event.preventDefault();
          document.body.textContent = 'Application submitted successfully. Confirmation ID: TEST-1234';
        });
      </script>
    </body></html>
    """
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    resumes = tmp_path / "resumes"
    resumes.mkdir()
    resume = resumes / "frontend_resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n% test fixture\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    job = Job(
        id=2,
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="test",
        application_url=f"http://127.0.0.1:{server.server_port}/index.html",
        recommended_resume="resumes/frontend_resume.pdf",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    agent = GenericApplicationAgent(project_root=tmp_path, headless=True, dry_run=False)
    try:
        await agent.open_application(job)
        await agent.fill_candidate_details(
            CandidateProfile(first_name="Rakshitha", last_name="M", email="candidate@example.test")
        )
        await agent.upload_resume(resume)

        submitted = await agent.submit()
        confirmation = await agent.verify_submission()

        assert submitted.submitted is True
        assert confirmation.confirmed is True
        assert confirmation.external_application_id == "TEST-1234"
    finally:
        await agent.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_listing_apply_link_is_followed_before_form_filling(tmp_path: Path) -> None:
    listing = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <a href="/application.html">Apply on company website</a>
    </body></html>
    """
    application = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form>
        <label for="first">First Name</label><input id="first" name="first_name" required>
        <label for="email">Email</label><input id="email" name="email" type="email" required>
        <label for="resume">Resume</label><input id="resume" type="file" required>
        <button type="submit">Submit application</button>
      </form>
    </body></html>
    """
    (tmp_path / "listing.html").write_text(listing, encoding="utf-8")
    (tmp_path / "application.html").write_text(application, encoding="utf-8")
    (tmp_path / "resumes").mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    job = Job(
        id=3,
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="linkedin",
        application_url=f"http://127.0.0.1:{server.server_port}/listing.html",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    agent = GenericApplicationAgent(project_root=tmp_path, headless=True, dry_run=True)
    try:
        assert await agent.open_application(job) is True
        assert agent.page.url.endswith("/application.html")
        assert agent.resolved_application_url == agent.page.url
        assert await agent.page.locator('input[type="email"]').count() == 1
    finally:
        await agent.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_browser_profile_persists_authentication_cookie(tmp_path: Path) -> None:
    html = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form>
        <input name="first_name"><input type="email" name="email">
        <button type="submit">Submit application</button>
      </form>
    </body></html>
    """
    (tmp_path / "index.html").write_text(html, encoding="utf-8")
    (tmp_path / "resumes").mkdir()
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    job = Job(
        id=4,
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="company",
        application_url=f"http://127.0.0.1:{server.server_port}/index.html",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    profile_directory = tmp_path / "browser-profile"
    first = GenericApplicationAgent(
        project_root=tmp_path,
        headless=True,
        dry_run=True,
        browser_profile_directory=profile_directory,
    )
    second = GenericApplicationAgent(
        project_root=tmp_path,
        headless=True,
        dry_run=True,
        browser_profile_directory=profile_directory,
    )
    try:
        await first.open_application(job)
        await first._context.add_cookies([{
            "name": "session",
            "value": "authenticated",
            "url": job.application_url,
            "expires": int(time.time()) + 3600,
        }])
        await first.close()

        await second.open_application(job)
        cookies = await second._context.cookies(job.application_url)

        assert any(cookie["name"] == "session" and cookie["value"] == "authenticated" for cookie in cookies)
    finally:
        await first.close()
        await second.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.asyncio
async def test_complete_application_handles_multiple_form_steps(tmp_path: Path) -> None:
    first_step = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form action="/step-two.html" method="get">
        <label for="first">First Name</label><input id="first" name="first_name" required>
        <label for="email">Email</label><input id="email" name="email" type="email" required>
        <button type="submit">Continue</button>
      </form>
    </body></html>
    """
    second_step = """
    <!doctype html><html><body>
      <h1>Frontend Engineer</h1>
      <form>
        <label for="resume">Resume</label><input id="resume" name="resume" type="file" required>
        <button type="submit">Submit application</button>
      </form>
    </body></html>
    """
    (tmp_path / "step-one.html").write_text(first_step, encoding="utf-8")
    (tmp_path / "step-two.html").write_text(second_step, encoding="utf-8")
    resume_directory = tmp_path / "resumes"
    resume_directory.mkdir()
    resume = resume_directory / "frontend_resume.pdf"
    resume.write_bytes(b"%PDF-1.4\n% test fixture\n")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    job = Job(
        id=5,
        title="Frontend Engineer",
        company="Example",
        location="Remote worldwide",
        workplace_type=WorkplaceType.REMOTE,
        source="test",
        application_url=f"http://127.0.0.1:{server.server_port}/step-one.html",
        recommended_resume="resumes/frontend_resume.pdf",
        match_score=90,
        status=JobStatus.QUALIFIED,
        is_open=True,
    )
    agent = GenericApplicationAgent(project_root=tmp_path, headless=True, dry_run=True)
    try:
        assert await agent.open_application(job) is True

        result = await agent.complete_application(
            job,
            CandidateProfile(first_name="Rakshitha", email="candidate@example.test"),
            resume,
        )

        assert result.ready_to_submit is True
        assert result.submitted is False
        assert agent.page.url.endswith("/step-two.html?first_name=Rakshitha&email=candidate%40example.test")
        assert agent.fill_result.resume_attached is True
    finally:
        await agent.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
