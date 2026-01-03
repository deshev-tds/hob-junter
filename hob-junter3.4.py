import os
import json
import time
import sys
import re
import html
import urllib.parse
import difflib
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from threading import Lock

# Third-party
import requests
import trafilatura
import pypdf
from bs4 import BeautifulSoup
from openai import OpenAI
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

# ==========================================
# 1. CONFIG & CONSTANTS
# ==========================================

CONFIG_FILE = "inputs.json"
PROFILE_FILE = "cv_profile_master.json"
HISTORY_FILE = "job_history.json"
REPORT_FILE = f"jobs_report_{datetime.now().strftime('%Y%m%d_%H%M')}.html"

ATS_TARGETS = [
    "site:boards.greenhouse.io",
    "site:jobs.ashbyhq.com",
    "site:jobs.lever.co",
    "site:apply.workable.com",
    "site:bamboohr.com"
]

TRACKING_BLOCKLIST = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "dclid", "msclkid", 
    "gh_src", "lever-source", "li_fat_id"
}

GARBAGE_SIGNATURES = [
    "enable javascript", "please turn on javascript", 
    "access denied", "error 1020", "cloudflare", 
    "captcha", "human verification", "too many requests"
]

SCORE_PROMPT = """You are a ruthless Executive Recruiter. 
Assess this job for a Senior Tech Leader.

INPUT SOURCE: {data_type} 
{warning_text}

CANDIDATE PROFILE:
{cv_profile}

JOB CONTENT:
{job_text}

OUTPUT STRICT JSON:
{
  "score": <0-100>,
  "company": "<extracted>",
  "title": "<extracted>",
  "reason": "<short justification>",
  "manual_review_needed": <boolean> 
}
"""

PROFILE_GEN_PROMPT = "Extract structured profile: Leadership Scope, Core Tech Stack, Strategic Skills. Return JSON."

# GLOBAL STATE & LOCKS
BLOCKED_DOMAINS = set()
DOMAIN_LOCK = Lock()

# ==========================================
# 2. CORE UTILS
# ==========================================

def load_config():
    if os.path.exists(CONFIG_FILE):
        try: with open(CONFIG_FILE, "r") as f: return json.load(f)
        except: return {}
    return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f: json.dump(cfg, f, indent=2)

def clean_url(url):
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        new_qs = {k: v for k, v in qs.items() if k.lower() not in TRACKING_BLOCKLIST}
        new_query = urllib.parse.urlencode(new_qs, doseq=True)
        return urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path, 
            parsed.params, new_query, parsed.fragment
        ))
    except: return url

def safe_read_pdf(path):
    try:
        reader = pypdf.PdfReader(path)
        return "\n".join([(page.extract_text() or "") for page in reader.pages])[:50000]
    except Exception as e:
        print(f"[Error] PDF Read: {e}"); return ""

def is_garbage_content(text):
    if len(text) < 300: return True
    header_text = text.lower()[:1000]
    for sig in GARBAGE_SIGNATURES:
        if sig in header_text: return True
    return False

def extract_date_posted(soup):
    try:
        scripts = soup.find_all('script', type='application/ld+json')
        for s in scripts:
            if not s.string: continue
            data = json.loads(s.string)
            def find_date(obj):
                if isinstance(obj, dict):
                    if obj.get("@type") == "JobPosting" and "datePosted" in obj: return obj["datePosted"]
                    for k, v in obj.items():
                        res = find_date(v); 
                        if res: return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_date(item); 
                        if res: return res
                return None
            res = find_date(data)
            if res: return res
    except: pass
    return None

# ==========================================
# 3. NOTIFICATION & CRM SERVICES
# ==========================================

class TelegramBot:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
    
    def send(self, message):
        if not self.token or not self.chat_id: return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            requests.post(url, json={"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}, timeout=5)
        except Exception as e:
            print(f" [!] Telegram Error: {e}")

class SheetsClient:
    def __init__(self, spreadsheet_id):
        self.spreadsheet_id = spreadsheet_id
        self.service = None
        
    def authenticate(self):
        if not self.spreadsheet_id: return
        SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
        creds = None
        if os.path.exists('token.json'):
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif os.path.exists('../../Downloads/google_credentials.json'):
                flow = InstalledAppFlow.from_client_secrets_file('../../Downloads/google_credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
            else:
                return # No auth possible
            with open('token.json', 'w') as token:
                token.write(creds.to_json())
        self.service = build('sheets', 'v4', credentials=creds)

    def append_row(self, job_data):
        if not self.service: return
        try:
            values = [[
                datetime.now().strftime("%Y-%m-%d"),
                job_data.get('score', 0),
                job_data.get('company', ''),
                job_data.get('title', ''),
                job_data.get('url', ''),
                job_data.get('reason', '')
            ]]
            body = {'values': values}
            self.service.spreadsheets().values().append(
                spreadsheetId=self.spreadsheet_id, range="Sheet1!A1",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
        except Exception as e:
            print(f" [!] Sheets Error: {e}")

# ==========================================
# 4. DEDUPLICATION
# ==========================================

class Deduplicator:
    def __init__(self, history_file=HISTORY_FILE):
        self.history_file = history_file
        self.history = self._load_history()

    def _load_history(self):
        if os.path.exists(self.history_file):
            try: with open(self.history_file, "r") as f: return json.load(f)
            except: pass
        return []

    def save(self, company, title, url):
        self.history.append({
            "company": company.lower().strip(),
            "title": title.lower().strip(),
            "url": url,
            "date": datetime.now().isoformat()
        })
        with open(self.history_file, "w") as f: json.dump(self.history, f, indent=2)

    def is_duplicate(self, new_company, new_title):
        nc = new_company.lower().strip()
        nt = new_title.lower().strip()
        if nc in ["unknown", ""] or nt in ["unknown", ""]: return False

        for old in self.history:
            oc = old["company"]
            ot = old["title"]
            if difflib.SequenceMatcher(None, nc, oc).ratio() > 0.85:
                if difflib.SequenceMatcher(None, nt, ot).ratio() > 0.80:
                    print(f"   [Dedupe] Blocks: '{new_title}' @ '{new_company}'")
                    return True
        return False

# ==========================================
# 5. ROBUST EXTRACTION
# ==========================================

def fetch_ats_content_robust(url):
    domain = urlparse(url).netloc
    
    with DOMAIN_LOCK:
        if domain in BLOCKED_DOMAINS: return None, None, 'SKIPPED_DOMAIN_BLOCKED'

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        
        if resp.status_code in [403, 429]:
            should_log = False
            with DOMAIN_LOCK:
                if domain not in BLOCKED_DOMAINS:
                    BLOCKED_DOMAINS.add(domain)
                    should_log = True
            if should_log: print(f" [!!!] DOMAIN BLOCKED ({resp.status_code}): {domain}. Circuit broken.")
            return None, None, 'BLOCK'
            
        if resp.status_code != 200: return None, None, 'ERROR'
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        text = ""
        date_posted = extract_date_posted(soup)

        if "boards.greenhouse.io" in url:
            main = soup.find(id="content") or soup.find(id="main")
            if main: text = main.get_text(separator="\n")
        elif "jobs.lever.co" in url:
            main = soup.find("div", class_="content-wrapper")
            if main: text = main.get_text(separator="\n")
        elif "ashbyhq.com" in url:
            try:
                script = soup.find("script", id="__NEXT_DATA__")
                if script:
                    data = json.loads(script.string)
                    props = data.get('props', {}).get('pageProps', {}).get('jobPosting', {})
                    text = props.get('description', '') or props.get('descriptionHtml', '')
                    if "<" in text: text = BeautifulSoup(text, "html.parser").get_text(separator="\n")
            except: pass
            if not text: text = soup.get_text(separator="\n")
        elif "workable.com" in url:
            main = soup.find("main")
            if main: text = main.get_text(separator="\n")

        if not text or len(text) < 200:
            text = trafilatura.extract(resp.text, include_comments=False)
            
        if not text or is_garbage_content(text): return None, None, 'GARBAGE'

        return text, date_posted, 'SUCCESS'
            
    except Exception as e: return None, None, 'ERROR'

# ==========================================
# 6. ENGINE LOGIC
# ==========================================

class HobJunterEngine:
    def __init__(self):
        self.cfg = load_config()
        self.client = None
        self.google_service = None
        self.cv_profile = None
        self.deduper = Deduplicator()
        self.telegram = None
        self.sheets = None

    def setup(self):
        print(">>> HOB JUNTER 3.4: PLATINUM EDITION <<<")
        if not self.cfg.get("openai_key") or not self.cfg.get("google_api_key"):
            self.cfg["openai_key"] = input("OpenAI Key: ").strip()
            self.cfg["google_api_key"] = input("Google API Key: ").strip()
            self.cfg["google_cse_id"] = input("Google CX ID: ").strip()
            save_config(self.cfg)
        
        self.client = OpenAI(api_key=self.cfg["openai_key"])
        self.google_service = build("customsearch", "v1", developerKey=self.cfg["google_api_key"])
        
        # Init Notifications (Non-blocking)
        if self.cfg.get("telegram_token"):
            self.telegram = TelegramBot(self.cfg["telegram_token"], self.cfg.get("telegram_chat_id"))
            
        if self.cfg.get("spreadsheet_id"):
            self.sheets = SheetsClient(self.cfg["spreadsheet_id"])
            try:
                self.sheets.authenticate()
            except Exception as e:
                print(f"[Warn] Sheets auth failed: {e}")

    def enforce_profile(self):
        if os.path.exists(PROFILE_FILE):
            print(f"[Profile] Loading MASTER profile from {PROFILE_FILE}")
            with open(PROFILE_FILE, "r") as f: self.cv_profile = json.dumps(json.load(f))
            return

        print("[Profile] Generating Master Profile...")
        cv_path = self.cfg.get("cv_path") or input("Path to CV PDF: ").strip()
        self.cfg["cv_path"] = cv_path
        save_config(self.cfg)

        cv_text = safe_read_pdf(cv_path)
        resp = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "system", "content": PROFILE_GEN_PROMPT}, {"role": "user", "content": cv_text[:15000]}],
            response_format={"type": "json_object"}
        )
        with open(PROFILE_FILE, "w") as f: json.dump(json.loads(resp.choices[0].message.content), f, indent=2)
        print(f"\n STOP! Verify {PROFILE_FILE} manually. Restart required.")
        sys.exit(0)

    def sniper_hunt(self):
        roles = self.cfg.get("target_roles", ["Director of Engineering", "Principal SRE", "Head of Infrastructure"])
        loc = self.cfg.get("location_query", '("Bulgaria" OR "Remote Europe")')
        neg = self.cfg.get("negative_keywords", "-Intern -Junior")
        
        print(f"\n[Sniper] Hunting: {roles}")
        raw_leads = []
        
        for domain in ATS_TARGETS:
            for role in roles:
                query = f"{domain} \"{role}\" {loc} {neg}"
                print(f"   > Scanning {domain}...", end="\r")
                try:
                    for start in [1, 11]: 
                        res = self.google_service.cse().list(
                            q=query, cx=self.cfg["google_cse_id"],
                            num=10, start=start, dateRestrict='w1'
                        ).execute()
                        for item in res.get('items', []):
                            raw_leads.append({
                                "url": clean_url(item['link']),
                                "snippet": item.get('snippet', ''),
                                "title": item.get('title', '')
                            })
                        time.sleep(0.5)
                except Exception as e:
                    if "Quota" in str(e): print("\n[!] Google Quota Exceeded."); return raw_leads
                    print(f"\n[!] Google API Error: {e}")

        unique = {l['url']: l for l in raw_leads}.values()
        print(f"\n[Sniper] Acquired {len(unique)} unique targets.")
        return list(unique)

    def process_leads(self, leads):
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_lead = {executor.submit(fetch_ats_content_robust, l['url']): l for l in leads}
            
            for future in as_completed(future_to_lead):
                lead = future_to_lead[future]
                full_text, date_posted, status = future.result()
                
                if status == 'SKIPPED_DOMAIN_BLOCKED':
                    print(f"   > Skipped (Circuit Breaker): {lead['title'][:20]}")
                    continue

                if date_posted:
                    try:
                        dt = datetime.fromisoformat(date_posted.replace('Z', '+00:00').split('T')[0])
                        if dt < datetime.now() - timedelta(days=30):
                            print(f"   > Skipping (Stale {date_posted}): {lead['title'][:30]}")
                            continue
                    except: pass 

                if status == 'SUCCESS':
                    data_type = "FULL_CONTENT"
                    content = full_text[:12000]
                    warning = ""
                else:
                    data_type = "SNIPPET_ONLY"
                    content = f"TITLE: {lead['title']}\nSNIPPET: {lead['snippet']}"
                    warning = "WARNING: CONTENT MISSING. MAX SCORE 65."

                print(f"   > Scoring ({data_type}): {lead['title'][:30]}...", end="", flush=True)
                
                try:
                    prompt = SCORE_PROMPT.replace("{data_type}", data_type) \
                                         .replace("{warning_text}", warning) \
                                         .replace("{cv_profile}", self.cv_profile) \
                                         .replace("{job_text}", content)
                    
                    resp = self.client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role": "system", "content": "JSON only"}, {"role": "user", "content": prompt}],
                        response_format={"type": "json_object"}
                    )
                    
                    data = json.loads(resp.choices[0].message.content)
                    score = data.get('score', 0)
                    
                    if data_type == "SNIPPET_ONLY" and score > 65:
                        score = 65 
                        data['reason'] = "[SNIPPET CAP] " + data.get('reason', '')

                    company = data.get('company', 'Unknown')
                    title = data.get('title', lead['title'])
                    
                    if self.deduper.is_duplicate(company, title):
                        print(f" [Duplicate]")
                        continue
                    
                    print(f" [{score}]")
                    
                    if score >= 60 or data.get('manual_review_needed'):
                        # 1. SAVE TO HISTORY
                        if status == 'SUCCESS' or score > 75:
                            self.deduper.save(company, title, lead['url'])
                        
                        # 2. PUSH TO TELEGRAM (Only High Value)
                        if self.telegram and score >= 85:
                            msg = f" <b>{score} - {title}</b>\n{company}\n<a href='{lead['url']}'>Apply Now</a>"
                            self.telegram.send(msg)

                        # 3. PUSH TO SHEETS (All accepted)
                        if self.sheets:
                            self.sheets.append_row({**data, "url": lead['url']})

                        results.append({
                            **data,
                            "score": score,
                            "url": lead['url'],
                            "is_snippet": (data_type == "SNIPPET_ONLY"),
                            "status": status
                        })
                except Exception as e: print(f" Err: {e}")
        return results

    def generate_report(self, results):
        if not results: return
        results.sort(key=lambda x: x['score'], reverse=True)
        rows = ""
        for r in results:
            style = "color:#e37400"
            tags = ""
            if r.get('is_snippet'): tags += "<span style='background:#fce8e6;color:#c5221f;padding:2px 4px;font-size:0.7em'>SNIPPET</span> "
            if r.get('manual_review_needed'): tags += "<span style='background:#fff8c5;color:#5f5500;padding:2px 4px;font-size:0.7em'>REVIEW</span> "
            if r['score'] >= 85: style = "color:#137333;font-weight:bold"
            rows += f"<tr><td><a href='{r['url']}'><b>{r['title']}</b></a><br>{r['company']}<br>{tags}</td><td style='{style};font-size:1.4em'>{r['score']}</td><td>{r['reason']}</td></tr>"
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            f.write(f"<html><head><style>body{{font-family:sans-serif;padding:30px}} table{{width:100%;border-collapse:collapse}} td{{padding:15px;border-bottom:1px solid #eee}}</style></head><body><h1>Report {datetime.now()}</h1><table>{rows}</table></body></html>")
        print(f"\n[Report] {REPORT_FILE}")

if __name__ == "__main__":
    engine = HobJunterEngine()
    engine.setup()
    engine.enforce_profile()
    leads = engine.sniper_hunt()
    if leads: engine.generate_report(engine.process_leads(leads))
    else: print("No leads.")