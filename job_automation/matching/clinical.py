"""Clinical software scope and CV-derived development-stack matching."""

from collections.abc import Iterable
import re

from job_automation.matching.requirements import matched_terms, normalize_for_matching
from job_automation.normalizer import NormalizedJob


# Generic healthcare, insurance and employee medical benefits are insufficient.
CLINICAL_SYSTEMS = re.compile(
    r"\b(?:ehr|emr|fhir|hl7|electronic (?:health|medical) records?|"
    r"(?:patient|medical|health) records?|"
    r"clinical (?:systems?|software|applications?|platforms?|workflows?|dashboards?|"
    r"informatics|decision support)|hospital information systems?|"
    r"patient (?:care|management|monitoring) (?:systems?|software|platforms?)|"
    r"telemedicine|telehealth|medical imaging|pacs|radiology information systems?)\b",
    re.I,
)
STACK_ALIASES = {
    "React": ("react", "react.js", "reactjs", "react js"),
    "Angular": ("angular", "angularjs", "angular.js"),
    "TypeScript": ("typescript", "type script", "t ypescript"),
    "JavaScript": ("javascript", "java script"),
    "Node.js": ("node.js", "nodejs", "node js"),
    "Express": ("express", "express.js", "expressjs"),
    "Python": ("python",),
    "SQL": ("sql", "mysql", "postgresql", "postgres"),
    "GraphQL": ("graphql",),
    "Redux": ("redux",),
    "Next.js": ("next.js", "nextjs", "next js"),
}
CORE_STACK = {"React", "Angular", "Node.js", "Python", "Next.js"}


def development_stack(skills: Iterable[str]) -> tuple[str, ...]:
    text = normalize_for_matching(" ".join(skills))
    return tuple(name for name, aliases in STACK_ALIASES.items() if matched_terms(aliases, text))


def clinical_match_reason(job: NormalizedJob, candidate_skills: Iterable[str]) -> str | None:
    """Fail closed without clinical-system evidence and two distinct CV stack matches."""
    if not job.description or not CLINICAL_SYSTEMS.search(job.description):
        return "Job description does not establish work on clinical healthcare systems"
    job_stack = set(development_stack((job.title or "", job.description, *(job.required_skills or []))))
    shared = job_stack.intersection(development_stack(candidate_skills))
    if len(shared) < 2 or not shared.intersection(CORE_STACK):
        return "Clinical role lacks a matching core framework/language and a second CV development skill"
    return None


def clinical_queries(skills: Iterable[str]) -> list[str]:
    """Search clinical products with technologies actually present in the active CV."""
    stack = development_stack(skills)
    primary = [skill for skill in stack if skill in CORE_STACK]
    return [f"{skill} {domain} developer" for skill in primary for domain in (
        "clinical software", "EHR", "patient records", "telehealth",
    )]
