from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
import requests

import streamlit as st

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from job_search_agent.config import SETTINGS
from job_search_agent.database import (
    fetch_top_jobs,
    get_job_by_id,
    initialize_database,
    record_match_result,
)
from job_search_agent.matcher import TruthCheckedMatcher
from job_search_agent.memory_clerk import PersistentMemoryClerk
from job_search_agent.models import JobStatus
from job_search_agent.resume_store import prepare_resume_profile
from job_search_agent.review_helpers import approve_and_generate, mark_applied, reject_job
from job_search_agent.search import StrictSifter
from job_search_agent.logging_config import logger




def _validate_place(name: str) -> bool:
    """Validate place name using OpenStreetMap Nominatim. Returns True if a match is found."""
    try:
        url = "https://nominatim.openstreetmap.org/search"
        resp = requests.get(url, params={"q": name, "format": "json", "limit": 1}, headers={"User-Agent": "NextGenJobAgent/1.0"}, timeout=8)
        resp.raise_for_status()
        data = resp.json()
        return bool(data)
    except Exception:
        logger.exception("_validate_place failed for name=%s", name)
        return False


def _parse_gaps(raw_value: object) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(item) for item in raw_value]
    if isinstance(raw_value, str):
        text = raw_value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            logger.debug("_parse_gaps: could not parse gaps text: %s", text)
            return [text]
        return [text]
    return [str(raw_value)]


def _run_discovery_like_cli(
    role: str,
    stack: str,
    limit: int,
    location: str,
    resume_path: Path,
) -> dict[str, int]:
    initialize_database()
    clerk = PersistentMemoryClerk()
    sifter = StrictSifter()
    matcher = TruthCheckedMatcher()
    resume_profile = prepare_resume_profile(resume_path=resume_path)

    query_role = role.strip()
    if location.strip():
        query_role = f"{query_role} {location.strip()}"

    candidates = sifter.search(query_role, stack.strip(), max_results=limit)
    if not candidates:
        return {"found": 0, "matched": 0, "skipped": 0}

    matched = 0
    skipped = 0
    for candidate in candidates:
        if clerk.should_skip(candidate.url):
            skipped += 1
            continue

        job_id, _inserted = clerk.remember_found_job(candidate)
        existing = get_job_by_id(job_id)
        if existing is not None and existing["status"] in {JobStatus.APPLIED.value, JobStatus.REJECTED.value}:
            skipped += 1
            continue

        match = matcher.match(resume_profile, candidate.job_title, candidate.company, candidate.job_description)
        record_match_result(job_id, match.score, match.detected_gaps)
        matched += 1

    return {"found": len(candidates), "matched": matched, "skipped": skipped}


def main() -> None:
    st.set_page_config(page_title="Next-Gen Job Search Dashboard", layout="wide")
    st.title("Next-Gen Job Search Assistant")
    st.caption("Step-by-step helper for discovery and review. Built to work like the CLI, with simpler language.")

    import job_search_agent.database as _db

    stored = getattr(_db, "load_latest_profile", lambda: {})() or {}

    st.sidebar.header("Actions")
    if st.sidebar.button("Refresh"):
        st.experimental_rerun()

    st.subheader("Step 1: Your Profile and Job Search Preferences")
    st.write("Fill this once. The app will remember it in the local database.")

    left, right = st.columns([2, 1])
    with left:
        uploaded = st.file_uploader("Upload your resume (TXT or PDF)", type=["txt", "pdf"], key="resume_upload")
        if stored.get("resume_path"):
            st.markdown(f"Current saved resume: **{stored.get('resume_path')}**")
        full_name = st.text_input("Your name", value=str(stored.get("name", "")))
        github = st.text_input("GitHub profile URL", value=str(stored.get("github", "")))
        linkedin = st.text_input("LinkedIn profile URL", value=str(stored.get("linkedin", "")))
        twitter = st.text_input("Twitter / X profile URL", value=str(stored.get("twitter", "")))
        other_links_raw = st.text_area("Other links (one per line)", value="\n".join(stored.get("other_links") or []))
        years_experience = st.number_input("Years of experience", min_value=0, max_value=80, value=int(stored.get("years_experience") or 0))
        desired_role = st.text_input("Target job role", value=str(stored.get("desired_role", "AI Engineer")))
        desired_stack = st.text_input("Tech stack / keywords (comma separated)", value=str(stored.get("desired_stack", "Python, Ollama, LangGraph")))
        declaration_date_val = stored.get("declaration_date") or date.today().isoformat()
        try:
            declaration_date = st.date_input("Declaration date (when you became available)", value=date.fromisoformat(str(declaration_date_val)))
        except Exception:
                logger.debug("Failed to parse declaration_date_val=%s, falling back to today", declaration_date_val)
                declaration_date = st.date_input("Declaration date (when you became available)", value=date.today())
        notes = st.text_area("Optional notes or preferences", value=str(stored.get("notes", "")))

    with right:
        st.markdown("### Preferred locations")
        if "locations" not in st.session_state:
            st.session_state.locations = stored.get("locations") or []
        loc_input = st.text_input("Enter a place and press Add", key="loc_input")
        if st.button("Add location"):
            place = loc_input.strip()
            if place:
                if _validate_place(place):
                    if place not in st.session_state.locations:
                        st.session_state.locations.append(place)
                        st.success(f"Added location: {place}")
                    else:
                        st.info("Location already added")
                else:
                    st.error("Place not found — please enter a valid city or region name")
        for i, loc in enumerate(list(st.session_state.locations)):
            c1, c2 = st.columns([8, 1])
            c1.markdown(f"- {loc}")
            if c2.button("Remove", key=f"remove_loc_{i}"):
                st.session_state.locations.pop(i)
                st.experimental_rerun()

    if st.button("Save profile"):
        # Save uploaded resume to SETTINGS.resume_path if provided
        if uploaded is not None:
            data = uploaded.getvalue()
            SETTINGS.resume_path.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.resume_path.write_bytes(data)
        other_links = [line.strip() for line in other_links_raw.splitlines() if line.strip()]
        import job_search_agent.database as _db

        getattr(_db, "save_profile")(  # type: ignore[arg-type]
            name=full_name,
            resume_path=str(SETTINGS.resume_path),
            github=github,
            linkedin=linkedin,
            twitter=twitter,
            other_links=other_links,
            years_experience=int(years_experience),
            desired_role=desired_role,
            desired_stack=desired_stack,
            locations=st.session_state.locations,
            declaration_date=str(declaration_date),
            notes=notes,
        )
        st.success("Profile saved to local DB")

    st.subheader("Step 2: Discover Jobs (same pipeline as CLI)")
    discover_help = st.expander("What this does")
    with discover_help:
        st.write("Runs strict sifting, saves jobs in SQLite, then scores matches against your resume.")

    if st.button("Run discovery now", type="primary"):
        import job_search_agent.database as _db

        active_profile = getattr(_db, "load_latest_profile", lambda: {})() or {}
        resume_path = Path(str(active_profile.get("resume_path", SETTINGS.resume_path))).expanduser()
        if not resume_path.exists():
            st.error(f"Resume file not found at: {resume_path}. Upload a resume or fix the path first.")
        else:
            with st.spinner("Running discovery and matching..."):
                summary = _run_discovery_like_cli(
                    role=str(active_profile.get("target_role", target_role)).strip() or target_role,
                    stack=str(active_profile.get("tech_stack", tech_stack)).strip() or tech_stack,
                    limit=int(active_profile.get("search_limit", search_limit) or search_limit),
                    location=str(active_profile.get("preferred_location", preferred_location)),
                    resume_path=resume_path,
                )
            st.success(
                "Discovery finished: "
                f"found={summary['found']}, matched={summary['matched']}, skipped={summary['skipped']}"
            )

    st.subheader("Step 3: Review Queue (same actions as CLI)")
    top_n = st.slider("How many top jobs to review", min_value=1, max_value=100, value=10, step=1)
    jobs = fetch_top_jobs(limit=top_n)

    if not jobs:
        st.info("No reviewable jobs found yet. Run discovery first.")
        return

    st.write(f"Loaded {len(jobs)} jobs for review.")
    labels = [f"{int(row['id'])} | {row['job_title']} @ {row['company']}" for row in jobs]
    selected_label = st.selectbox("Select a job", labels)
    selected_index = labels.index(selected_label)
    row = jobs[selected_index]
    job_id = int(row["id"])

    left, right = st.columns([2, 1])
    with left:
        st.write(f"**Title:** {row['job_title']}")
        st.write(f"**Company:** {row['company']}")
        st.write(f"**URL:** {row['url']}")
        st.write(f"**Status:** {row['status']}")
        st.write(f"**Match Score:** {row['match_score']}")
        st.write(f"**Date Found:** {row['date_found']}")
        st.write("**Detected Gaps:**")
        gaps = _parse_gaps(row["detected_gaps"])
        if gaps:
            for gap in gaps:
                st.write(f"- {gap}")
        else:
            st.write("- None")
        st.text_area("Job description", value=row["job_description"][:3500], height=220)

    with right:
        st.write("**Review action**")
        reject_reason = st.text_area("Reject reason (optional)", key=f"reject_reason_{job_id}", height=90)

        if st.button("Approve and generate materials", key=f"approve_{job_id}"):
            active_profile = load_latest_profile() or {}
            resume_path = Path(str(active_profile.get("resume_path", SETTINGS.resume_path))).expanduser()
            if not resume_path.exists():
                st.error(f"Resume file not found at: {resume_path}")
            else:
                with st.spinner("Generating tailored material..."):
                    resume_profile = prepare_resume_profile(resume_path=resume_path)
                    out_dir = approve_and_generate(row, resume_profile=resume_profile)
                st.success(f"Approved and generated materials in: {out_dir}")
                st.rerun()

        if st.button("Reject", key=f"reject_{job_id}"):
            reject_job(job_id, reason=reject_reason.strip())
            st.warning("Rejected")
            st.rerun()

        if st.button("Skip (no changes)", key=f"skip_{job_id}"):
            st.info("Skipped. No status changes were made.")

        if st.button("Mark Applied", key=f"applied_{job_id}"):
            mark_applied(job_id)
            st.success("Marked as APPLIED")
            st.rerun()

    st.markdown("---")
    st.caption("Keep ollama serve running if you want LLM-powered generation; otherwise deterministic fallbacks are used.")


if __name__ == "__main__":
    main()
