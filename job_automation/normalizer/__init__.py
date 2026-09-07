"""Normalization of source-specific records into one job schema."""

from .jobs import (
    NormalizedJob,
    WorkplaceType,
    generate_job_fingerprint,
    normalize_company,
    normalize_job,
    normalize_location,
    normalize_title,
    normalize_workplace_type,
)

__all__ = [
    "NormalizedJob",
    "WorkplaceType",
    "generate_job_fingerprint",
    "normalize_company",
    "normalize_job",
    "normalize_location",
    "normalize_title",
    "normalize_workplace_type",
]
