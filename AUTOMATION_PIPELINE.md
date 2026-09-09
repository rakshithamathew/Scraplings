# Complete automation pipeline

Use `POST /automation/run` or the CLI for the complete pipeline:

```powershell
.\.venv\Scripts\python.exe -m job_automation.pipeline
```

This performs real applications and outreach according to the existing Auto Apply
settings. It uses configured Scrapling sources and the active uploaded CV. To
exercise discovery, scoring, contact discovery, drafts, and Auto Apply's preview
without submitting applications or sending either kind of outreach:

```powershell
.\.venv\Scripts\python.exe -m job_automation.pipeline --dry-run
```

The pipeline normalizes and deduplicates source results, checks the existing
India-eligible remote/Bengaluru location rules, and scores jobs against the actual
CV file after verifying its uploaded hash. Jobs must score strictly above 70.
Contact discovery and generation produce the personalized email and LinkedIn
message. Missing contact/draft inputs do not prevent a valid application.

Auto Apply runs next. Only a persisted APPLIED job with submission evidence can
proceed to recruiter email with the active resume and permitted LinkedIn outreach.
Application failure stops that job's outreach. Email failure does not suppress
available LinkedIn messaging or stop other jobs. A CV change stops further actions
on affected jobs until a new run scores them against that CV.

Each run honors the existing application batch limit and delay. The outreach
modules retain their own durable ten-minute cooldowns and rolling daily limits.
Rate-limited or missing-input outreach remains pending for a later invocation;
the pipeline does not sleep for long intervals or start a background worker.
Previously confirmed applications can resume pending outreach after rescoring,
without another application. Existing sent/uncertain outreach claims are retained.

Real Auto Apply now commits a database claim before opening an application adapter.
Claims cover the job ID and normalized application URL across API/CLI workers.
They survive crashes and job deletion. An uncertain attempt is never automatically
replayed, even if the form's final confirmation could not be captured. Inspect the
provider before manually reconciling a claim; deleting an uncertain claim can cause
a duplicate submission. Dry runs do not create dispatch claims.

All stages use existing authentication and stop at login, CAPTCHA, OTP, access
restrictions, or unsupported controls. The pipeline does not reauthorize accounts.

Run history, per-job stage outcomes, errors, and summaries are persisted in
`pipeline_runs`; individual jobs, drafts, contacts, application claims, email
receipts, and LinkedIn receipts remain in their existing database tables.

```powershell
.\.venv\Scripts\python.exe -m job_automation.pipeline --status
```

`GET /automation/status` returns the latest 20 runs with stage details. A run left
RUNNING after a process crash is an incomplete audit; dispatch claims still prevent
replay. Each completed run logs and returns:

- Jobs discovered: raw results returned by configured sources before deduplication.
- Jobs ATS > 70: eligible saved jobs, including pending outreach for applied jobs.
- Jobs applied: newly confirmed applications during this run.
- Emails sent: newly confirmed email sends during this run.
- LinkedIn messages sent: newly confirmed messages during this run.
- Jobs skipped: saved jobs with no new confirmed application or outreach action,
  including filtered, deferred, failed, already completed, and dry-run jobs.

The discovered count covers the current scrape; subsequent stages also process
the existing database backlog. These counts therefore need not sum to discovered.
No contacts, CV contents, or message bodies are printed in the final summary.

## One-screen dashboard

The dashboard exposes the same modules as individual actions: Upload Resume,
Run Scraper, Run Scoring, Find Contacts, Auto Apply, Send Outreach, and Refresh.
Run Scraper saves discovery results; Run Scoring performs qualification separately.
Auto Apply uses selected jobs, or the configured batch when none are selected.
Send Outreach sends email only for confirmed APPLIED jobs and respects existing
duplicate guards and rate limits. Open LinkedIn opens the saved contact profile;
it does not send a message or mark a message as sent.

Each row displays the highest-priority saved relevant contact and that person's
public email/profile. Missing email remains Not found; a different person's email
is never substituted. The contacts table stores work email with the related job ID.
Find Contact refreshes a single job, Draft Email saves and previews a personalized
draft inline, and Send Email requires a confirmed application and public email.

Email statuses are NOT_FOUND, FOUND, DRAFTED, and SENT. LinkedIn statuses are
NOT_FOUND, FOUND, and SENT. Confirmed delivery receipts drive SENT and the top
delivery counters. Uncertain email attempts show a review note and disable repeat
send even though their display status never advances to SENT. CONTACTS FOUND counts
jobs with a relevant contact, not the total number of employees discovered.

ALL, QUALIFIED, APPLIED, EMAIL FOUND, and EMAIL SENT filters share the same table.
EMAIL FOUND includes any displayed contact with a public work email, including
drafted and sent rows. Dashboard projections are available at GET /dashboard/outreach;
dashboard email dispatch uses POST /dashboard/outreach/send or
POST /dashboard/jobs/{job_id}/send-email.

Contact discovery now reads supported LinkedIn/Naukri job pages in the existing
authorized browser profile when one is connected. HTTP denial, login, CAPTCHA,
OTP, redirected jobs, and verification stop that host without retrying through a
different transport. Without saved authorization it can read only public HTML.
Empty JavaScript shells are marked FAILED instead of reported as completed searches.
The table displays pending, blocked, failed, and no-public-contact outcomes.

Named recruiter cards and explicitly published HR/careers mailboxes in job
descriptions are supported. Shared inboxes display as Recruitment team / Public
recruitment mailbox; they are never labelled as a named HR employee. No personal
mailboxes, inferred email patterns, support addresses, or unrelated employees are
added. A name-only record enriched with a verified email retains the email's source.

Public employer pages already identified by a publisher are followed as before.
`config/company_contact_sources.json` adds independently verified public employer
URLs for specific company names. It contains URLs, not guessed addresses. These
independent sources can still be checked when a job portal is blocked, with the
partial-discovery restriction preserved in the audit. This is not a general search
engine or a guarantee that every company publishes a recruiter address.
