from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

from job_search_agent.config import SETTINGS
from job_search_agent.database import (
    fetch_top_jobs,
    fetch_unprocessed_candidates,
    get_job_by_id,
    initialize_database,
    record_match_result,
    seed_job,
)
from job_search_agent.matcher import TruthCheckedMatcher
from job_search_agent.memory_clerk import PersistentMemoryClerk
from job_search_agent.models import JobStatus, SearchCandidate
from job_search_agent.resume_store import prepare_resume_profile
from job_search_agent.search import StrictSifter
from job_search_agent.tailor import AdaptiveDocumentTailor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Next-Gen Job Search Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Find and score jobs")
    discover.add_argument("--role", required=True, help="Target role, e.g. 'AI Engineer'")
    discover.add_argument("--stack", required=True, help="Tech stack keywords")
    discover.add_argument("--limit", type=int, default=20, help="Max search results per query")

    review = subparsers.add_parser("review", help="Human-in-the-loop review for top jobs")
    review.add_argument("--top", type=int, default=SETTINGS.review_limit, help="How many top jobs to review")

    return parser.parse_args()


def run_discovery(role: str, stack: str, limit: int) -> None:
    initialize_database()
    clerk = PersistentMemoryClerk()
    sifter = StrictSifter()
    matcher = TruthCheckedMatcher()
    resume_profile = prepare_resume_profile()

    print("[Main] Starting discovery pipeline")
    candidates = sifter.search(role, stack, max_results=limit)
    if not candidates:
        print("[Main] No candidates passed strict anti-spam checks.")
        return

    for candidate in candidates:
        if clerk.should_skip(candidate.url):
            print(f"[Main] Skipping previously rejected URL: {candidate.url}")
            continue
        job_id, inserted = clerk.remember_found_job(candidate)
        if inserted:
            print(f"[Main] Stored new candidate id={job_id} url={candidate.url}")
        else:
            print(f"[Main] Candidate already in database id={job_id} url={candidate.url}")

        existing = get_job_by_id(job_id)
        if existing is not None and existing["status"] in {JobStatus.APPLIED.value, JobStatus.REJECTED.value}:
            print(f"[Main] Job id={job_id} already finalized with status={existing['status']}, skipping.")
            continue

        match = matcher.match(resume_profile, candidate.job_title, candidate.company, candidate.job_description)
        record_match_result(job_id, match.score, match.detected_gaps)
        print(
            f"[Main] MATCHED id={job_id} score={match.score} company={candidate.company} "
            f"title={candidate.job_title} gaps={len(match.detected_gaps)}"
        )


def run_review_loop(top_n: int) -> None:
    initialize_database()
    clerk = PersistentMemoryClerk()
    tailor = AdaptiveDocumentTailor()
    resume_profile = prepare_resume_profile()
    jobs = fetch_top_jobs(limit=top_n)

    if not jobs:
        print("[Review] No reviewable jobs found. Run discovery first.")
        return

    print(f"[Review] Loaded top {len(jobs)} jobs for human review")
    for row in jobs:
        job_id = int(row["id"])
        gaps = []
        try:
            gaps = json.loads(row["detected_gaps"]) if row["detected_gaps"] else []
        except Exception:
            gaps = [str(row["detected_gaps"])] if row["detected_gaps"] else []
        print("\n" + "=" * 90)
        print(f"Job ID: {job_id}")
        print(f"Title: {row['job_title']}")
        print(f"Company: {row['company']}")
        print(f"URL: {row['url']}")
        print(f"Status: {row['status']}")
        print(f"Match Score: {row['match_score']}")
        print(f"Date Found: {row['date_found']}")
        print("Detected Skill Gaps:")
        if gaps:
            for gap in gaps:
                print(f" - {gap}")
        else:
            print(" - None")

        while True:
            action = input("[A]pprove to generate materials, [S]kip, or [R]eject? ").strip().upper()
            if action not in {"A", "S", "R"}:
                print("[Review] Invalid input. Enter A, S, or R.")
                continue
            if action == "S":
                print(f"[Review] Skipped job id={job_id}")
                break
            if action == "R":
                reason = input("Optional rejection reason: ").strip()
                clerk.mark_rejected(job_id, reason=reason)
                print(f"[Review] Rejected job id={job_id}")
                break
            if action == "A":
                clerk.mark_approved(job_id)
                package = tailor.generate(
                    resume_profile=resume_profile,
                    job_title=row["job_title"],
                    company=row["company"],
                    job_description=row["job_description"],
                    match_result=_row_to_match_result(row, gaps),
                )
                out_dir = tailor.save(package, job_id=job_id, job_title=row["job_title"], company=row["company"])
                print(f"[Review] Approved and generated tailored materials in: {out_dir}")
                print("[Review] Mark APPLIED manually after you submit the application.")
                break


def _row_to_match_result(row, gaps: list[str]):
    from job_search_agent.models import MatchResult

    critical = [item for item in gaps if str(item).lower().startswith("critical hard skill gap")]
    matched = []
    return MatchResult(
        score=int(row["match_score"]),
        matched_requirements=matched,
        detected_gaps=gaps,
        critical_gaps=critical,
        factual_evidence=[],
        job_summary="",
        raw_llm_output={},
    )


def main() -> None:
    args = parse_args()
    if args.command == "discover":
        run_discovery(args.role, args.stack, args.limit)
        return
    if args.command == "review":
        run_review_loop(args.top)
        return


if __name__ == "__main__":
    main()
