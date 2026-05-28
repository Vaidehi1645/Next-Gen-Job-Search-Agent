from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    FOUND = "FOUND"
    MATCHED = "MATCHED"
    APPROVED_BY_HUMAN = "APPROVED_BY_HUMAN"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


@dataclass(slots=True)
class SearchCandidate:
    job_title: str
    company: str
    url: str
    job_description: str
    date_found: str
    source: str = "duckduckgo"
    is_direct_employer: bool = True
    rejection_reason: str = ""


@dataclass(slots=True)
class ResumeProfile:
    raw_text: str
    sections: dict[str, str] = field(default_factory=dict)
    skills: set[str] = field(default_factory=set)
    experience_years: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MatchResult:
    score: int
    matched_requirements: list[str] = field(default_factory=list)
    detected_gaps: list[str] = field(default_factory=list)
    critical_gaps: list[str] = field(default_factory=list)
    factual_evidence: list[str] = field(default_factory=list)
    job_summary: str = ""
    raw_llm_output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TailoredPackage:
    resume_bullets: list[str] = field(default_factory=list)
    networking_message: str = ""
    subject_line: str = ""
    cover_note: str = ""
