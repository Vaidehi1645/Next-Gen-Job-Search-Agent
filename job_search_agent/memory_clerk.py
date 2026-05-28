from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from .database import (
    get_job_by_url,
    initialize_database,
    job_exists,
    record_match_result,
    seed_job,
    set_job_state,
)
from .models import JobStatus, SearchCandidate


class PersistentMemoryClerk:
    """SQLite-backed memory clerk that prevents duplicate processing."""

    def __init__(self, db_path=None) -> None:
        self.db_path = db_path
        initialize_database(self.db_path)

    def remember_found_job(self, candidate: SearchCandidate) -> tuple[int, bool]:
        existing = get_job_by_url(candidate.url, self.db_path)
        if existing is not None:
            return int(existing["id"]), False
        job_id = seed_job(candidate, self.db_path)
        return job_id, True

    def has_been_processed(self, url: str) -> bool:
        existing = get_job_by_url(url, self.db_path)
        return existing is not None and existing["status"] in {
            JobStatus.REJECTED.value,
            JobStatus.APPLIED.value,
            JobStatus.APPROVED_BY_HUMAN.value,
            JobStatus.MATCHED.value,
            JobStatus.FOUND.value,
        }

    def mark_state(self, job_id: int, status: JobStatus, score: int | None = None, gaps: list[str] | None = None, reason: str = "") -> None:
        set_job_state(job_id, status, match_score=score, detected_gaps=gaps, rejection_reason=reason)

    def mark_found(self, candidate: SearchCandidate) -> int:
        job_id, _ = self.remember_found_job(candidate)
        self.mark_state(job_id, JobStatus.FOUND)
        return job_id

    def mark_matched(self, job_id: int, score: int, gaps: list[str]) -> None:
        record_match_result(job_id, score, gaps)

    def mark_approved(self, job_id: int) -> None:
        self.mark_state(job_id, JobStatus.APPROVED_BY_HUMAN)

    def mark_rejected(self, job_id: int, reason: str = "") -> None:
        self.mark_state(job_id, JobStatus.REJECTED, reason=reason)

    def mark_applied(self, job_id: int) -> None:
        self.mark_state(job_id, JobStatus.APPLIED)

    def should_skip(self, url: str) -> bool:
        existing = get_job_by_url(url, self.db_path)
        return existing is not None and existing["status"] == JobStatus.REJECTED.value
