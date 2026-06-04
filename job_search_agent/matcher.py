from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import json
import re
from typing import Iterable

from langchain_core.messages import HumanMessage, SystemMessage

from .llm import get_reasoning_llm
from .models import MatchResult, ResumeProfile
from .resume_store import ResumeVectorStore
from .logging_config import logger


HARD_SKILL_PATTERNS = {
    "python": r"\bpython\b",
    "sql": r"\bsql\b",
    "sqlite": r"\bsqlite\b",
    "chromadb": r"\bchroma(db)?\b",
    "langgraph": r"\blanggraph\b",
    "crewai": r"\bcrewai\b",
    "ollama": r"\bollama\b",
    "playwright": r"\bplaywright\b",
    "kubernetes": r"\bkubernetes\b|\bk8s\b",
    "docker": r"\bdocker\b",
    "aws": r"\baws\b|amazon web services",
    "gcp": r"\bgcp\b|google cloud",
    "azure": r"\bazure\b|microsoft azure",
    "fastapi": r"\bfastapi\b",
    "django": r"\bdjango\b",
    "flask": r"\bflask\b",
    "git": r"\bgit\b",
    "linux": r"\blinux\b",
    "llm": r"\bllm\b|large language model",
    "rag": r"\brag\b|retrieval[- ]augmented",
    "vector embeddings": r"vector embeddings?|embeddings?",
}


class TruthCheckedMatcher:
    def __init__(self, llm=None, resume_store: ResumeVectorStore | None = None) -> None:
        self.llm = llm or get_reasoning_llm()
        self.resume_store = resume_store or ResumeVectorStore()
        self._llm_enabled = True

    def match(self, resume_profile: ResumeProfile, job_title: str, company: str, job_description: str) -> MatchResult:
        logger.info("[Matcher] Reviewing factual overlap for %s at %s", job_title, company)
        relevant_resume_chunks = self.resume_store.query(job_description, top_k=5)
        extracted_requirements = self._extract_requirements(job_description)
        resume_text = resume_profile.raw_text.lower()
        resume_skills = {skill.lower() for skill in resume_profile.skills}
        matched: list[str] = []
        gaps: list[str] = []
        critical_gaps: list[str] = []
        evidence: list[str] = []

        for requirement in extracted_requirements:
            skill = requirement["skill"]
            required = requirement["required"]
            min_years = requirement.get("min_years")
            required_pattern = HARD_SKILL_PATTERNS.get(skill, re.escape(skill))
            present = self._skill_present(skill, required_pattern, resume_text, resume_skills)
            if present:
                matched.append(skill)
                evidence.append(self._find_resume_evidence(skill, relevant_resume_chunks, resume_text))
                if min_years is not None:
                    resume_years = resume_profile.experience_years.get(skill, self._infer_years_for_skill(resume_text, skill))
                    if resume_years < min_years:
                        gaps.append(f"{skill}: requires {min_years} years, resume evidence only supports {resume_years} years")
            else:
                label = f"{skill} is missing from the master resume"
                if required:
                    critical_gaps.append(f"Critical Hard Skill Gap: {label}")
                else:
                    gaps.append(label)

        hard_skill_count = sum(1 for requirement in extracted_requirements if requirement["required"])
        matched_count = len(matched)
        score = self._score_job(matched_count, hard_skill_count, len(gaps), len(critical_gaps))
        prompt_payload = {
            "job_title": job_title,
            "company": company,
            "requirements": extracted_requirements,
            "resume_chunks": relevant_resume_chunks,
            "resume_skills": sorted(resume_skills),
        }
        llm_summary = self._summarize_match(prompt_payload)
        if llm_summary.get("score") is not None:
            score = min(100, max(1, int(llm_summary["score"])))
        detected_gaps = critical_gaps + gaps
        if not detected_gaps and llm_summary.get("gaps"):
            detected_gaps = [str(item) for item in llm_summary["gaps"]]
        return MatchResult(
            score=score,
            matched_requirements=matched,
            detected_gaps=detected_gaps,
            critical_gaps=critical_gaps,
            factual_evidence=[item for item in evidence if item],
            job_summary=llm_summary.get("summary", ""),
            raw_llm_output=llm_summary,
        )

    def _extract_requirements(self, job_description: str) -> list[dict[str, object]]:
        lower_description = job_description.lower()
        requirements: list[dict[str, object]] = []
        for skill, pattern in HARD_SKILL_PATTERNS.items():
            if re.search(pattern, lower_description, re.IGNORECASE):
                requirements.append(
                    {
                        "skill": skill,
                        "required": self._is_required(lower_description, skill),
                        "min_years": self._extract_min_years(lower_description, skill),
                    }
                )
        if not requirements:
            requirements.append({"skill": "general alignment", "required": False, "min_years": None})
        return self._dedupe_requirements(requirements)

    @staticmethod
    def _dedupe_requirements(requirements: list[dict[str, object]]) -> list[dict[str, object]]:
        seen: set[str] = set()
        output: list[dict[str, object]] = []
        for item in requirements:
            skill = str(item["skill"])
            if skill in seen:
                continue
            seen.add(skill)
            output.append(item)
        return output

    @staticmethod
    def _is_required(job_description: str, skill: str) -> bool:
        anchor_positions = [
            job_description.find("required"),
            job_description.find("must have"),
            job_description.find("minimum"),
            job_description.find("experience"),
        ]
        skill_position = job_description.find(skill)
        return skill_position != -1 and any(position != -1 and position <= skill_position for position in anchor_positions)

    @staticmethod
    def _extract_min_years(job_description: str, skill: str) -> int | None:
        patterns = [
            rf"(\d+)\+?\s+years?[^.\n]{{0,80}}{re.escape(skill)}",
            rf"{re.escape(skill)}[^.\n]{{0,80}}(\d+)\+?\s+years?",
        ]
        for pattern in patterns:
            match = re.search(pattern, job_description, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _skill_present(skill: str, pattern: str, resume_text: str, resume_skills: set[str]) -> bool:
        return skill in resume_skills or re.search(pattern, resume_text, re.IGNORECASE) is not None

    @staticmethod
    def _infer_years_for_skill(resume_text: str, skill: str) -> float:
        pattern = rf"(\d+(?:\.\d+)?)\+?\s+years?[^.\n]{{0,100}}{re.escape(skill)}"
        match = re.search(pattern, resume_text, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return 0.0

    @staticmethod
    def _find_resume_evidence(skill: str, relevant_resume_chunks: list[str], resume_text: str) -> str:
        for chunk in relevant_resume_chunks:
            if skill.lower() in chunk.lower():
                return chunk.strip().replace("\n", " ")[:240]
        match = re.search(rf".{0,80}{re.escape(skill)}.{0,120}", resume_text, re.IGNORECASE | re.DOTALL)
        return match.group(0).replace("\n", " ").strip()[:240] if match else ""

    @staticmethod
    def _score_job(matched_count: int, hard_skill_count: int, gap_count: int, critical_gap_count: int) -> int:
        if hard_skill_count == 0:
            return 25
        base = 20 + (matched_count / hard_skill_count) * 60
        penalty = (gap_count * 4) + (critical_gap_count * 14)
        return int(max(1, min(100, round(base - penalty))))

    def _summarize_match(self, payload: dict[str, object]) -> dict[str, object]:
        if not self._llm_enabled:
            return {}
        messages = [
            SystemMessage(
                content=(
                    "You are the Truth-Checked Matcher. Produce only JSON with keys summary, score, gaps, "
                    "matched, and critical_gaps. Use only the provided resume chunks and skill list. "
                    "Never infer experience that is not explicitly supported."
                )
            ),
            HumanMessage(
                content=json.dumps(payload, ensure_ascii=True),
            ),
        ]
        try:
            response = self.llm.invoke(messages)
            content = getattr(response, "content", str(response))
            parsed = json.loads(self._extract_json_block(content))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.exception("[Matcher] Local LLM summary failed, falling back to deterministic result")
            self._llm_enabled = False
        return {}

    @staticmethod
    def _extract_json_block(text: str) -> str:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return match.group(0) if match else "{}"
