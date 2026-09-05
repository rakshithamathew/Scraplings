"""Normalization of source-specific records into one job schema."""

from .jobs import (
    NormalizedJob,
    generate_job_fingerprint,
    normalize_company,
    normalize_job,
    normalize_location,
    normalize_title,
)

__all__ = [
    "NormalizedJob",
    "generate_job_fingerprint",
    "normalize_company",
    "normalize_job",
    "normalize_location",
    "normalize_title",
]
