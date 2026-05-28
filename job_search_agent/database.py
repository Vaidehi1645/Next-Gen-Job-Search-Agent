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
