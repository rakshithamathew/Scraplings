"""Safe, idempotent database initialization."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text

from .models import Base


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "jobs.db"
DEFAULT_DATABASE_URL = f"sqlite:///{DEFAULT_DATABASE_PATH.as_posix()}"


def _apply_additive_migrations(engine: Engine) -> None:
    """Add nullable columns introduced after the initial SQLite schema."""
    if engine.dialect.name != "sqlite" or not inspect(engine).has_table("jobs"):
        return
    columns = {column["name"] for column in inspect(engine).get_columns("jobs")}
    additions = {
        "recommended_resume": "TEXT",
        "application_status": "VARCHAR(32)",
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
