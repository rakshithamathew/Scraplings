# Job Automation: Windows PowerShell

Run all backend commands from the cloned repository:

```powershell
Set-Location "C:\Users\raksh\OneDrive\Documents\GitHub\ScraplingAgent"
```

## 1. Activate the Python environment

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks local activation scripts for this terminal:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 2. Install backend dependencies

```powershell
python -m pip install -r job_automation\api\requirements.txt
```

If Playwright is used for application automation:

```powershell
python -m playwright install chromium
```

## 3. Initialize the database

```powershell
python -c "from job_automation.database import initialize_database; engine = initialize_database(); engine.dispose(); print('Database ready: data/jobs.db')"
```

Previously applied jobs must remain `APPLIED` after rescoring or uploading a new CV.

## 4. Start the backend

Keep this terminal open:

```powershell
python -m uvicorn job_automation.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Backend:

```text
http://127.0.0.1:8000
```

API docs:

```text
http://127.0.0.1:8000/docs
```

## 5. Start the frontend

Open a second PowerShell terminal:

```powershell
Set-Location "C:\Users\raksh\OneDrive\Documents\GitHub\ScraplingAgent\job_automation\frontend"
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

Open the dashboard:

```powershell
Start-Process "http://127.0.0.1:5173"
```

## 6. Upload the active resume

Use the dashboard button:

```text
Upload Resume
```

Supported formats:

- PDF
- DOCX

The uploaded CV becomes the single active resume. The system must parse it and use it for ATS scoring and future applications.

When a new resume is uploaded:

- make it the active resume
- rescore `DISCOVERED` and `QUALIFIED` jobs
- do not change jobs already marked `APPLIED`

## 7. Job discovery rules

The scraper must search roles similar to the uploaded CV, including frontend, React, Angular, JavaScript, TypeScript, full-stack, senior frontend, lead frontend, technical lead, product engineering, healthcare frontend, and AI-enabled application roles.

### Remote jobs

Scrape remote jobs worldwide.

Keep remote jobs where the candidate can reasonably apply from India.

Skip jobs explicitly restricted to regions that exclude India, such as:

```text
US only
Canada only
UK only
EU only
```

unless the posting explicitly supports international candidates or relocation.

### Hybrid and onsite jobs

Keep hybrid and onsite jobs only in Bengaluru/Bangalore.

Accept common location variations such as:

```text
Bengaluru
Bangalore
Bengaluru, Karnataka
Bangalore, Karnataka
Bengaluru Urban
Greater Bengaluru
```

Skip hybrid or onsite jobs outside Bengaluru/Bangalore.

## 8. Run job discovery

```powershell
python -m job_automation.scraper.service
```

Expected flow:

```text
discover jobs
→ normalize jobs
→ apply location/work-mode rules
→ remove duplicates
→ save new jobs
```

Jobs that fail hard rules should be marked `SKIPPED`.

## 9. Score jobs against the uploaded CV

```powershell
python -m job_automation.matching.ranker --limit 20
```

The ATS score must be based on:

```text
Active uploaded CV
+
Job description
```

Score from 0 to 100 using:

- job title similarity
- required skill match
- preferred skill match
- experience alignment
- technology match
- domain relevance
- keyword overlap

Store:

```text
match_score
matched_skills
missing_skills
score_reason
```

Application eligibility rule:

```text
ATS > 70  → QUALIFIED
ATS <= 70 → DISCOVERED
```

Do not rescore or change `APPLIED` jobs.

## 10. Job statuses

Keep only these states:

```text
DISCOVERED
QUALIFIED
APPLIED
SKIPPED
```

Remove these from the workflow and frontend:

```text
NEEDS_REVIEW
FAILED
INTERVIEW
```

## 11. Auto Apply workflow

Use the dashboard button:

```text
Auto Apply
```

For every `QUALIFIED` job:

```text
QUALIFIED
    ↓
verify ATS > 70
    ↓
check duplicate application
    ↓
open application URL
    ↓
detect application provider
    ↓
fill candidate information
    ↓
attach active uploaded resume
    ↓
fill supported application fields
    ↓
submit application
    ↓
verify submission confirmation
    ↓
APPLIED
```

Supported providers can include:

- Greenhouse
- Lever
- Workday
- Ashby
- SmartRecruiters
- Generic public application forms

Do not bypass CAPTCHA, OTP, authentication controls, or other security restrictions.

Do not fabricate answers to application questions.

## 12. Applied-state protection

A job must move to `APPLIED` only after successful submission is verified.

Valid confirmation evidence can include:

- thank-you page
- application submitted message
- application received message
- confirmation page
- external application/reference ID

Do not mark a job `APPLIED` merely because the page opened, fields were filled, the resume was attached, or Submit was clicked.

After successful submission, store:

```text
status = APPLIED
applied_at
resume_used
application_method
confirmation_text
external_application_id
```

The job must then automatically move from `QUALIFIED` to `APPLIED` in the dashboard.

## 13. Duplicate application protection

Before every application attempt, check:

```text
external job id
company
normalized job title
canonical application URL
```

If the job was already successfully applied to:

```text
keep status = APPLIED
do not submit again
```

## 14. Test auto-application safely

For the first test:

```powershell
$env:DRY_RUN = "true"
$env:APPLICATION_HEADLESS = "false"
python -m uvicorn job_automation.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Then click `Auto Apply`.

In dry-run mode the browser should:

```text
open application
→ fill candidate information
→ attach active resume
→ fill supported fields
→ stop before final submission
```

The job must remain `QUALIFIED` in dry-run mode.

## 15. Enable live auto apply

After dry-run testing is correct, stop the backend with `Ctrl + C` and restart:

```powershell
$env:DRY_RUN = "false"
$env:APPLICATION_HEADLESS = "true"
python -m uvicorn job_automation.api.main:app --host 127.0.0.1 --port 8000 --reload
```

Now `Auto Apply` can submit eligible applications.

Only successfully confirmed submissions move to `APPLIED`.

## 16. Dashboard requirement

Keep the frontend to one simple screen.

Top section:

```text
Job Automation

Active Resume: <uploaded filename>

Total Jobs
Qualified
Applied

[Upload Resume] [Run Scraper] [Run Scoring] [Auto Apply] [Refresh]
```

Filters:

```text
ALL
QUALIFIED
APPLIED
```

Table columns:

```text
Score
Company
Job Title
Location
Work Type
Source
Status
Resume
Applied Date
Application Link
```

Do not add charts, multiple pages, Redux, complicated dashboard widgets, Needs Review, Failed, or Interview.

## 17. Normal usage

```text
1. Upload Resume
2. Run Scraper
3. Run Scoring
4. Check QUALIFIED jobs
5. Click Auto Apply
6. Successfully submitted jobs automatically move to APPLIED
7. Use Refresh to reload the table and counts
```

Final workflow:

```text
UPLOAD CV
    ↓
PARSE CV
    ↓
SCRAPE JOBS
    ↓
FILTER BY LOCATION / ROLE
    ↓
SCORE AGAINST ACTIVE CV
    ↓
ATS > 70?
   /     \
 NO       YES
 ↓         ↓
DISCOVERED QUALIFIED
              ↓
          AUTO APPLY
              ↓
       SUBMISSION CONFIRMED
              ↓
           APPLIED
```

## LinkedIn discovery

Run the existing Scrapling discovery service for LinkedIn only:

```powershell
.\.venv\Scripts\python.exe -m job_automation.scraper.service --source linkedin --discovery-only
```

This uses the existing database (default `data/jobs.db`) and does not require an
active resume. The normal dashboard discovery/scoring flow also uses this adapter.
`config/job_sources.json` contains all eight requested titles and Bengaluru plus
worldwide remote searches. There is no posted-date cutoff. Pagination is bounded
by `max_pages` (default 1 per title/location); increase it explicitly for larger
runs. Requests are sequential with a two-second delay.

The adapter fetches public listing and detail pages using Scrapling's AsyncFetcher
and Selector. It does not use account cookies, private APIs, browser impersonation,
CAPTCHA solving, or login automation. Any redirect, verification/login page, HTTP
401/403/429/999 stops the source. Completed details before a block can still be
saved; the summary reports a failed source. Unavailable descriptions are omitted
from discovery rather than treating listing snippets as full descriptions.

Requested fields use the existing schema: `job_title` = `title`, `work_type` =
`workplace_type`, `job_description` = `description`, `job_url` = `source_url`, and
`posted_date` = `posted_at`. `company`, `location`, `application_url`, and `source`
retain their names. Public external application links are collected when exposed;
otherwise the canonical LinkedIn job page is retained as the application entry
point. Missing dates remain null. No application is submitted.

LinkedIn remote jobs require evidence of India eligibility or worldwide hiring;
unknown eligibility is skipped. Hybrid/onsite roles require Bangalore/Bengaluru.
The existing repository deduplicates repeat discoveries using source/job ID and
its existing identity matching. Filtered records remain in the database as
`SKIPPED` with a reason, consistent with the existing discovery architecture.

Verification on 2026-09-07: the public LinkedIn search returned an access-control
page on the first request. Discovery stopped with zero jobs saved. A successful
live extraction remains dependent on LinkedIn exposing authorized public pages.

## Public company contact discovery

Every newly saved job now creates a durable `PENDING` contact-discovery task in
the same transaction. This covers each newly discovered company and also links
contacts to subsequent jobs at that company. Existing job duplicates do not
create duplicate tasks. Run the worker after job discovery:

```powershell
.\.venv\Scripts\python.exe -m job_automation.outreach.discovery
```

The worker checks the public job page for its recruiter first, then follows
explicitly published employer links and same-site team/about/leadership/careers
links (up to six pages per job, two seconds between network requests). Company
pages are cached during the run. It extracts public Person structured data and
supported person/team cards; it does not crawl unrelated employee profiles or
use search snippets as verified contact evidence. Sites with unsupported markup
may return no contacts. Employer URLs and email addresses are never guessed.

Priority order: job-posting recruiter, Talent Acquisition, Technical Recruiter,
Hiring Manager, Engineering Manager, Head of Engineering, then CTO/Founder.
Hiring and engineering managers require evidence tying them to the role/team.
CTO/Founder also requires a published employee count of 1�50 and role/hiring
relevance. Unknown company size does not qualify. Person records must explicitly
match the employer, or appear in its verified team page or the job's poster card.

`job_contacts` stores `job_id`, `name`, `title`, `company`, `linkedin_url`,
`work_email`, and `source` (the public evidence URL), plus priority and relevance
evidence. Missing LinkedIn URLs/emails remain null. An email must be published
in that person's record on the employer site, or match the employer URL explicitly
published in their record. Common personal email providers are excluded. There
is no email-pattern generation, mailbox probing, private data source, or sending.

Contacts are deduplicated per job by normalized company/name, canonical LinkedIn
profile, or published email. Repeated discoveries enrich missing fields. The
same relevant person can be associated with multiple jobs; this is intentional.
Discovery does not replace an existing outreach recipient, draft, or sent state.

Login/verification pages, redirects, and HTTP 401/403/429/999 stop discovery and
persist `BLOCKED`. Network failures persist `FAILED`. Completed tasks persist
`COMPLETE`, including searches with no qualifying contacts. Only `PENDING` tasks
run automatically when the worker is invoked; blocked/failed tasks require an
explicit per-job retry after authorized access is available:

```powershell
.\.venv\Scripts\python.exe -m job_automation.outreach.discovery --job-id 123
.\.venv\Scripts\python.exe -m job_automation.outreach.discovery --job-id 123 --list
```

The per-job command also supports jobs saved before this workflow was installed.
API equivalents: `POST /contacts/discover` processes pending tasks,
`POST /jobs/{job_id}/contacts/discover` explicitly runs one job, and
`GET /jobs/{job_id}/contacts` lists its saved contacts in priority order.

## CV-grounded personalized outreach

Generate drafts for all `QUALIFIED` jobs, or one qualified job:

```powershell
.\.venv\Scripts\python.exe -m job_automation.outreach.personalized
.\.venv\Scripts\python.exe -m job_automation.outreach.personalized --job-id 123
.\.venv\Scripts\python.exe -m job_automation.outreach.personalized --job-id 123 --list
```

The generator verifies the active uploaded file's SHA-256 and reparses that file.
It does not use stale profile fields, inferred skills, or the JD as evidence of
candidate experience. The experience parser requires an explicit statement such
as “5+ years of experience”; “25+ years of patient records” is not candidate tenure.

For each qualified job it uses the highest-priority saved relevant contact and
the complete description, matching CV skills against the entire JD. It includes
a directly supported work/project example. Missing work email is allowed:
profile-only contacts still receive stored email and LinkedIn draft text.
No email, LinkedIn message, or contact request is sent.

`outreach_messages` stores one current record per `job_id`, containing
`email_subject`, `email_body` (150–200 whitespace-delimited words), and
`linkedin_message` (50–80 words), plus contact snapshot, CV hash, JD hash, selected
CV evidence and matched skills. Identical inputs are idempotent; changed inputs
regenerate the current draft on the next run. Existing sent/approved/replied
outreach tracking is preserved. These are local drafts, not delivery records.

An active readable CV, explicit experience statement, relevant contact, and
complete JD are required. Missing or unverifiable inputs produce `WAITING_INPUT`
with reasons and null message fields. The generator never pads missing candidate
facts with invented projects, metrics, skills, or referrals.

`jobs.description_complete` records full-detail provenance from the supported
scrapers. Existing unverified descriptions remain unknown; listing snippets are
not marked complete just because they contain many words. Re-fetch the full
public job detail through the source adapter before generation. Changing a
stored description without explicit full-detail provenance clears this flag.

API: `POST /outreach/generate`, `POST /jobs/{job_id}/outreach/generate`, and
`GET /jobs/{job_id}/outreach`. Run generation again after the active CV, complete
JD, or contact changes before using a saved draft.

Initial run: 57 qualified jobs checked; all 57 recorded as `WAITING_INPUT` because
complete JDs and relevant saved contacts were unavailable. No outreach text was
fabricated for those jobs.

## Authenticated outreach email automation

The sender reuses `ComposioConfig` and the existing connected Gmail account. It
requires the local backend environment to contain `COMPOSIO_ENABLED=true`,
`COMPOSIO_API_KEY`, `COMPOSIO_USER_ID`, and
`COMPOSIO_GMAIL_CONNECTED_ACCOUNT_ID` for that existing Composio connection.
The account must be ACTIVE, belong to that user, and have the Gmail toolkit.
No alternative account is chosen. The authenticated Gmail app in the assistant
session does not automatically provide credentials to the standalone Python
backend; no session tokens are copied into the project.

Install the sender dependency (installed in this workspace during implementation):

```powershell
.\.venv\Scripts\python.exe -m pip install -r job_automation/integrations/requirements.txt
```

Send at most one eligible outreach email, inspect history, or run a worker:

```powershell
.\.venv\Scripts\python.exe -m job_automation.outreach.email_automation
.\.venv\Scripts\python.exe -m job_automation.outreach.email_automation --job-id 123
.\.venv\Scripts\python.exe -m job_automation.outreach.email_automation --status
.\.venv\Scripts\python.exe -m job_automation.outreach.email_automation --watch
```

`--watch` checks every ten minutes until stopped. API equivalents are
`POST /outreach/send`, `POST /jobs/{job_id}/outreach/send`, and
`GET /outreach/email-status?job_id=123`. Send commands and POST endpoints perform
real sends when all conditions are satisfied; status requests are read-only.
The original basic `GmailDraftWorkflow` remains draft-only. Only this qualified,
CV-grounded pipeline dispatches messages.

Eligibility and dispatch:

- ATS must be strictly greater than 70; status must be QUALIFIED or APPLIED.
- Refresh qualified jobs' personalized drafts from the active CV, selecting the
  highest-priority relevant contact with a publicly available work email. A
  profile-only contact is not assigned an invented address.
- Require complete JD provenance, current CV and contact fingerprints, and a
  generated email. Stale, incomplete, closed, or previously contacted jobs skip.
- Attach the active uploaded PDF/DOCX. The sender verifies its SHA-256 and stages
  an immutable copy for the send. Only the staging directory is allowlisted for
  Composio uploads. Temporary local copies are removed after the call.
- One selected recipient per job; no CC, BCC, mailing list, or employee fan-out.

Limits are shared by every worker using the database: one attempt per invocation,
a minimum ten-minute gap, and at most ten attempts in a rolling 24-hour period.
The database reserves each attempt atomically before any provider send. Concurrent
workers and restarts cannot reset quotas or claim the same job again. These are
local application limits, not claims about Gmail's provider quotas.

`outreach_emails` stores related `job_id`, `contact_id`, recipient, company,
subject/body snapshot, active-CV hash/filename, attempt time, `sent_at`, connected
account ID, Gmail message ID, and email status. Audit records are retained even
if the job/draft is later removed, and changing a contact does not reset a claim.
The job's existing outreach status and sent timestamp are updated after confirmed
success. Generating a new draft does not alter the sent-email snapshot.

Statuses: `SENDING` is a durable reserved attempt; `SENT` requires a confirmed
provider message ID; `FAILED` records provider-reported failure; `UNKNOWN` means
the result could not be confirmed. SENDING after a crash, FAILED, and UNKNOWN are
never automatically replayed. Check Gmail Sent/provider logs before manual
recovery; absence of a local receipt is not proof that Gmail did not send.
This provides at-most-once automatic attempts rather than pretending Gmail offers
an exactly-once delivery guarantee.

The sender pins the Gmail toolkit version and requires a modern Composio SDK
whose execution calls disable retries. Attachment handling follows the
[Composio Gmail guide](https://docs.composio.dev/kb/guide/toolkits-gmail) and
[direct execution documentation](https://docs.composio.dev/docs/tools-direct/executing-tools).

Current workspace: Gmail was verified in the assistant session and the Composio
SDK was installed locally, but local Composio account configuration is absent.
The 57 qualified jobs also still lack send-ready complete JDs/contact drafts.
No email was sent and no background sending worker was started.
