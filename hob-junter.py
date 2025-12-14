import os
import json
import time
import asyncio
import urllib.parse
import string
import html
import sys
import re
from datetime import datetime
from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

import requests

# OpenAI
from openai import OpenAI

# Playwright
from playwright.async_api import async_playwright

# Google OAuth
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# ==========================================
# CONFIGURATION
# ==========================================

# 1. LOCAL LLM SETTINGS
LOCAL_LLM_URL = "http://127.0.0.1:1234/v1/chat/completions"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is required")
client = OpenAI(api_key=OPENAI_API_KEY)

# Hiring.cafe
HIRING_BASE = "https://hiring.cafe"
JOBS_ENDPOINT = f"{HIRING_BASE}/api/search-jobs"

# Google OAuth
GOOGLE_CLIENT_SECRET_FILE = "client_secret.json"
GOOGLE_TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

OPENAI_MODEL = "gpt-5.1" # Adjust if needed, e.g. gpt-4o
CONFIG_FILE = "inputs.json"
DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes", "on")
DEFAULT_CV_PROFILE_PATH = "cv_profile.json"

OCR_PROMPT_DEFAULT = "Extract all human-readable text from this PDF. No formatting, no comments, no summary. Return ONLY the plain text content of the CV."

PROFILE_PROMPT_DEFAULT = """Extract a structured, ATS-oriented candidate profile from the CV text.

Return STRICT JSON with the following keys ONLY:
- summary
- skills[]
- experience[]
- preferred_roles[]
- locations[]
- seniority

HARD RULES:
- Do NOT embellish language.
- Do NOT infer skills, seniority, scope, or intent.
- Do NOT normalize leadership into IC roles.
- If information is unclear or not explicitly stated, leave the field empty.
- Prefer factual signals over descriptive language.

EXTRACTION GUIDELINES (VERY IMPORTANT):
- Preserve explicit numbers, years, dates, team sizes, budgets, SLAs, percentages, and scale indicators.
- Preserve explicit ownership and authority phrases (e.g. "led", "owned", "reported to", "appointed by", "responsible for").
- Preserve explicit scope markers (e.g. "global", "cross-regional", "enterprise", "24/7", "regulated").
- Preserve explicit domain indicators (e.g. gaming, fintech, banking, healthcare, security).

FIELD-SPECIFIC RULES:

summary:
- 2–4 sentences maximum.
- Focus on seniority level, domains, scale, and leadership scope.
- Avoid generic adjectives.

skills[]:
- Flat list of concrete technologies, methodologies, platforms, or domains.
- No soft skills unless explicitly stated in the CV.
- No duplication.

experience[]:
Each item must include ONLY information explicitly present in the CV:
{
  "company": "",
  "title": "",
  "start_date": "",
  "end_date": "",
  "description": ""
}

- The description should retain metrics, scope, and ownership signals.
- Do NOT summarize away numbers or scale.

preferred_roles[]:
- Include ONLY roles explicitly stated or clearly implied by recent titles.
- Do NOT add aspirational roles.

locations[]:
- Include explicit locations and remote eligibility ONLY if stated.

seniority:
- Use one of: "ic", "senior", "lead", "manager", "director", "executive"
- Base this STRICTLY on recent roles and scope described, not total years.

OUTPUT RULES:
- STRICT JSON ONLY.
- No markdown.
- No comments.
- No trailing text.

CV TEXT:
\"\"\"{cv_text}\"\"\"
"""


SCORE_PROMPT_DEFAULT = """You are an enterprise-grade Talent Intelligence Engine designed for objective candidate assessment.

Your task is to assess whether this candidate would realistically PASS or FAIL the screening stage for THIS specific job.

EVALUATION PROTOCOL (Execute in Order):

PHASE 1: FUNCTIONAL & JOB FAMILY VERIFICATION (The "Gatekeeper")
- Compare the PRIMARY FUNCTION of the Job vs. the Candidate's recent history.
- CRITICAL CHECKS:
  * Builder vs. Auditor: If Job is "Audit/Assurance" and Candidate is "Engineering/Implementation" (even with compliance exp) -> FUNCTIONAL MISMATCH. (Conflict of interest/skillset).
  * Sales vs. Engineering: If Job is Technical but Candidate is purely Commercial -> FUNCTIONAL MISMATCH.
  * Management vs. IC: If Job is an Individual Contributor (IC) role and Candidate is pure Management -> FUNCTIONAL MISMATCH.
- ACTION: If a mismatch is detected, set Maximum Possible Score to 60.

PHASE 2: CORE COMPETENCY & PROXY SKILLS
- Identify Top 3 Mandatory Hard Skills/Certifications.
- Check for DIRECT EVIDENCE or VALID PROXIES.
  * Valid Proxy: "Founder of Cybersecurity Firm" IS A PROXY FOR "Security Management" and "Pre-sales".
  * Invalid Proxy: "Software Engineer" IS NOT A PROXY FOR "IT Auditor" or "Finance Manager".
- If 2+ Mandatory Skills are missing with NO valid proxies -> Apply significant penalty.

PHASE 3: SENIORITY & ARCHETYPE ALIGNMENT
- Overqualification Logic:
  * Senior Leader applying for Mid-Level IC Role: ACCEPTABLE IF recent hands-on work is evident.
  * Senior Leader applying for Junior Role: FLIGHT RISK (Cap score at 40).
- Underqualification Logic:
  * IC applying for Director Role: REJECT (Cap score at 50) unless "Founder" experience exists.

PHASE 4: FINAL SCORING CALCULATION
- Base Score: 100
- Deduct 40 points if Functional Mismatch (Phase 1).
- Deduct 20 points for missing Domain Expertise (e.g., Banking, Healthcare).
- Deduct 15 points per missing Mandatory Hard Skill (if no proxy exists).
- Bonus: Add 10 points (up to 100) for "Elite Signals" (Founder, Patents, C-Level at Scale).

OUTPUT INSTRUCTIONS:
- Return STRICT JSON ONLY.
- The "reason" must be professional, evidence-based, and concise.

JSON FORMAT:
{
  "score": <number>,
  "reason": "<concise explanation focused on functional mismatches or strong transferable signals>"
}

Candidate Profile:
{cv_profile_json}

Job:
{job_title} @ {job_company}
Apply Link: {apply_url}

JOB DESCRIPTION:
{job_description}
"""


def with_retries(fn, attempts: int = 3, base_delay: float = 1.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            if i == attempts - 1:
                raise
            delay = base_delay * (2 ** i)
            print(f"[Retry] {i + 1}/{attempts} failed: {exc}. {delay:.1f}s...")
            time.sleep(delay)


def load_run_parameters():
    global DEBUG
    config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        except Exception as exc:
            print(f"[Config] Failed to read {CONFIG_FILE}: {exc}. Ignoring.")
            config = {}

    cv_path = config.get("cv_path")
    search_url = config.get("search_url")
    spreadsheet_id = config.get("spreadsheet_id")
    threshold = config.get("threshold")
    debug_cfg = config.get("debug")
    cv_profile_path = config.get("cv_profile_path") or DEFAULT_CV_PROFILE_PATH
    ocr_prompt = config.get("ocr_prompt") or OCR_PROMPT_DEFAULT
    profile_prompt = config.get("profile_prompt") or PROFILE_PROMPT_DEFAULT
    score_prompt = config.get("score_prompt") or SCORE_PROMPT_DEFAULT
    scoring_mode = config.get("scoring_mode") or "local"

    if not cv_path:
        cv_path = input("Path to CV PDF: ").strip()
    if not search_url:
        search_url = input("Hiring.cafe Search URL: ").strip()
    if not spreadsheet_id:
        spreadsheet_id = input("Google Sheet ID: ").strip()
    if threshold is None or threshold == "":
        threshold_raw = input("Minimum score for Telegram (default 65): ").strip()
        threshold = int(threshold_raw or 65)
    if debug_cfg is None:
        debug_raw = input("Enable debug logging? (y/N): ").strip().lower()
        debug_cfg = debug_raw in ("y", "yes", "1", "true", "on")

    new_config = {
        "cv_path": cv_path,
        "search_url": search_url,
        "spreadsheet_id": spreadsheet_id,
        "threshold": threshold,
        "debug": bool(debug_cfg),
        "cv_profile_path": cv_profile_path,
        "ocr_prompt": ocr_prompt,
        "profile_prompt": profile_prompt,
        "score_prompt": score_prompt,
        "scoring_mode": scoring_mode,
    }

    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(new_config, f, indent=2)
    except Exception as exc:
        print(f"[Config] Warning: failed to write {CONFIG_FILE}: {exc}")

    DEBUG = bool(debug_cfg)

    return (
        cv_path,
        search_url,
        spreadsheet_id,
        threshold,
        cv_profile_path,
        ocr_prompt,
        profile_prompt,
        score_prompt,
        scoring_mode,
    )


def debug_print(msg: str):
    if DEBUG:
        print(f"[DEBUG] {msg}")
        sys.stdout.flush()


def safe_json_loads(raw: str):
    try:
        return json.loads(raw)
    except Exception:
        return {}


def get_google_service():
    if not os.path.exists(GOOGLE_CLIENT_SECRET_FILE):
        raise FileNotFoundError(f"Missing Google client secret file at {GOOGLE_CLIENT_SECRET_FILE}")

    creds = None

    if os.path.exists(GOOGLE_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            with_retries(lambda: creds.refresh(Request()))
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                GOOGLE_CLIENT_SECRET_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(GOOGLE_TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("sheets", "v4", credentials=creds)


def ensure_sheet_exists(service, spreadsheet_id: str, sheet_name: str):
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in metadata.get("sheets", []):
        if s["properties"]["title"] == sheet_name:
            return

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
    ).execute()

    header = [["job_id", "title", "company", "apply_url", "source_url", "score", "reason"]]
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": header},
    ).execute()


def append_jobs(service, spreadsheet_id: str, sheet_name: str, jobs: List[List[str]]):
    ensure_sheet_exists(service, spreadsheet_id, sheet_name)

    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1",
        valueInputOption="RAW",
        body={"values": jobs},
    ).execute()


def extract_text_from_cv_pdf_with_gpt(pdf_path: str, ocr_prompt: str) -> str:
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"CV PDF not found: {pdf_path}")

    print("[CV] Uploading PDF to OpenAI...")
    upload = with_retries(
        lambda: client.files.create(
            file=open(pdf_path, "rb"),
            purpose="assistants"
        )
    )

    print("[CV] Requesting OCR/text extraction...")
    resp = with_retries(
        lambda: client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": ocr_prompt,
                        },
                        {
                            "type": "file",
                            "file": {"file_id": upload.id}
                        }
                    ]
                }
            ],
        )
    )

    text = resp.choices[0].message.content
    return text.strip()


def load_cv_profile_from_json(path: str) -> str:
    with open(path, "r") as f:
        data = f.read()
    try:
        parsed = json.loads(data)
        return json.dumps(parsed)
    except Exception:
        raise ValueError("CV JSON file is invalid JSON")


def save_cv_profile_to_file(profile_json: str, path: str):
    try:
        with open(path, "w") as f:
            f.write(profile_json)
        print(f"[CV] Cached profile to {path}")
    except Exception as exc:
        print(f"[CV] Warning: failed to cache profile to {path}: {exc}")


def build_cv_profile(cv_text: str, profile_prompt: str) -> str:
    prompt = profile_prompt.format(cv_text=cv_text[:20000])

    resp = with_retries(
        lambda: client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Extract structured JSON candidate profiles."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    )

    content = resp.choices[0].message.content
    try:
        parsed = json.loads(content)
        return json.dumps(parsed)
    except Exception:
        raise ValueError("Failed to parse CV profile JSON")


# ==========================================
# Job model
# ==========================================

@dataclass
class JobRecord:
    raw: Dict[str, Any]
    job_id: str
    title: str
    company: str
    apply_url: str
    source_url: str
    description: str = ""

    @staticmethod
    def from_api(job: Dict[str, Any]) -> "JobRecord":
        job_id = (
            str(job.get("id"))
            or str(job.get("jobId"))
            or job.get("slug")
            or f"{job.get('title','')}|{job.get('company_name','')}"
        )
        title = job.get("title") or job.get("jobTitle") or ""
        company = job.get("company_name") or job.get("companyName") or ""
        # The key field for external scraping:
        apply_url = job.get("apply_url") or job.get("jobUrl") or ""
        source_url = HIRING_BASE

        return JobRecord(job, job_id, title, company, apply_url, source_url, job.get("description", ""))


# ==========================================
# PLAYWRIGHT & SCRAPING LOGIC
# ==========================================

async def fetch_jobs_via_browser(search_state: Dict[str, Any]) -> List[JobRecord]:
    encoded = urllib.parse.quote(json.dumps(search_state))
    url = f"{HIRING_BASE}/?searchState={encoded}"

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = await browser.new_context()
    page = await context.new_page()

    jobs: List[JobRecord] = []
    seen = set()
    captured_payload = {}
    captured_url = JOBS_ENDPOINT
    captured_headers = {}
    page_size = 1000
    session = context.request
    template_ready = asyncio.Event()

    # 1. INTERCEPTOR
    async def process_response(resp):
        nonlocal captured_payload, captured_url, captured_headers, page_size
        try:
            if resp.request.method != "POST":
                return
            if not resp.url.endswith("/api/search-jobs"):
                return

            req_data = resp.request.post_data_json or {}
            page_val = req_data.get("page")
            
            is_candidate = (
                isinstance(req_data, dict)
                and req_data.get("searchState")
                and page_val is not None
                and page_val >= 1
            )

            if is_candidate and not captured_payload:
                captured_payload = req_data
                page_size = req_data.get("size", page_size)
                if resp.request.url:
                    captured_url = resp.request.url
                
                req_headers = resp.request.headers or {}
                captured_headers = {
                    k: v for k, v in req_headers.items()
                    if k.lower() not in {"content-length", "host", "connection"}
                }
                template_ready.set()
                debug_print("[Playwright] Captured valid search pagination template.")

            # Capture jobs from this response immediately
            try:
                data = await resp.json()
            except Exception:
                return 

            batch = []
            if isinstance(data, list):
                batch = data
            else:
                for key in ["results", "jobs", "data", "items", "content"]:
                    if key in data and isinstance(data[key], list):
                        batch = data[key]
                        break
            
            new_count = 0
            for item in batch:
                jr = JobRecord.from_api(item)
                if jr.job_id not in seen:
                    seen.add(jr.job_id)
                    jobs.append(jr)
                    new_count += 1
            if new_count > 0:
                debug_print(f"[Playwright] +{new_count} jobs from network stream.")

        except Exception as exc:
            debug_print(f"[Playwright] Response processing error: {exc}")

    page.on("response", lambda r: asyncio.create_task(process_response(r)))

    print(f"[Hiring] Opening {url}")
    await page.goto(url, wait_until="networkidle")

    # 2. BANNER & SCROLL
    try:
        banner_close = page.locator('button[aria-label="Close banner"]')
        if await banner_close.count() > 0:
            await banner_close.first.click()
    except Exception:
        pass

    # Infinite Scroll Loop
    print("[Hiring] Scrolling to load all JSON pages...")
    stagnant_height = 0
    last_height = await page.evaluate("document.body.scrollHeight")
    
    for i in range(60):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
        await page.wait_for_timeout(1500) 
        new_height = await page.evaluate("document.body.scrollHeight")
        
        if new_height <= last_height:
            stagnant_height += 1
        else:
            stagnant_height = 0
        last_height = new_height
        
        if template_ready.is_set() and stagnant_height >= 3:
            print("[Hiring] Scroll bottom reached.")
            break
        
        if len(jobs) > 3000: 
            break

    # 3. PAGINATION REPLAY (Backfill)
    if not template_ready.is_set():
         print("[Warning] No API template captured. relying only on visible network traffic.")
    elif captured_payload:
        print("[Hiring] Checking for missed pages via API replay...")
        current_page = captured_payload.get("page", 1) + 1 
        empty_fetches = 0
        
        while empty_fetches < 3:
            try:
                payload = dict(captured_payload)
                payload["page"] = current_page
                resp = await session.post(
                    captured_url,
                    data=json.dumps(payload),
                    headers=captured_headers
                )
                if resp.status != 200:
                    break
                    
                data = await resp.json()
                batch = []
                if isinstance(data, list):
                    batch = data
                else:
                    for key in ["results", "jobs", "data", "items", "content"]:
                        if key in data and isinstance(data[key], list):
                            batch = data[key]
                            break
                
                if not batch:
                    empty_fetches += 1
                else:
                    new_count = 0
                    for item in batch:
                        jr = JobRecord.from_api(item)
                        if jr.job_id not in seen:
                            seen.add(jr.job_id)
                            jobs.append(jr)
                            new_count += 1
                    if new_count == 0:
                        empty_fetches += 1
                    else:
                        empty_fetches = 0 
                
                current_page += 1
                await asyncio.sleep(0.5) 
            except Exception as exc:
                debug_print(f"[Playwright] Pagination replay error: {exc}")
                break

    print(f"[Hiring] Total unique jobs found: {len(jobs)}")
    
    # 4. EXTERNAL DESCRIPTION SCRAPING (CONCURRENT)
    await page.close() 
    
    print("[Hiring] Fetching full descriptions from external ATS links (parallel)...")
    
    sem = asyncio.Semaphore(5) # Max 5 concurrent tabs

    async def fetch_external_desc(job: JobRecord):
        target_url = job.apply_url
        if not target_url:
            return

        async with sem:
            p = await context.new_page()
            try:
                # 1. Load Page with increased timeout
                # We use domcontentloaded for speed, but add a polling wait below for dynamic content.
                await p.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                
                # 2. Smart Extraction Logic with Retry Loop
                # Many ATS sites (Workday, etc.) load the shell first, then the content. 
                # We poll for up to 10 seconds to find substantial text.
                content = ""
                for attempt in range(5):
                    try:
                        content = await p.evaluate("""() => {
                            const clone = document.body.cloneNode(true);
                            // Remove junk
                            const junk = clone.querySelectorAll('script, style, noscript, svg, nav, header, footer, button, iframe');
                            junk.forEach(el => el.remove());
                            
                            // First try innerText (cleanest)
                            let text = clone.innerText || '';
                            
                            // If too short (likely blocked by overlay), fall back to textContent
                            if (text.length < 800) {
                                text = clone.textContent || '';
                            }
                            return text;
                        }""")
                    except Exception:
                        pass # Page might be navigating or unstable
                    
                    # If we got a good chunk of text, we are done
                    if len(content.strip()) > 500:
                        break
                    
                    # Otherwise wait 2s and try again
                    await asyncio.sleep(2.0)

                # 3. Cleanup
                clean_text = " ".join(content.split())
                
                if len(clean_text) > 200:
                    job.description = clean_text
                    debug_print(f"[External] Fetched {len(clean_text)} chars for {job.company}")
                else:
                    debug_print(f"[External] Text too short ({len(clean_text)}) for {job.company}, keeping default.")
            
            except Exception as e:
                debug_print(f"[External] Timeout/Error {job.company}: {str(e)[:100]}")
            finally:
                await p.close()

    tasks = [fetch_external_desc(j) for j in jobs if j.apply_url]
    if tasks:
        await asyncio.gather(*tasks)

    await browser.close()
    await p.stop()
    return jobs


# ==========================================
# Job matching (LLM scoring)
# ==========================================

def score_job_match(cv_profile_json: str, job: JobRecord, score_prompt: str, scoring_mode: str) -> Tuple[int, str]:
    # Prepare the context
    template_vars = {
        "cv_profile_json": cv_profile_json,
        "job_title": job.title,
        "job_company": job.company,
        "apply_url": job.apply_url,
        "job_raw": json.dumps(job.raw)[:2000], 
        "job_description": (job.description or "")[:15000],
    }
    
    # FIX: Use direct string replacement
    prompt = score_prompt
    for key, val in template_vars.items():
        prompt = prompt.replace("{" + key + "}", str(val))
    
    print(f"[SCORING] Sending {job.company} - {job.title}...", flush=True)
    try:
        if scoring_mode == "openai":
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an enterprise-grade Talent Intelligence Engine designed for objective candidate assessment.\n"
                            "Your role is to strictly evaluate the candidate's provided profile against the job description based on EVIDENTIARY SUPPORT.\n"
                            "OPERATIONAL STANDARDS:\n"
                            "1. EVIDENCE OVER INFERENCE: Do not credit skills unless they are explicitly stated or strongly implied by context (e.g., 'React' implies 'JavaScript').\n"
                            "2. FUNCTIONAL MATCHING: Prioritize the *nature* of the work (e.g., Strategic vs. Execution, Engineering vs. Sales) over exact keyword matches.\n"
                            "3. SENIORITY CONTEXT: Recognize that senior leaders (Founders, Directors) possess high-level strategic skills that supersede specific IC tool requirements, provided the Core Domain matches.\n"
                            "4. NEUTRALITY: Do not penalize for formatting, gaps, or non-standard career paths unless they directly impact job qualification.\n"
                            "Output STRICT JSON ONLY."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=512,
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
        else:
            resp = requests.post(
                LOCAL_LLM_URL,
                json={
                    "model": "local-model",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are an enterprise-grade Talent Intelligence Engine designed for objective candidate assessment.\n"
                                "Your role is to strictly evaluate the candidate's provided profile against the job description based on EVIDENTIARY SUPPORT.\n"
                                "OPERATIONAL STANDARDS:\n"
                                "1. EVIDENCE OVER INFERENCE: Do not credit skills unless they are explicitly stated or strongly implied by context (e.g., 'React' implies 'JavaScript').\n"
                                "2. FUNCTIONAL MATCHING: Prioritize the *nature* of the work (e.g., Strategic vs. Execution, Engineering vs. Sales) over exact keyword matches.\n"
                                "3. SENIORITY CONTEXT: Recognize that senior leaders (Founders, Directors) possess high-level strategic skills that supersede specific IC tool requirements, provided the Core Domain matches.\n"
                                "4. NEUTRALITY: Do not penalize for formatting, gaps, or non-standard career paths unless they directly impact job qualification.\n"
                                "Output STRICT JSON ONLY."
                            )
                        },
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.0,
                    "max_tokens": 512,
                },
                timeout=120,
            )
            if resp.status_code != 200:
                print(f"[SCORING] API Error: {resp.status_code} {resp.text}")
                return 0, "API Error"
            content = resp.text

        # Clean markdown code blocks
        if "```" in content:
            match = re.search(r"```(?:json)?(.*?)```", content, re.DOTALL)
            if match:
                content = match.group(1).strip()
            
        result = safe_json_loads(content)
        return int(result.get("score", 0)), str(result.get("reason", "No reason provided"))

    except Exception as exc:
        print(f"[SCORING] Failed: {exc}", flush=True)
        return 0, f"Error: {exc}"


# ==========================================
# Telegram
# ==========================================

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Missing config (token/chat_id).")
        return

    # FIXED: Removed artifact from previous versions
    url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                chunk = text[i:i+4000]
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=10)
        else:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as exc:
        print(f"[Telegram] Error sending message: {exc}")


def summarize_jobs(jobs_with_scores):
    if not jobs_with_scores:
        return "No new matches."

    msg = f"Found {len(jobs_with_scores)} matches:\n"
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)
    
    for job, score, reason in sorted_jobs[:10]:
        msg += f"\n🔥 {score}/100 — {job.title} @ {job.company}\n🔗 {job.apply_url}\n📝 {reason[:100]}...\n"
    
    if len(sorted_jobs) > 10:
        msg += f"\n...and {len(sorted_jobs)-10} more in the HTML report."
        
    return msg


def export_jobs_html(jobs_with_scores, path: str):
    """
    Overwrites the HTML file with the full list of jobs.
    Called incrementally to simulate 'appending' to the report.
    """
    if not jobs_with_scores:
        return
    
    # Sort by score descending
    jobs_sorted = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)
    rows = []
    
    for job, score, reason in jobs_sorted:
        rows.append(
            f"<tr><td>{html.escape(job.title)}</td>"
            f"<td>{html.escape(job.company)}</td>"
            f"<td><strong>{score}</strong></td>"
            f"<td><a href=\"{html.escape(job.apply_url)}\" target=\"_blank\">Apply Now</a></td>"
            f"<td>{html.escape(reason)}</td></tr>"
        )
        
    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Job Matches</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 40px; background: #f0f2f5; color: #1c1e21; }}
    h1 {{ margin-bottom: 20px; color: #1a73e8; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 15px; border-bottom: 1px solid #e0e0e0; vertical-align: top; text-align: left; }}
    th {{ background: #f8f9fa; font-weight: 600; color: #5f6368; }}
    tr:hover {{ background: #f1f3f4; }}
    a {{ color: #1a73e8; text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
    td:nth-child(3) {{ font-size: 1.1em; color: #137333; }} /* Score column */
  </style>
</head>
<body>
  <h1>Job Matches ({len(jobs_with_scores)})</h1>
  <p style="color: #666; font-size: 0.9em;">Last Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
  <table>
    <thead>
      <tr><th>Title</th><th>Company</th><th>Score</th><th>Link</th><th>Reason</th></tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>"""
    
    with open(path, "w") as f:
        f.write(html_doc)
    
    print(f"[Report] Updated {path} with {len(jobs_with_scores)} matches.", end="\r")


# ==========================================
# PARSE searchState from URL
# ==========================================

def parse_hiring_cafe_search_state_from_url(url: str) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "searchState" not in qs:
        print("[Warning] No searchState in URL, scraping default view.")
        return {}
    raw = qs["searchState"][0]
    decoded = urllib.parse.unquote(raw)
    return json.loads(decoded)


# ==========================================
# MAIN PIPELINE
# ==========================================

async def main():
    (
        cv_path,
        search_url,
        spreadsheet_id,
        threshold,
        cv_profile_path,
        ocr_prompt,
        profile_prompt,
        score_prompt,
        scoring_mode,
    ) = load_run_parameters()

    # GENERATE UNIQUE FILENAME FOR THIS RUN
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"jobs_{timestamp}.html"
    print(f"[Init] Report will be saved to: {report_filename}")

    # Create empty file immediately so user knows it exists
    with open(report_filename, "w") as f:
        f.write(f"<html><body><h1>Job Search Started: {timestamp}</h1><p>Waiting for matches...</p></body></html>")

    # Step 1 — OCR the CV
    if cv_path.lower().endswith(".json"):
        print("[CV] Loading structured profile from JSON file…")
        cv_profile_json = load_cv_profile_from_json(cv_path)
    else:
        # Check if cache exists AND is newer than the PDF
        use_cache = False
        if os.path.exists(cv_profile_path):
            try:
                pdf_mtime = os.path.getmtime(cv_path)
                json_mtime = os.path.getmtime(cv_profile_path)
                if json_mtime > pdf_mtime:
                    use_cache = True
                else:
                    print("[CV] Detected newer PDF. Regenerating profile...")
            except OSError:
                # Fallback if file access fails
                pass

        if use_cache:
            print(f"[CV] Using cached profile: {cv_profile_path}")
            cv_profile_json = load_cv_profile_from_json(cv_profile_path)
        else:
            print("[CV] Extracting text from PDF via GPT...")
            cv_text = extract_text_from_cv_pdf_with_gpt(cv_path, ocr_prompt)
            print("[CV] Text extracted. Building profile...")
            cv_profile_json = build_cv_profile(cv_text, profile_prompt)
            save_cv_profile_to_file(cv_profile_json, cv_profile_path)
    
    # Step 2 — Parse URL
    try:
        search_state = parse_hiring_cafe_search_state_from_url(search_url)
    except Exception as e:
        print(f"[Error] Invalid URL: {e}")
        return

    # Step 3 — Scrape Jobs
    print("[Hiring] Starting browser automation...")
    jobs = await fetch_jobs_via_browser(search_state)
    
    if not jobs:
        print("[Hiring] No jobs found. Exiting.")
        return

    print(f"[Pipeline] Processing {len(jobs)} jobs for scoring...")

    # Step 4 — Score Jobs (WITH INCREMENTAL SAVING)
    scored = []
    
    # Pre-filter: only fetch/score jobs that actually have data
    valid_jobs = [j for j in jobs if j.apply_url and j.description]
    print(f"[Pipeline] {len(valid_jobs)} jobs have valid descriptions. Scoring now...")

    for i, job in enumerate(valid_jobs):
        print(f"[{i+1}/{len(valid_jobs)}] Scoring: {job.title}...", end="\r")
        score, reason = score_job_match(cv_profile_json, job, score_prompt, scoring_mode)
        scored.append((job, score, reason))
        
        # INCREMENTAL SAVE every 5 jobs
        if (i + 1) % 5 == 0:
            good_matches_temp = [(j, s, r) for (j, s, r) in scored if s >= threshold]
            
            # If matches exist, update the file.
            if good_matches_temp:
                export_jobs_html(good_matches_temp, report_filename)
            else:
                # Optional: If you want to know it's trying but finding nothing
                print(f"[Info] Scored {i+1} jobs. No matches > {threshold} yet.", end="\r")
                
    print("\n[Pipeline] Scoring complete.")

    # Step 5 — Final Report
    good_matches = [(j, s, r) for (j, s, r) in scored if s >= threshold]
    
    print(f"[Pipeline] Found {len(good_matches)} jobs above threshold {threshold}.")
    
    if good_matches:
        # Final update to ensure everything is captured
        export_jobs_html(good_matches, report_filename)
        send_telegram_message(summarize_jobs(good_matches))
        print(f"[Success] Done. Report saved to {report_filename}")
    else:
        print("No matches met the threshold.")
        # Write a "No Matches" HTML so the file isn't just the initial skeleton
        with open(report_filename, "w") as f:
            f.write(f"<html><body><h1>Job Search Completed</h1><p>No jobs met the threshold ({threshold}). Scored {len(scored)} jobs total.</p></body></html>")

if __name__ == "__main__":
    asyncio.run(main())
