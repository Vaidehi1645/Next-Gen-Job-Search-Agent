from __future__ import annotations

from pathlib import Path

import streamlit as st

from job_search_agent.config import SETTINGS
from job_search_agent.database import connect, fetch_top_jobs, initialize_database, serialize_job
from job_search_agent.memory_clerk import PersistentMemoryClerk
from job_search_agent.review_helpers import approve_and_generate, mark_applied, reject_job
from job_search_agent.resume_store import prepare_resume_profile


st.set_page_config(page_title="Next-Gen Job Search Agent", page_icon="🧭", layout="wide")


@st.cache_resource
def load_resume_profile():
    return prepare_resume_profile()


def load_status_counts() -> list[tuple[str, int]]:
    initialize_database()
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM jobs
            GROUP BY status
            ORDER BY count DESC, status ASC
            """
        ).fetchall()
        return [(str(row["status"]), int(row["count"])) for row in rows]


def load_jobs(status_filter: str, limit: int) -> list[dict[str, object]]:
    initialize_database()
    with connect() as connection:
        if status_filter == "All":
            rows = connection.execute(
                """
                SELECT *
                FROM jobs
                ORDER BY match_score DESC, date_found DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT *
                FROM jobs
                WHERE status = ?
                ORDER BY match_score DESC, date_found DESC, id DESC
                LIMIT ?
                """,
                (status_filter, limit),
            ).fetchall()
        return [serialize_job(row) for row in rows]


def main() -> None:
    initialize_database()
    st.title("Next-Gen Job Search Agent")
    st.caption("Local-first job discovery, matching, and human review")

    status_counts = load_status_counts()
    if status_counts:
        cols = st.columns(min(4, len(status_counts)))
        for index, (status, count) in enumerate(status_counts[: len(cols)]):
            with cols[index]:
                st.metric(status, count)

    sidebar = st.sidebar
    sidebar.header("Review Filters")
    statuses = ["All"] + [status for status, _count in status_counts] if status_counts else ["All"]
    status_filter = sidebar.selectbox("Status", statuses, index=0)
    limit = sidebar.slider("Rows", min_value=5, max_value=100, value=25, step=5)

    jobs = load_jobs(status_filter, limit)
    if not jobs:
        st.info("No jobs found. Run discovery first.")
        return

    st.subheader("Jobs")
    st.dataframe(
        jobs,
        use_container_width=True,
        hide_index=True,
    )

    job_labels = {f"{job['id']} | {job['job_title']} @ {job['company']}": job for job in jobs}
    selected_label = st.selectbox("Select a job to review", list(job_labels.keys()))
    selected_job = job_labels[selected_label]

    st.divider()
    left, right = st.columns([1.2, 1])

    with left:
        st.subheader(f"{selected_job['job_title']}")
        st.write(f"**Company:** {selected_job['company']}")
        st.write(f"**Status:** {selected_job['status']}")
        st.write(f"**Match Score:** {selected_job['match_score']}")
        st.write(f"**URL:** {selected_job['url']}")
        st.write(f"**Found:** {selected_job['date_found']}")
        st.text_area("Description", value=selected_job["job_description"], height=280)

    with right:
        st.subheader("Detected Gaps")
        gaps = selected_job.get("detected_gaps", "[]")
        st.code(gaps if isinstance(gaps, str) else str(gaps), language="json")

        st.subheader("Actions")
        if st.button("Approve and generate materials", type="primary"):
            resume_profile = load_resume_profile()
            out_dir = approve_and_generate(selected_job, resume_profile)
            st.success(f"Generated materials in {out_dir}")
            st.rerun()

        reject_reason = st.text_input("Reject reason", value="")
        if st.button("Reject job"):
            reject_job(int(selected_job["id"]), reject_reason)
            st.warning("Job rejected")
            st.rerun()

        if st.button("Mark applied"):
            mark_applied(int(selected_job["id"]))
            st.success("Marked as applied")
            st.rerun()

        st.caption(f"Database: {SETTINGS.db_path}")


if __name__ == "__main__":
    main()