"""Safe, idempotent database initialization."""

from __future__ import annotations

from pathlib import Path
import re

from sqlalchemy import Engine, MetaData, create_engine, inspect, text
from sqlalchemy.schema import CreateTable

from .models import Base, Job


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "jobs.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


def _apply_additive_migrations(engine: Engine) -> None:
    """Add nullable columns introduced after the initial SQLite schema."""
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("jobs"):
        return
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    additions = {
        "description_complete": "BOOLEAN",
        "recommended_resume": "TEXT",
        "application_status": "VARCHAR(32)",
        "workplace_type": "VARCHAR(16)",
        "required_skills": "JSON",
        "preferred_skills": "JSON",
        "minimum_experience": "INTEGER",
        "maximum_experience": "INTEGER",
        "is_open": "BOOLEAN",
        "skip_reason": "TEXT",
        "resume_match_reason": "TEXT",
        "application_confirmation": "TEXT",
        "external_application_id": "VARCHAR(255)",
        "contact_role": "VARCHAR(255)",
        "outreach_sent_at": "DATETIME",
        "reply_status": "VARCHAR(100)",
        "review_reason": "TEXT",
        "failure_reason": "TEXT",
        "matched_skills": "JSON",
        "missing_skills": "JSON",
        "score_reason": "TEXT",
    }
    with engine.begin() as connection:
        for name, column_type in additions.items():
            if name in columns:
                continue
            # Names and types are internal constants, never user-provided values.
            connection.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}"))
        if "outreach_status" in columns:
            connection.execute(
                text("UPDATE jobs SET outreach_status = 'NOT_STARTED' WHERE outreach_status IS NULL")
            )
        if "workplace_type" in columns or "workplace_type" in additions:
            connection.execute(
                text("UPDATE jobs SET workplace_type = 'UNKNOWN' WHERE workplace_type IS NULL")
            )
        for json_column in ("required_skills", "preferred_skills", "matched_skills", "missing_skills"):
            connection.execute(
                text(f"UPDATE jobs SET {json_column} = '[]' WHERE {json_column} IS NULL")
            )
    _migrate_job_status_constraint(engine)


def _migrate_job_status_constraint(engine: Engine) -> None:
    """Map legacy lifecycle values and rebuild the SQLite enum constraint once."""
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    if "status" not in columns:
        return
    with engine.connect() as connection:
        create_sql = connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'")
        )
    normalized_sql = (create_sql or "").upper()
    status_constraint = re.search(
        r"\bJOB_STATUS\s+CHECK\s*\(\s*STATUS\s+IN\s*\(([^)]*)\)\s*\)", normalized_sql
    )
    legacy_statuses = (
        "SCORED",
        "READY_TO_APPLY",
        "NEEDS_REVIEW",
        "FAILED",
        "REJECTED",
        "INTERVIEW",
        "OFFER",
    )
    if not status_constraint or not any(
        f"'{status}'" in status_constraint.group(1) for status in legacy_statuses
    ):
        return

    temporary_name = "jobs_status_migration"
    temporary_metadata = MetaData()
    temporary_table = Job.__table__.to_metadata(temporary_metadata, name=temporary_name)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE jobs SET status = CASE "
                "WHEN status = 'APPLIED' THEN 'APPLIED' "
                "WHEN status = 'SKIPPED' THEN 'SKIPPED' "
                "WHEN status IN ('REJECTED', 'INTERVIEW', 'OFFER') "
                "AND (applied_at IS NOT NULL OR application_confirmation IS NOT NULL) THEN 'APPLIED' "
                "WHEN match_score > 70 THEN 'QUALIFIED' "
                "ELSE 'DISCOVERED' END"
            )
        )
        connection.execute(
            text("UPDATE jobs SET application_status = NULL WHERE status != 'APPLIED'")
        )
        connection.execute(text(f'DROP TABLE IF EXISTS "{temporary_name}"'))
        connection.execute(CreateTable(temporary_table))
        existing_columns = {column["name"] for column in inspect(connection).get_columns("jobs")}
        copy_columns = [column.name for column in Job.__table__.columns if column.name in existing_columns]
        column_sql = ", ".join(f'"{name}"' for name in copy_columns)
        connection.execute(
            text(
                f'INSERT INTO "{temporary_name}" ({column_sql}) '
                f'SELECT {column_sql} FROM "jobs"'
            )
        )
        old_count = connection.scalar(text('SELECT COUNT(*) FROM "jobs"'))
        new_count = connection.scalar(text(f'SELECT COUNT(*) FROM "{temporary_name}"'))
        if old_count != new_count:
            raise RuntimeError("Job-status migration row-count verification failed")
        connection.execute(text('DROP TABLE "jobs"'))
        connection.execute(text(f'ALTER TABLE "{temporary_name}" RENAME TO "jobs"'))
        for index in Job.__table__.indexes:
            index.create(connection, checkfirst=True)


def create_database_engine(database_url: str = DEFAULT_DATABASE_URL, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy engine and ensure a file-backed SQLite parent exists."""
    if database_url == DEFAULT_DATABASE_URL:
        DEFAULT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(database_url, echo=echo)


def initialize_database(
    database_url: str = DEFAULT_DATABASE_URL,
    *,
    engine: Engine | None = None,
    echo: bool = False,
) -> Engine:
    """Create missing tables without dropping or recreating existing data."""
    database_engine = engine or create_database_engine(database_url, echo=echo)
    Base.metadata.create_all(database_engine)
    _apply_additive_migrations(database_engine)
    return database_engine


if __name__ == "__main__":
    initialize_database()
    print(f"Database ready: {DEFAULT_DATABASE_PATH}")
