from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
import re

from langchain_core.messages import HumanMessage, SystemMessage

from .config import SETTINGS
from .llm import get_reasoning_llm
from .models import MatchResult, ResumeProfile, TailoredPackage
from .logging_config import logger


class AdaptiveDocumentTailor:
    def __init__(self, llm=None) -> None:
        self.llm = llm or get_reasoning_llm()
        self._llm_enabled = True

    def generate(self, resume_profile: ResumeProfile, job_title: str, company: str, job_description: str, match_result: MatchResult) -> TailoredPackage:
        logger.info("[Tailor] Generating materials for %s at %s", job_title, company)
        payload = {
            "job_title": job_title,
            "company": company,
            "job_description": job_description,
            "resume_text": resume_profile.raw_text,
            "resume_skills": sorted(resume_profile.skills),
            "matched_requirements": match_result.matched_requirements,
            "factual_evidence": match_result.factual_evidence,
        }
        structured = self._generate_structured_content(payload)
        bullets = structured.get("resume_bullets") or self._fallback_bullets(resume_profile, match_result)
        message = structured.get("networking_message") or self._fallback_message(job_title, company, match_result)
        subject = structured.get("subject_line") or f"Interest in {job_title} at {company}"
        cover_note = structured.get("cover_note") or self._fallback_cover_note(job_title, company, match_result)
        return TailoredPackage(
            resume_bullets=[self._sanitize_bullet(item) for item in bullets if str(item).strip()],
            networking_message=message.strip(),
            subject_line=subject.strip(),
            cover_note=cover_note.strip(),
        )

    def save(self, package: TailoredPackage, job_id: int, job_title: str, company: str, output_dir: Path | None = None) -> Path:
        base_dir = output_dir or SETTINGS.outputs_path
        base_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{job_id}_{company}_{job_title}").strip("_")
        target_dir = base_dir / safe_name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "tailored_resume_bullets.md").write_text("\n".join(f"- {bullet}" for bullet in package.resume_bullets), encoding="utf-8")
        (target_dir / "networking_message.txt").write_text(package.networking_message, encoding="utf-8")
        (target_dir / "subject_line.txt").write_text(package.subject_line, encoding="utf-8")
        (target_dir / "cover_note.txt").write_text(package.cover_note, encoding="utf-8")
        (target_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "job_id": job_id,
                    "job_title": job_title,
                    "company": company,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )
        return target_dir

    def _generate_structured_content(self, payload: dict[str, object]) -> dict[str, object]:
        if not self._llm_enabled:
            return {}
        messages = [
            SystemMessage(
                content=(
                    "You are the Adaptive Document Tailor. Output only JSON with keys resume_bullets, "
                    "networking_message, subject_line, and cover_note. Use only existing resume facts and "
                    "matched requirements. Do not invent jobs, degrees, tools, or years of experience."
                )
            ),
            HumanMessage(content=json.dumps(payload, ensure_ascii=True)),
        ]
        try:
            response = self.llm.invoke(messages)
            content = getattr(response, "content", str(response))
            parsed = json.loads(self._extract_json_block(content))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.exception("[Tailor] LLM generation failed, falling back to deterministic templates")
            self._llm_enabled = False
        return {}

    @staticmethod
    def _fallback_bullets(resume_profile: ResumeProfile, match_result: MatchResult) -> list[str]:
        bullets = []
        for skill in sorted(resume_profile.skills)[:5]:
            bullets.append(f"Applied {skill} experience to deliver work aligned with the role's core requirements.")
        if match_result.matched_requirements:
            bullets.append(
                f"Reframed existing experience around {', '.join(match_result.matched_requirements[:3])} to match the role language."
            )
        return bullets or ["Summarized existing experience with direct relevance to the target role."]

    @staticmethod
    def _fallback_message(job_title: str, company: str, match_result: MatchResult) -> str:
        return (
            f"Hi, I came across the {job_title} role at {company} and noticed strong overlap with my background in "
            f"{', '.join(match_result.matched_requirements[:3]) or 'key delivery areas'}. I would value the chance to "
            "connect and learn more about the team."
        )

    @staticmethod
    def _fallback_cover_note(job_title: str, company: str, match_result: MatchResult) -> str:
        return (
            f"I am interested in the {job_title} role at {company}. My experience maps most directly to "
            f"{', '.join(match_result.matched_requirements[:4]) or 'the role requirements'}, and I have highlighted only "
            "facts already present in my master resume."
        )

    @staticmethod
    def _sanitize_bullet(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text).strip())
        return cleaned.rstrip(".") + "."

    @staticmethod
    def _extract_json_block(text: str) -> str:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        return match.group(0) if match else "{}"
