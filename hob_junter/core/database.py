import sqlite3
from hob_junter.core.scraper import JobRecord

def get_db_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def is_job_processed(conn: sqlite3.Connection, job_id: str) -> bool:
    """Checks if we have ever seen this job ID before."""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job_id,))
    return cursor.fetchone() is not None

def mark_job_as_processed(conn: sqlite3.Connection, job: JobRecord, score: int, status: str = "analyzed"):
    """Saves the job to DB so we don't pay for it again."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO jobs (job_id, title, company, url, score, status) VALUES (?, ?, ?, ?, ?, ?)",
            (job.job_id, job.title, job.company, job.apply_url, score, status)
        )
        conn.commit()
    except sqlite3.Error as exc:
        print(f"[DB] Error saving job {job.job_id}: {exc}")