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
