# LinkedIn outreach with saved authentication

The sender reuses the existing `SessionManager` Chrome profile. It opens the job,
verifies the company, extracts the full description and job poster with Scrapling,
and generates a CV-grounded message for that person. If no poster is shown, it can
use the highest-priority relevant LinkedIn contact already saved against the job.
It opens that profile and uses its permitted Message control.

```powershell
.\.venv\Scripts\python.exe -m job_automation.outreach.linkedin_automation
.\.venv\Scripts\python.exe -m job_automation.outreach.linkedin_automation --job-id 123
.\.venv\Scripts\python.exe -m job_automation.outreach.linkedin_automation --status
```

API equivalents are `POST /outreach/linkedin/send`,
`POST /jobs/{job_id}/outreach/linkedin/send`, and
`GET /outreach/linkedin/status?job_id=123`. Send commands perform real messaging
when all requirements pass. Each invocation handles at most one job; no background
sender starts automatically.

Only open QUALIFIED/APPLIED jobs with ATS strictly greater than 70 qualify.
The active uploaded CV, complete JD, selected contact, and generated 50-80 word
message must remain current immediately before dispatch. Recipient profile URL,
name, and single-person conversation are verified before filling and sending.
Login, CAPTCHA, OTP, security checks, access restrictions, missing Message
controls, ambiguous recipients, and unsupported page layouts stop the attempt.
The sender does not automate authorization, copy/reset profiles, request a
connection, purchase InMail, or use private messaging APIs.

`linkedin_outreach` stores `job_id`, the contact snapshot, message snapshot,
`linkedin_message_sent`, `sent_at`, attempted time, status, and diagnostic detail.
An atomic claim precedes the Send click. Canonical job/profile identity prevents
duplicate sends across reimported jobs, and each job receives at most one automatic
attempt. A shared database lease prevents concurrent use of the sender's profile.
Limits are at least ten minutes between attempts and ten attempts per rolling day.

`SENT` requires the exact outgoing message to appear in the verified conversation
and the composer to clear. `ALREADY_PRESENT` records an existing outgoing message
without another click; its original `sent_at` stays unknown. `SENDING` after a crash
and `UNKNOWN` outcomes are never automatically retried. Inspect the conversation
before any manual recovery. `BLOCKED` and `UNAVAILABLE` require resolving normal
authorization or messaging availability before an explicit job-specific retry.
Browser layout changes can stop delivery safely; automated tests use local
intercepted pages and do not establish compatibility with every live layout.
