from __future__ import annotations

import json

from .database import record_match_result
from .memory_clerk import PersistentMemoryClerk
from .models import MatchResult, JobStatus
from .tailor import AdaptiveDocumentTailor


def row_to_match_result(row) -> MatchResult:
    gaps = _parse_json_list(row["detected_gaps"])
    critical = [item for item in gaps if str(item).lower().startswith("critical hard skill gap")]
    return MatchResult(
        score=int(row["match_score"]),
        matched_requirements=[],
        detected_gaps=gaps,
        critical_gaps=critical,
        factual_evidence=[],
        job_summary="",
        raw_llm_output={},
    )


def approve_and_generate(row, resume_profile) -> str:
    clerk = PersistentMemoryClerk()
    tailor = AdaptiveDocumentTailor()
    job_id = int(row["id"])
    clerk.mark_approved(job_id)
    package = tailor.generate(
        resume_profile=resume_profile,
        job_title=row["job_title"],
        company=row["company"],
        job_description=row["job_description"],
        match_result=row_to_match_result(row),
    )
    out_dir = tailor.save(package, job_id=job_id, job_title=row["job_title"], company=row["company"])
    return str(out_dir)


def reject_job(job_id: int, reason: str = "") -> None:
    clerk = PersistentMemoryClerk()
    clerk.mark_rejected(job_id, reason=reason)


def mark_applied(job_id: int) -> None:
    clerk = PersistentMemoryClerk()
    clerk.mark_applied(job_id)


def _parse_json_list(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except Exception:
        pass
    return [str(raw_value)]