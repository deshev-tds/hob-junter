import gspread
import time
from datetime import datetime
from hob_junter.core.scraper import JobRecord

def get_gspread_client(creds_path: str):
    try:
        return gspread.service_account(filename=creds_path)
    except Exception as exc:
        print(f"[Sheets] Auth Error: {exc}")
        return None

def log_job_to_sheet(client, spreadsheet_id: str, job: JobRecord, score: int, reason: str):
    if not client or not spreadsheet_id:
        return

    try:
        sheet = client.open_by_key(spreadsheet_id).sheet1
        
        # Check if headers exist (lazy check)
        if not sheet.get_values("A1:A1"):
            sheet.append_row(["Date", "Company", "Role", "Score", "Link", "Status", "Reason", "Notes"])

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        
        # Status defaults to "New"
        row = [
            timestamp,
            job.company,
            job.title,
            score,
            job.apply_url,
            "New",          # Status
            reason[:100],   # Short reason
            ""              # Notes (empty)
        ]
        
        sheet.append_row(row)
        # Avoid hitting API limits
        time.sleep(1) 
        
    except Exception as exc:
        print(f"[Sheets] Write Error: {exc}")