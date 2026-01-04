import sqlite3
import datetime

def get_db_connection(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Гарантираме, че таблицата съществува
    conn.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            company TEXT,
            title TEXT,
            score INTEGER,
            status TEXT,
            processed_at TIMESTAMP,
            url TEXT,
            raw_data TEXT
        )
    ''')
    return conn

def is_job_processed(conn, job):
    """
    Checks if a job exists by ID OR by (Company + Title) combination.
    This prevents duplicates when URLs/IDs change slightly.
    """
    cur = conn.cursor()
    
    # 1. Проверка по точно ID (най-бързо)
    cur.execute("SELECT 1 FROM jobs WHERE job_id = ?", (job.job_id,))
    if cur.fetchone():
        return True
    
    # 2. Проверка по Компания + Заглавие (The Safety Net)
    # Нормализираме до lowercase, за да хванем "Sap" vs "SAP"
    cur.execute(
        "SELECT 1 FROM jobs WHERE lower(company) = ? AND lower(title) = ?", 
        (job.company.lower().strip(), job.title.lower().strip())
    )
    if cur.fetchone():
        return True
        
    return False

def mark_job_as_processed(conn, job, score):
    """
    Saves the job result to the DB.
    """
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Използваме job.raw (dict) или празен string ако го няма
        raw_str = str(getattr(job, 'raw', '')) 
        
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs 
            (job_id, company, title, score, status, processed_at, url, raw_data) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.job_id, 
                job.company, 
                job.title, 
                score, 
                "analyzed", 
                now, 
                job.apply_url, 
                raw_str
            )
        )
        conn.commit()
    except Exception as e:
        print(f"[DB Error] Failed to save job: {e}")