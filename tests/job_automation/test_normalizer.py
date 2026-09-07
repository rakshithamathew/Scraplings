from datetime import datetime, timezone

from job_automation.normalizer import (
    NormalizedJob,
    generate_job_fingerprint,
    normalize_company,
    normalize_job,
    normalize_location,
    normalize_title,
    WorkplaceType,
)


def test_normalize_greenhouse_record_with_missing_fields() -> None:
    raw = {
        "id": 12345,
        "title": "  Senior   Python\nEngineer ",
        "location": {"name": " New York,   NY "},
        "content": " Build\n\n reliable   services. ",
        "absolute_url": " HTTPS://BOARDS.EXAMPLE.COM/jobs/12345/#details ",
        "source": " greenhouse ",
        "posted_at": None,
    }

    job = normalize_job(raw)

    assert isinstance(job, NormalizedJob)
    assert job.external_id == "12345"
    assert job.title == "Senior Python Engineer"
    assert job.location == "New York, NY"
    assert job.description == "Build reliable services."
    assert job.skills is None
    assert job.source_url == "https://boards.example.com/jobs/12345"
    assert job.application_url is None
    assert job.posted_at is None
    assert set(job.model_dump()) == {
        "external_id",
        "title",
        "company",
        "location",
        "workplace_type",
        "description",
        "skills",
        "required_skills",
        "preferred_skills",
        "minimum_experience",
        "maximum_experience",
        "is_open",
        "source",
        "source_url",
        "application_url",
        "posted_at",
    }


def test_normalize_lever_record() -> None:
    raw = {
        "id": "lever-42",
        "text": " Data   Engineer ",
        "companyName": " Example   Labs ",
        "categories": {"location": "Remote,   US"},
        "descriptionPlain": "Develop pipelines.",
        "skills": [" Python ", "SQL", "python"],
        "hostedUrl": "https://jobs.example.com/lever-42/",
        "applyUrl": "https://jobs.example.com/lever-42/apply/#form",
        "createdAt": 1_700_000_000_000,
    }

    job = normalize_job(raw, source="lever")

    assert job.title == "Data Engineer"
    assert job.company == "Example Labs"
    assert job.location == "Remote, US"
    assert job.skills == ["Python", "SQL"]
    assert job.source == "lever"
    assert job.source_url == "https://jobs.example.com/lever-42"
    assert job.application_url == "https://jobs.example.com/lever-42/apply"
    assert job.posted_at == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_normalize_workday_partial_record_and_stable_fingerprint() -> None:
    raw = {
        "jobReqId": "REQ-9",
        "jobTitle": " Platform Engineer ",
        "organization": " ACME\tCorp ",
        "locationsText": ["London", "Remote"],
        "jobDescription": None,
        "skillList": "Python; Kubernetes, SQL",
        "externalPath": "/careers/job/REQ-9/",
        "applicationUrl": "/careers/job/REQ-9/apply/",
        "postedOn": "not-a-date",
        "ignored_source_field": "safe to ignore",
    }

    job = normalize_job(raw, source="workday", base_url="https://careers.example.com/root/")

    assert job.external_id == "REQ-9"
    assert job.location == "London, Remote"
    assert job.workplace_type is WorkplaceType.REMOTE
    assert job.description is None
    assert job.skills == ["Python", "Kubernetes", "SQL"]
    assert job.source_url == "https://careers.example.com/careers/job/REQ-9"
    assert job.application_url == "https://careers.example.com/careers/job/REQ-9/apply"
    assert job.posted_at is None

    equivalent = normalize_job(
        {
            "title": "platform   engineer",
            "company": "acme corp",
            "application_url": "https://careers.example.com/careers/job/REQ-9/apply/",
        }
    )
    assert generate_job_fingerprint(job) == generate_job_fingerprint(equivalent)


def test_normalization_helpers_tolerate_nulls() -> None:
    assert normalize_title(None) is None
    assert normalize_company(None) is None
    assert normalize_location(None) is None
    assert normalize_job(None).model_dump() == {
        "external_id": None,
        "title": None,
        "company": None,
        "location": None,
        "workplace_type": WorkplaceType.UNKNOWN,
        "description": None,
        "skills": None,
        "required_skills": None,
        "preferred_skills": None,
        "minimum_experience": None,
        "maximum_experience": None,
        "is_open": None,
        "source": None,
        "source_url": None,
        "application_url": None,
        "posted_at": None,
    }
