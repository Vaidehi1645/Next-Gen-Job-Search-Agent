from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from typing import Iterator

from .config import SETTINGS
from .models import JobStatus, SearchCandidate
from .logging_config import logger


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_title TEXT NOT NULL,
    company TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    job_description TEXT NOT NULL DEFAULT '',
    match_score INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'FOUND',
    detected_gaps TEXT NOT NULL DEFAULT '[]',
    date_found TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'duckduckgo',
    is_direct_employer INTEGER NOT NULL DEFAULT 1,
    rejection_reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_score ON jobs(status, match_score DESC, date_found DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_url ON jobs(url);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    resume_path TEXT NOT NULL DEFAULT '',
    github TEXT NOT NULL DEFAULT '',
    linkedin TEXT NOT NULL DEFAULT '',
    twitter TEXT NOT NULL DEFAULT '',
    other_links TEXT NOT NULL DEFAULT '[]',
    years_experience INTEGER NOT NULL DEFAULT 0,
    desired_role TEXT NOT NULL DEFAULT '',
    desired_stack TEXT NOT NULL DEFAULT '',
    locations TEXT NOT NULL DEFAULT '[]',
    declaration_date TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_profiles_updated_at ON profiles(updated_at DESC);
"""


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or SETTINGS.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def initialize_database(db_path: Path | None = None) -> None:
    with connect(db_path) as connection:
        connection.executescript(SCHEMA)


def seed_job(candidate: SearchCandidate, db_path: Path | None = None) -> int:
    initialize_database(db_path)
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO jobs (
                job_title, company, url, job_description, match_score, status,
                detected_gaps, date_found, source, is_direct_employer, rejection_reason, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                job_title=excluded.job_title,
                company=excluded.company,
                job_description=excluded.job_description,
                source=excluded.source,
                is_direct_employer=excluded.is_direct_employer,
                rejection_reason=excluded.rejection_reason,
                updated_at=excluded.updated_at
            """,
            (
                candidate.job_title,
                candidate.company,
                candidate.url,
                candidate.job_description,
                0,
                JobStatus.FOUND.value,
                json.dumps([], ensure_ascii=True),
                candidate.date_found,
                candidate.source,
                1 if candidate.is_direct_employer else 0,
                candidate.rejection_reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        row = connection.execute("SELECT id FROM jobs WHERE url = ?", (candidate.url,)).fetchone()
        return int(row["id"])


def update_job_fields(job_id: int, **fields: object) -> None:
    if not fields:
        return
    allowed = {
        "job_title",
        "company",
        "url",
        "job_description",
        "match_score",
        "status",
        "detected_gaps",
        "date_found",
        "source",
        "is_direct_employer",
        "rejection_reason",
    }
    filtered = {key: value for key, value in fields.items() if key in allowed}
    if not filtered:
        return
    filtered["updated_at"] = datetime.now(timezone.utc).isoformat()
    assignments = ", ".join(f"{column} = ?" for column in filtered)
    values = []
    for value in filtered.values():
        if isinstance(value, (list, dict)):
            values.append(json.dumps(value, ensure_ascii=True))
        else:
            values.append(value)
    values.append(job_id)
    with connect() as connection:
        connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)


def set_job_state(job_id: int, status: JobStatus | str, match_score: int | None = None, detected_gaps: list[str] | None = None, rejection_reason: str = "") -> None:
    payload: dict[str, object] = {"status": str(status)}
    if match_score is not None:
        payload["match_score"] = int(match_score)
    if detected_gaps is not None:
        payload["detected_gaps"] = detected_gaps
    if rejection_reason:
        payload["rejection_reason"] = rejection_reason
    update_job_fields(job_id, **payload)


def job_exists(url: str, db_path: Path | None = None) -> bool:
    initialize_database(db_path)
    with connect(db_path) as connection:
        row = connection.execute("SELECT 1 FROM jobs WHERE url = ? LIMIT 1", (url,)).fetchone()
        return row is not None


def get_job_by_id(job_id: int, db_path: Path | None = None) -> sqlite3.Row | None:
    initialize_database(db_path)
    with connect(db_path) as connection:
        return connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_job_by_url(url: str, db_path: Path | None = None) -> sqlite3.Row | None:
    initialize_database(db_path)
    with connect(db_path) as connection:
        return connection.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()


def fetch_top_jobs(limit: int = 3, db_path: Path | None = None) -> list[sqlite3.Row]:
    initialize_database(db_path)
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status NOT IN ('REJECTED', 'APPLIED')
            ORDER BY match_score DESC, date_found DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(rows)


def fetch_unprocessed_candidates(limit: int = 50, db_path: Path | None = None) -> list[sqlite3.Row]:
    initialize_database(db_path)
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM jobs
            WHERE status = 'FOUND' OR status = 'MATCHED'
            ORDER BY date_found DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(rows)


def record_match_result(job_id: int, score: int, detected_gaps: list[str], status: JobStatus = JobStatus.MATCHED) -> None:
    set_job_state(job_id, status, match_score=score, detected_gaps=detected_gaps)


def serialize_job(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def save_profile(
    name: str | None = None,
    resume_path: str | None = None,
    github: str | None = None,
    linkedin: str | None = None,
    twitter: str | None = None,
    other_links: list[str] | None = None,
    years_experience: int | None = None,
    desired_role: str | None = None,
    desired_stack: str | None = None,
    locations: list[str] | None = None,
    declaration_date: str | None = None,
    notes: str | None = None,
    db_path: Path | None = None,
) -> int:
    initialize_database(db_path)
    other_links = other_links or []
    locations = locations or []
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO profiles (
                name, resume_path, github, linkedin, twitter, other_links, years_experience,
                desired_role, desired_stack, locations, declaration_date, notes, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name or "",
                resume_path or "",
                github or "",
                linkedin or "",
                twitter or "",
                json.dumps(other_links, ensure_ascii=True),
                int(years_experience or 0),
                desired_role or "",
                desired_stack or "",
                json.dumps(locations, ensure_ascii=True),
                declaration_date or "",
                notes or "",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        row = connection.execute("SELECT id FROM profiles ORDER BY updated_at DESC LIMIT 1").fetchone()
        return int(row["id"])


def load_latest_profile(db_path: Path | None = None) -> dict[str, object] | None:
    initialize_database(db_path)
    with connect(db_path) as connection:
        row = connection.execute("SELECT * FROM profiles ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            return None
        result = {key: row[key] for key in row.keys()}
        # parse JSON fields
        try:
            import json as _json

            result["other_links"] = _json.loads(result.get("other_links", "[]"))
        except Exception:
            logger.exception("Failed to parse other_links for latest profile")
            result["other_links"] = []
        try:
            import json as _json

            result["locations"] = _json.loads(result.get("locations", "[]"))
        except Exception:
            logger.exception("Failed to parse locations for latest profile")
            result["locations"] = []
        return result
