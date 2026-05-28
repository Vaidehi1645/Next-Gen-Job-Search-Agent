from __future__ import annotations

from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, StateGraph

from .database import initialize_database, job_exists, seed_job
from .matcher import TruthCheckedMatcher
from .memory_clerk import PersistentMemoryClerk
from .models import MatchResult, ResumeProfile, SearchCandidate
from .search import StrictSifter
from .resume_store import ResumeVectorStore, prepare_resume_profile


class WorkflowState(TypedDict, total=False):
    search_role: str
    tech_stack: str
    candidate: SearchCandidate
    job_id: int
    resume_profile: ResumeProfile
    match_result: MatchResult
    inserted: bool


class JobSearchWorkflow:
    def __init__(self, database_path=None) -> None:
        self.database_path = database_path
        initialize_database(self.database_path)
        self.memory = PersistentMemoryClerk(self.database_path)
        self.resume_store = ResumeVectorStore()
        self.matcher = TruthCheckedMatcher(resume_store=self.resume_store)
        self.sifter = StrictSifter()
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("sift", self._sift_node)
        graph.add_node("remember", self._remember_node)
        graph.add_node("match", self._match_node)
        graph.set_entry_point("sift")
        graph.add_edge("sift", "remember")
        graph.add_edge("remember", "match")
        graph.add_edge("match", END)
        return graph.compile()

    def run_discovery(self, search_role: str, tech_stack: str) -> list[dict[str, object]]:
        resume_profile = prepare_resume_profile()
        state: WorkflowState = {
            "search_role": search_role,
            "tech_stack": tech_stack,
            "resume_profile": resume_profile,
        }
        result = self.graph.invoke(state)
        return result.get("match_result", []) if isinstance(result.get("match_result"), list) else []

    def process_single_candidate(self, candidate: SearchCandidate, resume_profile: ResumeProfile | None = None) -> tuple[int, MatchResult]:
        resume_profile = resume_profile or prepare_resume_profile()
        job_id, inserted = self.memory.remember_found_job(candidate)
        if inserted:
            print(f"[Workflow] Stored new job {candidate.company} | {candidate.job_title}")
        else:
            print(f"[Workflow] Job already existed in SQLite, reusing id={job_id}")
        match_result = self.matcher.match(resume_profile, candidate.job_title, candidate.company, candidate.job_description)
        self.memory.mark_matched(job_id, match_result.score, match_result.detected_gaps)
        return job_id, match_result

    def _sift_node(self, state: WorkflowState) -> WorkflowState:
        search_role = state.get("search_role", "software engineer")
        tech_stack = state.get("tech_stack", "python")
        candidates = self.sifter.search(search_role, tech_stack)
        state["candidate"] = candidates[0] if candidates else SearchCandidate(
            job_title="No job found",
            company="",
            url="",
            job_description="",
            date_found="",
        )
        return state

    def _remember_node(self, state: WorkflowState) -> WorkflowState:
        candidate = state["candidate"]
        job_id, inserted = self.memory.remember_found_job(candidate)
        state["job_id"] = job_id
        state["inserted"] = inserted
        return state

    def _match_node(self, state: WorkflowState) -> WorkflowState:
        candidate = state["candidate"]
        resume_profile = state.get("resume_profile") or prepare_resume_profile()
        match_result = self.matcher.match(resume_profile, candidate.job_title, candidate.company, candidate.job_description)
        self.memory.mark_matched(state["job_id"], match_result.score, match_result.detected_gaps)
        state["match_result"] = match_result
        return state
