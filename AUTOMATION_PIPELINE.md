# Complete automation pipeline

Use **Run Full Automation** in the existing dashboard, `POST /automation/run`, or:

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
