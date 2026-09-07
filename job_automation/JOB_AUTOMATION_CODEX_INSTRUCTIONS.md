# Codex Job Automation Instructions

## Objective

Build and run the job-discovery and application workflow inside the current `ScraplingAgent` project using the existing Scrapling-based architecture.

The system must use the candidate profile and rules below as the source of truth for:

- job discovery
- job-title expansion
- location filtering
- ATS scoring
- application eligibility
- duplicate prevention
- application tracking
- resume selection
- outreach preparation

Do not modify the candidate's resume facts, invent experience, or add skills that are not supported by the resume.

---

# 1. Candidate Profile

Candidate: **Rakshitha M**

Location: **Bengaluru, India**

Experience: **5+ years**

Primary profile:

- Technical Lead
- Software Development Team Lead
- Frontend / Full Stack Engineer
- React Developer
- Angular Developer
- JavaScript / TypeScript Engineer
- AI-enabled product engineer
- Healthcare / clinical application frontend experience
- High-volume data application experience

Core technical skills:

### Frontend
- React.js
- Angular
- JavaScript
- TypeScript
- Redux
- Axios
- Material UI
- Tailwind CSS
- Responsive Design
- Micro-frontends
- Cypress

### Backend / API
- Node.js
- Express.js
- REST APIs
- GraphQL
- SQL
- Python

### Cloud / DevOps
- Azure
- Docker
- Kubernetes
- Nginx
- GitHub
- CI/CD

### Additional relevant experience
- WebSockets
- HL7 / FHIR
- Gemini / GenAI
- OpenAI / LLM applications
- OCR / NLP
- RAG
- Chart.js
- Snowflake
- Amplitude
- Webhooks
- CRM integrations
- System Design
- Product execution
- Cross-functional collaboration

---

# 2. Job Search Rules

These rules are mandatory.

## Remote jobs

Scrape **remote jobs from anywhere in the world**.

A remote job is eligible when:

- it explicitly allows remote work, AND
- the candidate can reasonably apply from India, OR
- it says worldwide / global / work from anywhere / distributed / remote across multiple countries.

Do not automatically reject a remote role just because the company is outside India.

However, skip jobs that explicitly restrict remote hiring to a geography that excludes India, for example:

- US only
- Canada only
- EU residents only
- UK only

unless the job explicitly supports relocation or international candidates.

## Onsite and Hybrid jobs

For onsite and hybrid jobs:

**ONLY keep jobs located in Bengaluru / Bangalore.**

Accept location variations such as:

- Bengaluru
- Bangalore
- Bengaluru, Karnataka
- Bangalore, Karnataka
- Bengaluru Urban
- Greater Bengaluru

Reject onsite/hybrid roles from other cities.

---

# 3. Job Titles to Search

Do not search only one exact title.

Search all roles reasonably aligned with the candidate's experience.

## Highest priority

- Senior Frontend Developer
- Senior Frontend Engineer
- Frontend Developer
- Frontend Engineer
- Senior React Developer
- Senior React Engineer
- React Developer
- React Engineer
- UI Developer
- UI Engineer
- Senior UI Developer
- Senior UI Engineer
- Web Application Developer
- Senior Web Developer
- JavaScript Developer
- JavaScript Engineer
- TypeScript Developer
- TypeScript Engineer

## Full Stack

- Full Stack Developer
- Full Stack Engineer
- Senior Full Stack Developer
- Senior Full Stack Engineer
- MERN Stack Developer
- MERN Stack Engineer
- React Node Developer
- React Node Engineer

## Lead / Senior IC roles

- Technical Lead
- Tech Lead
- Frontend Lead
- Frontend Technical Lead
- Lead Frontend Developer
- Lead Frontend Engineer
- Lead UI Engineer
- Lead React Developer
- Lead React Engineer
- Software Development Team Lead
- Software Engineer III
- Senior Software Engineer
- Staff Frontend Engineer
- Staff Software Engineer

For Staff roles, only keep them when the job requirements are reasonably compatible with 5+ years of experience.

## Product / Platform / Dashboard roles

- Product Engineer
- Senior Product Engineer
- Platform UI Engineer
- Dashboard Developer
- Dashboard UI Engineer
- Clinical Dashboard Developer
- Clinical UI Developer
- Healthcare Frontend Developer
- Healthcare Software Engineer
- HealthTech Frontend Engineer
- Medical Software UI Developer

## AI-enabled frontend/full-stack roles

Also search:

- AI Product Engineer
- GenAI Frontend Engineer
- GenAI Full Stack Engineer
- AI Application Developer
- LLM Application Engineer
- AI Full Stack Developer

Only keep these when the job still materially matches the candidate's frontend/full-stack experience.

---

# 4. Roles to Avoid

Avoid jobs where the core role is primarily:

- pure Data Scientist
- pure ML Researcher
- pure ML Engineer requiring deep model training experience
- DevOps-only
- SRE-only
- Cloud Engineer-only
- DBA
- Network Engineer
- cybersecurity specialist
- QA-only
- manual tester
- mobile-only Android/iOS developer
- Java-only backend developer
- .NET-only backend developer
- embedded systems engineer
- firmware engineer

A missing secondary skill should NOT automatically reject an otherwise strong frontend/full-stack role.

---

# 5. Sources to Discover Jobs

Use the existing Scrapling scraper architecture.

Prioritize:

1. public company careers pages
2. Greenhouse
3. Lever
4. Workday
5. Ashby
6. SmartRecruiters
7. other publicly accessible ATS portals
8. Google Jobs discovery
9. Indeed where permitted
10. Naukri where permitted
11. other public job boards where permitted

Also discover company career portals directly.

Example discovery flow:

search / aggregator
→ identify company
→ locate official company careers page
→ locate canonical job posting
→ scrape canonical posting
→ normalize
→ deduplicate
→ score
→ apply

Prefer the official company / ATS application URL as the canonical `application_url`.

Do not bypass:

- authentication controls
- CAPTCHAs
- access restrictions
- rate limits
- platform security measures

---

# 6. Job Normalization

Every discovered job must normalize into the existing Job model.

Capture at least:

- external_id
- title
- company
- location
- workplace_type
- description
- required_skills
- preferred_skills
- minimum_experience
- maximum_experience
- source
- source_url
- application_url
- posted_at
- scraped_at
- match_score
- status

Normalize workplace type to:

- REMOTE
- HYBRID
- ONSITE
- UNKNOWN

---

# 7. Mandatory Filtering Before ATS Scoring

Before spending resources on scoring, reject jobs that fail hard constraints.

## Keep when

### Remote
`workplace_type == REMOTE`

AND India/global candidates are not explicitly excluded.

### Hybrid / Onsite
`workplace_type in [HYBRID, ONSITE]`

AND normalized location is Bengaluru/Bangalore.

## Reject when

- onsite outside Bengaluru
- hybrid outside Bengaluru
- remote geography explicitly excludes India
- role is fundamentally unrelated
- job is closed / expired
- application URL is invalid
- exact duplicate already exists
- candidate already applied to the same requisition

Store rejected jobs as `SKIPPED` with a `skip_reason`.

---

# 8. ATS / Match Scoring

Score each eligible job from `0-100`.

Use the actual resume as the source of candidate facts.

Suggested weighting:

- Role/title similarity: 20
- Required skill match: 30
- Experience alignment: 15
- Frontend/full-stack relevance: 10
- Preferred skill match: 10
- Domain relevance: 5
- Location/workplace eligibility: 10

Relevant domain boosts can include:

- healthcare
- clinical dashboards
- healthtech
- GenAI
- AI applications
- SaaS
- high-volume data platforms
- product engineering

Do not artificially inflate a score.

Do not treat every keyword as equally important.

Required skills should have more weight than preferred skills.

Return explanation:

```json
{
  "score": 84,
  "matched_skills": [],
  "missing_required_skills": [],
  "missing_preferred_skills": [],
  "experience_match": true,
  "title_match": true,
  "location_match": true,
  "reason": ""
}
```

---

# 9. Application Rule

**Automatically prepare/apply only when ATS score is strictly greater than 70%.**

Decision:

```text
ATS > 70    → eligible for application
ATS <= 70   → do not apply
```

Before applying, validate again:

- job is still open
- application URL works
- job has not already been applied to
- candidate meets location requirement
- resume exists
- required information is available
- no unsupported mandatory qualification invalidates the application

Never fabricate:

- years of experience
- employment history
- salary history
- degree
- certifications
- visa/work authorization
- security clearance
- skills
- identity information

If a mandatory application question cannot be truthfully answered from candidate data/configuration, mark:

`NEEDS_REVIEW`

instead of guessing.

If CAPTCHA, OTP, login approval, assessment, video response, legal declaration, or another human-only checkpoint appears:

`NEEDS_REVIEW`

and do not attempt to bypass it.

---

# 10. Resume Selection

Use the existing resume as the primary candidate source.

If multiple approved resumes are configured later:

1. choose the version with the strongest legitimate match
2. do not create fictional experience
3. do not alter dates, employers, titles, education, or factual metrics
4. resume tailoring may reorder/emphasize truthful existing content only

Save:

- resume_used
- resume_match_reason

for every application.

---

# 11. Application Tracking

The simple frontend is primarily an application tracker.

Statuses:

- DISCOVERED
- SCORED
- QUALIFIED
- READY_TO_APPLY
- NEEDS_REVIEW
- APPLIED
- INTERVIEW
- REJECTED
- OFFER
- SKIPPED
- FAILED

When an application succeeds, persist:

- job id
- company
- title
- application URL
- source
- ATS score
- resume used
- application method
- applied_at
- application confirmation / external application ID when available

Never mark a job as `APPLIED` unless submission actually completed.

---

# 12. Duplicate Protection

Prevent duplicate applications.

Use a fingerprint based on combinations of:

- company
- normalized title
- requisition / external job ID
- canonical application URL
- location

Before every submission call:

`already_applied(job)`

If true:

skip submission.

---

# 13. Outreach / Cold Email

Outreach should be job-linked and targeted.

Contact priority:

1. recruiter responsible for the job
2. talent acquisition
3. hiring manager
4. engineering manager / frontend manager
5. CTO / technical founder for appropriate smaller companies
6. founder only when role/company context makes outreach relevant

Avoid indiscriminate CEO/founder emailing.

Use the actual job and candidate experience to generate personalized outreach.

Attach or reference the appropriate resume.

Never invent a relationship or referral.

Track:

- contact_name
- role
- company
- email
- related_job_id
- outreach_status
- sent_at
- reply_status

Prevent repeated emails to the same person for the same job.

---

# 14. Scraping Schedule / Search Behavior

When a discovery run starts:

1. collect configured sources
2. find new jobs
3. resolve official job pages
4. normalize
5. filter by location/work mode
6. deduplicate
7. score
8. store jobs
9. prepare/apply jobs with ATS > 70
10. update application state
11. surface failures and NEEDS_REVIEW items

Prioritize newer postings.

If posted date is available, prioritize:

1. last 24 hours
2. last 3 days
3. last 7 days
4. older jobs afterward

Do not discard a strong eligible job solely because the posting date is unavailable.

---

# 15. Frontend Requirement

Keep frontend extremely simple.

One screen only.

Show summary:

- Total Jobs
- Qualified
- Applied
- Needs Review
- Interview

Table columns:

- Score
- Company
- Job Title
- Location
- Work Type
- Source
- Status
- Resume
- Applied Date
- Application Link

Filters:

- All
- Qualified
- Applied
- Needs Review
- Interview

Actions:

- Run Scraper
- Run Scoring
- Open Job
- Open Application
- Mark Applied / Review where needed

No heavy dashboard.
No charts.
No Redux unless already required.
No unnecessary design system.

---

# 16. Implementation Instructions for Codex

Before modifying code:

1. inspect the current project
2. inspect existing scraper/database/API/frontend modules
3. reuse existing architecture
4. do not create duplicate services
5. do not break working Scrapling setup
6. inspect tests
7. inspect dependency files

Then implement only what is missing.

After implementation run:

- Python import checks
- database tests
- scraper tests
- normalization tests
- scoring tests
- API tests
- frontend build

Fix errors before considering the task complete.

Do not silently suppress exceptions.

Use structured logging for:

- discovery run
- scraper source
- jobs found
- jobs filtered
- duplicate jobs
- score calculation
- application attempt
- application result
- NEEDS_REVIEW reason

---

# 17. Required Final Verification

At the end print a summary such as:

```text
JOB DISCOVERY RUN

Sources checked:         45
Jobs discovered:        430
Remote eligible:        125
Bengaluru hybrid:        36
Bengaluru onsite:        21
Duplicates removed:      87
Jobs scored:             95
ATS > 70:                31
Applications completed:  20
Needs review:             7
Failed:                   4
```

Also show the top qualified jobs.

Do not claim applications succeeded unless there is actual submission confirmation.

---

# Codex Execution Prompt

Use this file as the permanent requirements document for the job automation feature.

Read this entire file before implementing or running the automation.

Then:

1. inspect the current `ScraplingAgent` codebase
2. map existing modules against these requirements
3. implement missing functionality incrementally
4. preserve current working functionality
5. run tests after every major module change
6. start with job discovery + filtering + normalization + scoring
7. then integrate application tracking
8. then implement supported application flows
9. surface unsupported/human-required applications as `NEEDS_REVIEW`
10. keep the frontend to one simple job/application table

Do not rewrite the architecture unnecessarily.

The finished system must make job discovery broad, but application decisions strict:

**Remote worldwide + Bengaluru onsite/hybrid + role relevance + ATS > 70 + no duplicate applications.**
