from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import re
from typing import Iterable

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from .config import SETTINGS
from .models import ResumeProfile


class ResumeVectorStore:
    def __init__(self, resume_path: Path | None = None, chroma_path: Path | None = None) -> None:
        self.resume_path = resume_path or SETTINGS.resume_path
        self.chroma_path = chroma_path or SETTINGS.chroma_path
        self.embedding_function = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        self.client = chromadb.PersistentClient(path=str(self.chroma_path))
        self.collection = self.client.get_or_create_collection(
            name="resume_memory",
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine"},
        )

    def load_resume_text(self) -> str:
        if not self.resume_path.exists():
            raise FileNotFoundError(
                f"Master resume not found at {self.resume_path}. Create resume.txt before running the workflow."
            )
        return self.resume_path.read_text(encoding="utf-8")

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
        cleaned = re.sub(r"\r\n", "\n", text).strip()
        if not cleaned:
            return []
        chunks: list[str] = []
        start = 0
        while start < len(cleaned):
            end = min(len(cleaned), start + chunk_size)
            chunks.append(cleaned[start:end])
            if end == len(cleaned):
                break
            start = max(0, end - overlap)
        return chunks

    def build_or_refresh_index(self) -> ResumeProfile:
        resume_text = self.load_resume_text()
        chunks = self.chunk_text(resume_text)
        self.collection.delete(where={"source": "resume.txt"})
        ids = [f"chunk-{index}" for index in range(len(chunks))]
        metadatas = [{"source": "resume.txt", "chunk_index": index} for index in range(len(chunks))]
        self.collection.add(ids=ids, documents=chunks, metadatas=metadatas)
        return ResumeProfile(raw_text=resume_text, sections=self._split_sections(resume_text), skills=self._extract_skills(resume_text))

    def query(self, query_text: str, top_k: int = 5) -> list[str]:
        response = self.collection.query(query_texts=[query_text], n_results=top_k)
        documents = response.get("documents", [[]])[0]
        return [document for document in documents if document]

    @staticmethod
    def _split_sections(text: str) -> dict[str, str]:
        sections: dict[str, str] = {}
        current_header = "summary"
        buffer: list[str] = []
        for line in text.splitlines():
            header_match = re.match(r"^([A-Z][A-Z\s/&-]{2,})$", line.strip())
            if header_match:
                if buffer:
                    sections[current_header] = "\n".join(buffer).strip()
                current_header = header_match.group(1).lower().strip().replace(" ", "_")
                buffer = []
                continue
            buffer.append(line)
        if buffer:
            sections[current_header] = "\n".join(buffer).strip()
        return sections

    @staticmethod
    def _extract_skills(text: str) -> set[str]:
        canonical_skills = {
            "python",
            "sql",
            "sqlite",
            "chromadb",
            "langgraph",
            "crewai",
            "ollama",
            "llm",
            "playwright",
            "pandas",
            "numpy",
            "fastapi",
            "django",
            "flask",
            "docker",
            "kubernetes",
            "aws",
            "gcp",
            "azure",
            "git",
            "linux",
            "pytest",
            "typescript",
            "javascript",
            "react",
            "machine learning",
            "nlp",
            "rag",
            "vector embeddings",
        }
        lower_text = text.lower()
        return {skill for skill in canonical_skills if skill in lower_text}


def prepare_resume_profile(resume_path: Path | None = None, chroma_path: Path | None = None) -> ResumeProfile:
    store = ResumeVectorStore(resume_path=resume_path, chroma_path=chroma_path)
    return store.build_or_refresh_index()
