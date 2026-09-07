"""SQLite persistence for the job-automation application."""

from .init_db import DEFAULT_DATABASE_PATH, DEFAULT_DATABASE_URL, create_database_engine, initialize_database
from .models import ActiveResume, ApplicationMethod, ApplicationStatus, Base, Job, JobStatus, OutreachStatus
from .repository import DuplicateJobError, JobRepository
from job_automation.normalizer import WorkplaceType

__all__ = [
    "Base",
    "ActiveResume",
    "ApplicationMethod",
    "ApplicationStatus",
    "DEFAULT_DATABASE_PATH",
    "DEFAULT_DATABASE_URL",
    "DuplicateJobError",
    "Job",
    "JobRepository",
    "JobStatus",
    "OutreachStatus",
    "WorkplaceType",
    "create_database_engine",
    "initialize_database",
]
