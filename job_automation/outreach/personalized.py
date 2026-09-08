"""Generate and store factual outreach from the active CV and complete job details."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from job_automation.database import DEFAULT_DATABASE_URL, JobRepository, JobStatus, OutreachStatus, initialize_database
from job_automation.database.models import OutreachMessage
from job_automation.outreach.contact_repository import ContactRepository
from job_automation.resume.resume_parser import parse_resume

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_VERSION = 'cv-grounded-v1'


def input_fingerprint(job, resume_hash, contact_snapshot, missing):
    payload = {'version': GENERATOR_VERSION, 'cv': resume_hash,
        'description': job.description, 'description_complete': job.description_complete,
        'company': job.company, 'title': job.title, 'contact': contact_snapshot, 'missing': missing}
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def clean(value):
    # Repair known PDF extraction splits, without adding facts.
    for broken, fixed in (('T ypeScript', 'TypeScript'), ('T ailwind', 'Tailwind'), ('T echnical', 'Technical')):
        value = value.replace(broken, fixed)
    return ' '.join(value.split())


def present(term, text):
    return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', text, re.I))


def words(value):
    return len(value.split())


def joined(values):
    return ', '.join(values[:-1]) + ' and ' + values[-1] if len(values) > 1 else values[0]


def evidence_blocks(profile):
    """Rejoin wrapped CV bullets; retain their wording and full metrics."""
    starts = re.compile(r'^(?:Implemented|Optimized|Enhanced|Developed|Built|Ran|Achieved|Architected|Redesigned|Re-architected|Resolved|Reduced|Converted|Led|Created|Designed|Delivered)\b', re.I)
    blocks = []
    for section in (profile.work_experience, profile.projects):
        current = ''
        for raw in section:
            line = clean(raw)
            if line.startswith('Tech Stack:'):
                if current:
                    blocks.append(current)
                    current = ''
                continue
            is_start = bool(starts.search(line)) or (':' in line and not line.startswith('Tech Stack:'))
            if is_start:
                if current:
                    blocks.append(current)
                current = line
            elif current and (line[:1].islower() or line.startswith(('by ', '5,000', 'records ', 'query '))):
                current += ' ' + line
            else:
                if current:
                    blocks.append(current)
                current = ''
        if current:
            blocks.append(current)
    return [block.rstrip('.') for block in blocks if 8 <= words(block) <= 50]


def draft_text(job, contact, profile):
    cv = clean(profile.resume_text)
    lines = [clean(line) for line in profile.resume_text.splitlines() if clean(line)]
    name = lines[0] if lines else ''
    if not name or not 2 <= words(name) <= 6 or re.search(r'[@\d]|resume|curriculum|engineer|developer', name, re.I):
        raise ValueError('Candidate name could not be verified from the CV header')
    experience = re.search(r'\b(\d{1,2}(?:\.\d+)?\s*\+?)\s+years?\s+(?:of\s+)?(?:professional\s+|relevant\s+|industry\s+)?experience\b', cv, re.I)
    if not experience:
        raise ValueError('CV does not explicitly state years of experience')
    years = experience.group(1).replace(' ', '')
    jd = clean(job.description or '')
    skills = []
    for raw in (*profile.technologies, *profile.skills):
        skill = clean(raw).split(':')[-1].strip()
        if skill and words(skill) <= 4 and present(skill, cv) and present(skill, jd) and skill.lower() not in {s.lower() for s in skills}:
            # Avoid React + React.js taking two slots for the same skill.
            if any(skill.lower().removesuffix('.js') == s.lower().removesuffix('.js') for s in skills):
                continue
            skills.append(skill)
    skills.sort(key=lambda skill: (-len(re.findall(re.escape(skill), jd, re.I)), -len(skill)))
    skills = skills[:4]
    if not skills:
        raise ValueError('No CV-supported skills match the complete JD')
    jd_tokens = set(re.findall(r'[a-z]{4,}', jd.lower())) - {'with', 'from', 'that', 'this', 'have', 'will', 'your', 'team', 'work', 'role', 'years', 'experience', 'company'}
    def score(block):
        return sum(5 for skill in skills if present(skill, block)) + len(set(re.findall(r'[a-z]{4,}', block.lower())) & jd_tokens)
    blocks = sorted(evidence_blocks(profile), key=score, reverse=True)
    if not blocks or score(blocks[0]) == 0:
        raise ValueError('No relevant project or work example could be verified from the CV')
    example = blocks[0]
    # Every copied candidate claim must occur in the actual CV text.
    if clean(example).casefold() not in cv.casefold():
        raise ValueError('Selected experience excerpt could not be verified against CV text')
    skills_text = joined(skills)
    title, company, recipient = clean(job.title), clean(job.company), clean(contact.name)
    paragraphs = [
        f'Hi {recipient},',
        f"I'm {name}, with {years} years of experience, and I'm interested in the {title} position at {company}.",
        f'The clearest overlap between my CV and your job description is {skills_text}. These are skills documented in my background and called for in this role, so I would welcome the opportunity to discuss how I could apply them to your team\'s requirements.',
        f'A relevant example from my CV is: “{example}.”',
        f'This experience is why the position caught my attention. I would be interested in discussing the responsibilities in your job description, sharing more context about this work, and learning which priorities are most important for the person joining {company}.',
        'Would you please consider my profile for the position? I would appreciate the opportunity to discuss whether my background fits what you are looking for. Thank you for your time and consideration.',
        f'Best regards,\n{name}',
    ]
    body = '\n\n'.join(paragraphs)
    # Adjust connective prose only; never truncate or expand a CV claim to meet length.
    if words(body) > 200:
        paragraphs[4] = 'I would welcome a discussion about how this experience relates to the responsibilities and priorities of the position.'
        body = '\n\n'.join(paragraphs)
    if words(body) < 150:
        paragraphs.insert(-1, 'I would be glad to provide more context on the work described in my CV and answer questions about its relevance to the role.')
        body = '\n\n'.join(paragraphs)
    linkedin = (f"Hi {recipient}, I'm {name}, with {years} years of experience. I'm interested in the {title} role at {company}. "
        f"My CV includes {joined(skills[:2])}, matching the requirements in your JD. Would you please consider my profile? "
        'I would welcome a brief conversation about how my background fits the position. Thank you for your time.')
    if not 150 <= words(body) <= 200 or not 50 <= words(linkedin) <= 80:
        raise ValueError('Draft length needs review; factual content was not truncated')
    return {
        'email_subject': f'{title} at {company} | {name}',
        'email_body': body,
        'linkedin_message': linkedin,
        'evidence': {'candidate_name': name, 'experience': experience.group(0), 'matched_skills': skills,
            'cv_excerpt': example, 'jd_skill_matches': skills, 'job_description_sha256': sha256(jd.encode()).hexdigest(),
            'generator_version': GENERATOR_VERSION},
    }


@dataclass
class GenerationSummary:
    qualified_jobs: int = 0
    generated: int = 0
    unchanged: int = 0
    waiting_input: int = 0


class PersonalizedOutreachService:
    def __init__(self, repository, *, project_root=PROJECT_ROOT, prefer_email_contact=False):
        self.repository = repository
        self.project_root = Path(project_root)
        self.prefer_email_contact = prefer_email_contact

    def list(self, job_id=None):
        with Session(self.repository.engine) as session:
            query = select(OutreachMessage).order_by(OutreachMessage.job_id)
            if job_id is not None:
                query = query.where(OutreachMessage.job_id == job_id)
            return list(session.scalars(query))

    def run(self, job_id=None, *, contact_id=None, allow_applied=False):
        jobs = self.repository.get_jobs(status=JobStatus.QUALIFIED)
        if job_id is not None:
            job = self.repository.get_job(job_id)
            allowed = {JobStatus.QUALIFIED, JobStatus.APPLIED} if allow_applied else {JobStatus.QUALIFIED}
            if job is None or job.status not in allowed:
                raise ValueError('Outreach generation requires a qualified job')
            jobs = [job]
        active = self.repository.get_active_resume()
        profile, cv_error = None, None
        if active is None:
            cv_error = 'Active uploaded CV is missing'
        else:
            path = (self.project_root / active.path).resolve()
            try:
                if sha256(path.read_bytes()).hexdigest() != active.sha256:
                    raise ValueError('Active CV file does not match its uploaded hash')
                # Reparse the actual active file; never trust stale/inferred cached claims.
                profile = parse_resume(path)
            except (ValueError, OSError, RuntimeError) as error:
                cv_error = str(error)
        summary = GenerationSummary(qualified_jobs=len(jobs))
        contacts = ContactRepository(self.repository.engine)
        for job in jobs:
            candidates = contacts.list(job.id)
            if contact_id is not None:
                candidates = [contact for contact in candidates if contact.id == contact_id]
            if self.prefer_email_contact:
                candidates = sorted(candidates, key=lambda contact: (not bool(contact.work_email), contact.priority))
            contact = next((c for c in candidates if clean(c.company).casefold() == clean(job.company).casefold()), None)
            missing = [cv_error] if cv_error else []
            if not job.description or job.description_complete is not True:
                missing.append('Complete job description has not been collected and verified')
            if contact is None:
                missing.append('Relevant public contact information is missing')
            snapshot = {key: getattr(contact, key) for key in ('id', 'name', 'title', 'company', 'work_email', 'linkedin_url', 'source')} if contact else {}
            signature = input_fingerprint(job, active.sha256 if active else None, snapshot, missing)
            values = {}
            if not missing:
                try:
                    values = draft_text(job, contact, profile)
                except ValueError as error:
                    missing.append(str(error))
            status = 'WAITING_INPUT' if missing else 'DRAFTED'
            with Session(self.repository.engine) as session, session.begin():
                existing = session.get(OutreachMessage, job.id)
                if existing and existing.input_hash == signature and existing.status == status:
                    summary.unchanged += 1
                    summary.waiting_input += bool(missing)
                    continue
                record = existing or OutreachMessage(job_id=job.id)
                record.contact_id = contact.id if contact else None
                record.status, record.input_hash = status, signature
                record.resume_sha256 = active.sha256 if active else None
                record.contact_snapshot = snapshot
                record.missing_inputs = missing
                record.email_subject = values.get('email_subject')
                record.email_body = values.get('email_body')
                record.linkedin_message = values.get('linkedin_message')
                record.evidence = values.get('evidence', {})
                session.add(record)
            if missing:
                summary.waiting_input += 1
            else:
                summary.generated += 1
                if job.outreach_status in {OutreachStatus.NOT_STARTED, OutreachStatus.CONTACT_FOUND, OutreachStatus.DRAFTED}:
                    self.repository.update_outreach_status(job.id, OutreachStatus.DRAFTED)
        return summary


def serialize_message(record):
    return {key: getattr(record, key) for key in ('job_id', 'contact_id', 'status', 'email_subject', 'email_body',
        'linkedin_message', 'resume_sha256', 'missing_inputs', 'evidence', 'contact_snapshot')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=DEFAULT_DATABASE_URL)
    parser.add_argument('--job-id', type=int)
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    engine = initialize_database(args.database_url)
    try:
        service = PersonalizedOutreachService(JobRepository(engine))
        if args.list:
            print(json.dumps([serialize_message(row) for row in service.list(args.job_id)], indent=2))
        else:
            print(json.dumps(asdict(service.run(args.job_id)), indent=2))
        return 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
