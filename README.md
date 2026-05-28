# Next-Gen Job Search Agent

A local-first autonomous job search system built around four restrictive agents:

- Strict Sifter: anti-spam sourcing and direct-employer validation
- Truth-Checked Matcher: factual resume-to-job alignment with explicit gap reporting
- Persistent Memory Clerk: SQLite-backed state tracking
- Adaptive Document Tailor: resume bullets and networking messages based only on existing resume facts

## Setup

1. Install dependencies: `pip install -e .`
2. Put your master resume in `resume.txt` at the repo root.
3. Start Ollama locally and pull a model such as `llama3.1` or `qwen2.5`.
4. Run `python main.py review` to review top-scoring jobs from SQLite.

## Notes

- All reasoning goes through local Ollama by default.
- ChromaDB is used to index the resume locally.
- The review loop is intentionally human-in-the-loop and does not auto-apply.
