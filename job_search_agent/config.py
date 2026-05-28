from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path = Path(__file__).resolve().parents[1]
    db_path: Path = project_root / "job_tracker.db"
    chroma_path: Path = project_root / "chroma_store"
    resume_path: Path = project_root / "resume.txt"
    outputs_path: Path = project_root / "outputs"
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.1")
    ollama_fallback_model: str = os.getenv("OLLAMA_FALLBACK_MODEL", "qwen2.5")
    use_tavily: bool = os.getenv("TAVILY_API_KEY", "").strip() != ""
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    review_limit: int = int(os.getenv("REVIEW_LIMIT", "3"))
    max_job_age_days: int = int(os.getenv("MAX_JOB_AGE_DAYS", "14"))
    allowed_recruiters: bool = os.getenv("ALLOW_RECRUITERS", "false").lower() in {"1", "true", "yes"}
    min_score_to_surface: int = int(os.getenv("MIN_SCORE_TO_SURFACE", "55"))


SETTINGS = Settings()
