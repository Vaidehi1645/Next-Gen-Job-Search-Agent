from job_search_agent.database import *  # noqa: F401,F403

if __name__ == "__main__":
    initialize_database()
    print("Initialized SQLite schema at job_tracker.db")
