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

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

OPENAI_MODEL = "gpt-4o" 
CONFIG_FILE = "inputs.json"
DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes", "on")
DEFAULT_CV_PROFILE_PATH = "cv_profile.json"
DEFAULT_CV_TEXT_PATH = "cv_full_text.txt" 

OCR_PROMPT_DEFAULT = "Extract all human-readable text from this PDF. No formatting, no comments, no summary. Return ONLY the plain text content of the CV."

STRATEGY_PROMPT = """You are an elite Executive Headhunter and Career Strategist.
Analyze the candidate's CV text below. 

1. Classify the candidate's PRIMARY TARGET INDUSTRY (e.g. "Technology", "Finance", "Healthcare", "Manufacturing").
2. Determine if the target roles fall under "Information Technology", "Software Engineering", "Data", or "Product" (Tech). Set 'is_tech_industry' to true if yes.
3. Identify the 3-5 Highest Probability Job Titles this candidate should target.
4. Draft a "Candidate Archetype" summary (2 sentences) explaining their unique value proposition and professional identity.

OUTPUT REQUIREMENTS:
- Return STRICT JSON format.
- Structure: 
{
  "industry": "Industry Name",
  "is_tech_industry": true, 
  "archetype": "Brief executive summary of the candidate's persona (e.g. 'Battle-hardened DevOps Director specializing in high-frequency trading platforms...')",
  "suggestions": [ 
    { "role": "Exact Job Title", "reason": "Why this fits" } 
  ]
}

CV TEXT:
\"\"\"{cv_text}\"\"\"
"""

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

OUTPUT RULES:
- STRICT JSON ONLY.

CV TEXT:
\"\"\"{cv_text}\"\"\"
"""

SCORE_PROMPT_DEFAULT = """You are an enterprise-grade Talent Intelligence Engine designed for objective candidate assessment.
The job of the candidate is to prove to you that they are a match with the job description provided. Yours is to evaluate that match fairly and strictly based on EVIDENTIARY SUPPORT from the candidate profile.

Your task is to assess whether this candidate would realistically PASS or FAIL the screening stage for THIS specific job.

EVALUATION PROTOCOL:
- Compare PRIMARY FUNCTION of Job vs. Candidate.
- Check for DIRECT EVIDENCE of Mandatory Hard Skills.
- Check for SENIORITY & ARCHETYPE ALIGNMENT.

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

RED_TEAM_PROMPT = """ROLE: You are a skeptical, cynical Hiring Manager for a high-stakes role.
TASK: Review this Full CV against the Job Description. You are looking for reasons to REJECT.
Do NOT be polite. Find the weak spots.

JOB: {job_title} @ {job_company}

JOB DESCRIPTION:
{job_description}

FULL CANDIDATE CV:
{cv_full_text}

OUTPUT:
Return a STRICT JSON with:
1. "interview_questions": 3 "Kill Questions" specifically designed to expose weaknesses or verify vague claims.
2. "outreach_hook": A 1-sentence "Sniper" cold message to the hiring manager. It must identify their biggest likely pain point (from JD) and offer a specific solution/experience (from CV). No "I hope you are well". Straight to value.

Format:
{
  "interview_questions": ["Question 1", "Question 2", "Question 3"],
  "outreach_hook": "Your concise sniper message here."
}
"""

# ==========================================
# UX HELPERS
# ==========================================

def print_phase_header(phase_num: int, title: str):
    """Prints a fancy header for the current phase."""
    # Colors: Blue=34, Reset=0
    print(f"\n\033[34m{'='*65}\033[0m")
    print(f"\033[1;34m 🚀 PHASE {phase_num}/4: {title}\033[0m")
    print(f"\033[34m{'='*65}\033[0m")

# ==========================================
# UTILS
# ==========================================

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
    prompt = profile_prompt.replace("{cv_text}", cv_text[:20000])

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
        job_id = str(job.get("id") or job.get("objectID") or "")
        info = job.get("job_information", {})
        processed_job = job.get("v5_processed_job_data", {})
        processed_comp = job.get("v5_processed_company_data", {})
        
        title = info.get("title") or info.get("job_title_raw") or processed_job.get("core_job_title") or ""
        company = processed_job.get("company_name") or processed_comp.get("name") or "Unknown Company"
        apply_url = job.get("apply_url") or ""
        source_url = HIRING_BASE
        description = info.get("description", "")

        return JobRecord(job, job_id, title, company, apply_url, source_url, description)

# ==========================================
# DYNAMIC URL & STRATEGY
# ==========================================

def consult_career_advisor_gpt(cv_text: str) -> Dict[str, Any]:
    prompt = STRATEGY_PROMPT.replace("{cv_text}", cv_text[:20000])

    print("[Advisor] Consulting GPT for strategic role targeting & industry...")
    resp = with_retries(
        lambda: client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an executive career strategist."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4, 
            response_format={"type": "json_object"},
        )
    )

    content = resp.choices[0].message.content
    try:
        return json.loads(content)
    except Exception as e:
        print(f"[Advisor] Error parsing strategy response: {e}")
        return {}

def select_roles_interactive(ai_suggestions: List[Dict[str, str]]) -> List[str]:
    if not ai_suggestions:
        print("[Advisor] AI returned no suggestions.")
        return []

    print("\n" + "="*60)
    print(" CAREER STRATEGY ADVISOR (GPT)")
    print("="*60)
    print("Based on your CV, I recommend targeting these roles:\n")
    
    for i, item in enumerate(ai_suggestions):
        role = item.get('role', 'Unknown')
        reason = item.get('reason', 'No reason provided')
        print(f" [{i+1}] {role}")
        print(f"     -> {reason}")
        print("-" * 40)
    
    print("\nACTIONS:")
    print(" - Enter numbers to SELECT (e.g. '1, 3')")
    print(" - Press ENTER to select ALL recommended roles")
    print(" - Type text to ADD custom roles (comma separated)")
    
    user_input = input("\nYour Choice > ").strip()
    
    final_roles = []
    
    if not user_input:
        print("[Config] Selected ALL recommended roles.")
        return [x['role'] for x in ai_suggestions]

    parts = [p.strip() for p in user_input.split(",")]
    custom_roles = []
    
    for p in parts:
        if p.isdigit():
            idx = int(p) - 1
            if 0 <= idx < len(ai_suggestions):
                final_roles.append(ai_suggestions[idx]['role'])
        else:
            if p:
                custom_roles.append(p)
                final_roles.append(p)
    
    if custom_roles:
        print(f" WARNING: Added custom roles: {', '.join(custom_roles)}")
        time.sleep(1.0) 

    return list(set(final_roles))

def input_exclusions_interactive() -> List[str]:
    print("\n" + "="*60)
    print(" EXCLUSION PROTOCOL (Signal-to-Noise Filter)")
    print("="*60)
    print("Enter keywords to explicitly EXCLUDE from search results.")
    
    user_input = input("\nExclude Keywords (comma separated) > ").strip()
    
    if not user_input:
        return []
    
    exclusions = [x.strip() for x in user_input.split(",") if x.strip()]
    return exclusions

def construct_search_url(roles: List[str], locations: List[str], is_tech_industry: bool, exclusions: List[str] = None) -> str:
    if not roles:
        print("[Config] No roles provided to build URL.")
        return ""

    search_state = {}
    if is_tech_industry:
        search_state["departments"] = ["Engineering", "Software Development", "Information Technology", "Data and Analytics"]

    cleaned_roles = [f'\\"{r.strip()}\\"' for r in roles if r.strip()]
    if not cleaned_roles:
        return ""

    query_string = f"({' OR '.join(cleaned_roles)})"
    
    if exclusions:
        negative_clauses = [f'NOT \\"{e}\\"' for e in exclusions]
        query_string = f"{query_string} {' '.join(negative_clauses)}"

    search_state["jobTitleQuery"] = query_string
    
    locs_lower = [loc.lower() for loc in locations]
    if any("remote" in l for l in locs_lower):
        search_state["remote"] = "Remote"

    json_str = json.dumps(search_state)
    encoded_state = urllib.parse.quote(json_str)
    
    return f"{HIRING_BASE}/?searchState={encoded_state}"

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
            
            for item in batch:
                jr = JobRecord.from_api(item)
                if jr.job_id not in seen:
                    seen.add(jr.job_id)
                    jobs.append(jr)

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
    if captured_payload:
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
    
    # 4. EXTERNAL DESCRIPTION SCRAPING
    await page.close() 
    jobs_needing_scrape = [j for j in jobs if j.apply_url and len(j.description) < 200]
    
    if jobs_needing_scrape:
        print(f"[Hiring] {len(jobs_needing_scrape)} jobs need external scraping.")
        sem = asyncio.Semaphore(5) 

        async def fetch_external_desc(job: JobRecord):
            target_url = job.apply_url
            if not target_url:
                return

            async with sem:
                p = await context.new_page()
                try:
                    await p.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    content = ""
                    for attempt in range(5):
                        try:
                            content = await p.evaluate("""() => {
                                const clone = document.body.cloneNode(true);
                                const junk = clone.querySelectorAll('script, style, noscript, svg, nav, header, footer, button, iframe');
                                junk.forEach(el => el.remove());
                                let text = clone.innerText || '';
                                if (text.length < 800) text = clone.textContent || '';
                                return text;
                            }""")
                        except Exception:
                            pass
                        
                        if len(content.strip()) > 500:
                            break
                        await asyncio.sleep(2.0)

                    clean_text = " ".join(content.split())
                    if len(clean_text) > 200:
                        job.description = clean_text
                except Exception:
                    pass
                finally:
                    await p.close()

        tasks = [fetch_external_desc(j) for j in jobs_needing_scrape]
        if tasks:
            await asyncio.gather(*tasks)

    await browser.close()
    await p.stop()
    return jobs


# ==========================================
# Job matching (LLM scoring) & RED TEAM
# ==========================================

def score_job_match(cv_profile_json: str, job: JobRecord, score_prompt: str, scoring_mode: str) -> Tuple[int, str]:
    clean_desc = re.sub('<[^<]+?>', ' ', job.description)
    
    template_vars = {
        "cv_profile_json": cv_profile_json,
        "job_title": job.title,
        "job_company": job.company,
        "apply_url": job.apply_url,
        "job_raw": json.dumps(job.raw)[:2000], 
        "job_description": clean_desc[:15000],
    }
    
    prompt = score_prompt
    for key, val in template_vars.items():
        prompt = prompt.replace("{" + key + "}", str(val))
    
    # SILENT MODE for function (we handle printing in main)
    # print(f"[SCORING] Sending {job.company} - {job.title}...", flush=True) 
    
    try:
        content = ""
        if scoring_mode == "openai":
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": "You are a talent intelligence engine. Output STRICT JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_completion_tokens=512,
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
        else:
            resp = requests.post(
                LOCAL_LLM_URL,
                json={
                    "model": "local-model",
                    "messages": [
                        {"role": "system", "content": "You are a talent intelligence engine. Output STRICT JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.0,
                    "max_tokens": 512,
                },
                timeout=120,
            )
            try:
                api_response = resp.json()
                content = api_response['choices'][0]['message']['content']
            except (KeyError, ValueError):
                content = resp.text

        if "```" in content:
            match = re.search(r"```(?:json)?(.*?)```", content, re.DOTALL)
            if match:
                content = match.group(1).strip()
            
        result = safe_json_loads(content)
        return int(result.get("score", 0)), str(result.get("reason", "No reason provided"))

    except Exception as exc:
        # print(f"[SCORING] Failed: {exc}", flush=True)
        return 0, f"Error: {exc}"


def red_team_analysis(cv_full_text: str, job: JobRecord) -> Dict[str, Any]:
    """
    Simulates a hostile Hiring Manager reading the full CV.
    RUNS LOCALLY via Qwen/MLX to save tokens and privacy.
    """
    clean_desc = re.sub('<[^<]+?>', ' ', job.description)
    
    # Inject variables
    prompt = RED_TEAM_PROMPT.replace("{job_title}", job.title)
    prompt = prompt.replace("{job_company}", job.company)
    prompt = prompt.replace("{job_description}", clean_desc[:10000])
    prompt = prompt.replace("{cv_full_text}", cv_full_text[:20000])
    
    try:
        # --- LOCAL LLM SWITCH ---
        # Using local endpoint instead of OpenAI
        resp = requests.post(
            LOCAL_LLM_URL,
            json={
                "model": "local-model",
                "messages": [
                    {"role": "system", "content": "You are a cynical, hostile hiring manager. Output STRICT JSON only. No markdown, no pre-amble."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.7, 
                "max_tokens": 1024,
            },
            timeout=180, 
        )
        
        if resp.status_code != 200:
            print(f"[RedTeam] API Error: {resp.status_code} {resp.text}")
            return {}

        try:
            api_response = resp.json()
            content = api_response['choices'][0]['message']['content']
        except (KeyError, ValueError):
            content = resp.text

        # Sanitize JSON 
        if "```" in content:
            match = re.search(r"```(?:json)?(.*?)```", content, re.DOTALL)
            if match:
                content = match.group(1).strip()
        
        return safe_json_loads(content)

    except Exception as e:
        print(f"[RedTeam] Failed for {job.company}: {e}")
        return {}


# ==========================================
# Telegram & Reports
# ==========================================

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        if len(text) > 4000:
            for i in range(0, len(text), 4000):
                chunk = text[i:i+4000]
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=10)
        else:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as exc:
        print(f"[Telegram] Error: {exc}")


def summarize_jobs(jobs_with_scores):
    if not jobs_with_scores:
        return "No new matches."

    msg = f"Found {len(jobs_with_scores)} matches:\n"
    # Unpack 4 elements (job, score, reason, red_team_data)
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)
    
    for job, score, reason, red_team_data in sorted_jobs[:10]:
        msg += f"\n {score}/100 - {job.title} @ {job.company}\nLink: {job.apply_url}\nReason: {reason[:100]}...\n"
    
    if len(sorted_jobs) > 10:
        msg += f"\n...and {len(sorted_jobs)-10} more in the HTML report."
        
    return msg


def export_jobs_html(jobs_with_scores, strategy_data, path: str):
    if not jobs_with_scores:
        return

    if not strategy_data: strategy_data = {}
    advisor = strategy_data.get("advisor_response", {})
    final_roles = strategy_data.get("final_roles", [])
    exclusions = strategy_data.get("exclusions", [])
    
    archetype = advisor.get("archetype", "N/A")
    ai_suggestions = advisor.get("suggestions", [])
    
    suggestions_html = ""
    for sugg in ai_suggestions:
        suggestions_html += f"<li><strong>{sugg.get('role')}</strong>: {sugg.get('reason')}</li>"
        
    header_html = f"""
    <div class="strategy-box">
      <div class="strategy-header">
        <div>
          <h2>MISSION DOSSIER: <span style="color:#1a73e8">{archetype}</span></h2>
          <p><strong>Target Industry:</strong> {advisor.get('industry', 'Unknown')}</p>
        </div>
        <div class="stats-box">
           <div><strong>Matches Found:</strong> {len(jobs_with_scores)}</div>
           <div><strong>Active Filters:</strong> {len(final_roles)} Roles</div>
        </div>
      </div>
      
      <div class="grid-container">
        <div class="panel">
           <h3> AI Strategic Assessment</h3>
           <p style="font-size:0.9em; color:#555;">Based on your profile, the following opportunities offer the highest probability of success:</p>
           <ul class="suggestion-list">{suggestions_html}</ul>
        </div>
        
        <div class="panel">
           <h3>⚡ Active Search Parameters</h3>
           <p style="font-size:0.9em; color:#555;">These are the actual keywords and filters currently being hunted:</p>
           <div class="tag-container">
             {''.join([f'<span class="tag tag-role">{r}</span>' for r in final_roles])}
           </div>
           
           {f'<h4>Exclusions (NOT):</h4><div class="tag-container">' + ''.join([f'<span class="tag tag-exclude">{e}</span>' for e in exclusions]) + '</div>' if exclusions else ''}
        </div>
      </div>
    </div>
    """

    rows = []
    # Sort and Unpack
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)
    
    for job, score, reason, red_team_data in sorted_jobs:
        color = "#137333" if score >= 80 else "#f9ab00" if score >= 60 else "#d93025"
        
        # Build Red Team HTML if present
        red_team_html = ""
        if red_team_data and score >= 85:
            questions = "<li>" + "</li><li>".join(red_team_data.get('interview_questions', [])) + "</li>"
            hook = red_team_data.get('outreach_hook', 'N/A')
            red_team_html = f"""
            <div style="background: #fff0f0; padding: 12px; margin-top: 10px; border-left: 4px solid #d93025; font-size: 0.9em; border-radius: 4px;">
                <strong style="color: #b71c1c;"> Red Team Analysis (Kill Questions):</strong>
                <ul style="margin: 5px 0 10px 20px; color: #333;">{questions}</ul>
                <div style="background: #e3f2fd; padding: 8px; border-left: 4px solid #1976d2; color: #0d47a1; margin-top: 5px;">
                    <strong>📧 Sniper Outreach:</strong> "{html.escape(hook)}"
                </div>
            </div>
            """

        rows.append(
            f"<tr><td><div class='job-title'>{html.escape(job.title)}</div><div class='job-comp'>{html.escape(job.company)}</div></td>"
            f"<td><span style='font-size:1.2em; font-weight:bold; color:{color}'>{score}</span></td>"
            f"<td><a href='{html.escape(job.apply_url)}' target='_blank' class='btn'>Apply</a></td>"
            f"<td class='reason-cell'>{html.escape(reason)}{red_team_html}</td></tr>"
        )
        
    html_doc = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Hob-Junter Intelligence Report</title>
  <style>
    body {{ font-family: 'Segoe UI', Roboto, Helvetica, sans-serif; margin: 0; background: #f4f6f8; color: #172b4d; }}
    .container {{ max-width: 1200px; margin: 40px auto; padding: 0 20px; }}
    
    .strategy-box {{ background: white; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); padding: 25px; margin-bottom: 30px; border-top: 5px solid #1a73e8; }}
    .strategy-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px; }}
    .strategy-header h2 {{ margin: 0; font-size: 1.4em; }}
    .stats-box {{ text-align: right; font-size: 0.9em; color: #5e6c84; }}
    
    .grid-container {{ display: grid; grid-template-columns: 1fr 1fr; gap: 30px; }}
    .panel h3 {{ margin-top: 0; color: #091e42; font-size: 1.1em; border-bottom: 2px solid #dfe1e6; padding-bottom: 8px; display: inline-block; }}
    .suggestion-list {{ padding-left: 20px; font-size: 0.9em; color: #333; }}
    .suggestion-list li {{ margin-bottom: 8px; }}
    
    .tag-container {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }}
    .tag {{ padding: 4px 10px; border-radius: 4px; font-size: 0.85em; font-weight: 500; }}
    .tag-role {{ background: #e3f2fd; color: #0d47a1; border: 1px solid #bbdefb; }}
    .tag-exclude {{ background: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }}

    table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-radius: 8px; overflow: hidden; }}
    th, td {{ padding: 15px; border-bottom: 1px solid #ebecf0; vertical-align: middle; text-align: left; }}
    th {{ background: #fafbfc; font-weight: 600; color: #5e6c84; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.05em; }}
    tr:hover {{ background: #f4f5f7; }}
    
    .job-title {{ font-weight: 600; font-size: 1.05em; color: #172b4d; }}
    .job-comp {{ font-size: 0.9em; color: #6b778c; margin-top: 2px; }}
    .reason-cell {{ font-size: 0.9em; color: #42526e; line-height: 1.5; }}
    
    .btn {{ display: inline-block; padding: 6px 12px; background: #0052cc; color: white; text-decoration: none; border-radius: 3px; font-size: 0.9em; font-weight: 500; }}
    .btn:hover {{ background: #0065ff; }}
  </style>
</head>
<body>
  <div class="container">
    {header_html}
    
    <table>
      <thead>
        <tr><th style="width: 30%">Role</th><th style="width: 10%">Score</th><th style="width: 10%">Action</th><th>Analysis</th></tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
    <p style="text-align: center; color: #888; font-size: 0.8em; margin-top: 30px;">Generated by Hob-Junter at {datetime.now().strftime('%H:%M:%S')}</p>
  </div>
</body>
</html>"""
    
    with open(path, "w") as f:
        f.write(html_doc)
    
    # Just quiet update, we show progress in console
    # print(f"[Report] Updated {path} with {len(jobs_with_scores)} matches.", end="\r")


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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"jobs_{timestamp}.html"
    print(f"\n[Init] Report will be saved to: {report_filename}")

    with open(report_filename, "w") as f:
        f.write(f"<html><body><h1>Job Search Started: {timestamp}</h1><p>Waiting for matches...</p></body></html>")

    # Step 1 - OCR / Cache
    print_phase_header(1, "CV INTELLIGENCE & OCR")
    cv_text_raw = ""
    
    if cv_path.lower().endswith(".json"):
        cv_profile_json = load_cv_profile_from_json(cv_path)
        cv_text_raw = cv_profile_json 
    else:
        use_cache = False
        cv_text_path = DEFAULT_CV_TEXT_PATH
        
        if os.path.exists(cv_profile_path) and os.path.exists(cv_text_path):
            try:
                if (os.path.getmtime(cv_profile_path) > os.path.getmtime(cv_path) and 
                    os.path.getmtime(cv_text_path) > os.path.getmtime(cv_path)):
                    use_cache = True
            except OSError:
                pass

        if use_cache:
            print(f"[CV] Using cached profile & text...")
            cv_profile_json = load_cv_profile_from_json(cv_profile_path)
            try:
                with open(cv_text_path, "r", encoding="utf-8") as f:
                    cv_text_raw = f.read()
            except Exception as e:
                print(f"[CV] Warning: Failed to read cached text: {e}")
        else:
            print("[CV] Extracting text from PDF (Fresh Run)...")
            cv_text_raw = extract_text_from_cv_pdf_with_gpt(cv_path, ocr_prompt)
            
            try:
                with open(cv_text_path, "w", encoding="utf-8") as f:
                    f.write(cv_text_raw)
            except Exception as e:
                print(f"[CV] Warning: Failed to cache raw text: {e}")

            print("[CV] Building profile...")
            cv_profile_json = build_cv_profile(cv_text_raw, profile_prompt)
            save_cv_profile_to_file(cv_profile_json, cv_profile_path)
    
    cv_profile_data = json.loads(cv_profile_json)
    
    # Step 2 - Strategy
    print_phase_header(2, "STRATEGIC ALIGNMENT")
    strategy_data = {}
    if not search_url:
        print("[Advisor] Initializing strategic analysis...")
        if not cv_text_raw:
             cv_text_for_strategy = json.dumps(cv_profile_data, indent=2)
        else:
             cv_text_for_strategy = cv_text_raw

        advisor_response = consult_career_advisor_gpt(cv_text_for_strategy)
        
        ai_suggestions = advisor_response.get("suggestions", [])
        industry = advisor_response.get("industry", "Unknown")
        is_tech_industry = advisor_response.get("is_tech_industry", False)
        
        print(f"\n[Advisor] Detected Industry: {industry}")
        final_roles = select_roles_interactive(ai_suggestions)
        final_exclusions = input_exclusions_interactive()
        
        strategy_data = {
            "advisor_response": advisor_response,
            "final_roles": final_roles,
            "exclusions": final_exclusions
        }
        
        search_url = construct_search_url(final_roles, cv_profile_data.get("locations", []), is_tech_industry, final_exclusions)
        print(f"[Config] Generated URL: {search_url}")
        
        if not search_url:
            return

    # Step 3 - Parse & Scrape
    print_phase_header(3, "DEPLOYING SCRAPERS (BROWSER AUTOMATION)")
    try:
        search_state = parse_hiring_cafe_search_state_from_url(search_url)
    except Exception as e:
        print(f"[Error] Invalid URL: {e}")
        return

    print("[Hiring] Starting browser automation...")
    jobs = await fetch_jobs_via_browser(search_state)
    
    if not jobs:
        print("[Hiring] No jobs found.")
        return

    # Step 4 - Score & Red Team
    print_phase_header(4, "SCORING & RED TEAM ANALYSIS")
    valid_jobs = [j for j in jobs if j.apply_url and len(j.description) > 50]
    print(f"[Pipeline] Processing {len(valid_jobs)} jobs for scoring...\n")

    scored = []
    start_time = time.time()
    total_jobs = len(valid_jobs)
    
    for i, job in enumerate(valid_jobs):
        # 1. CALCULATE ETA
        elapsed = time.time() - start_time
        processed_count = i
        
        if processed_count > 0:
            avg_time_per_job = elapsed / processed_count
            remaining_jobs = total_jobs - processed_count
            est_remaining_seconds = avg_time_per_job * remaining_jobs
            mins, secs = divmod(int(est_remaining_seconds), 60)
            eta_str = f"{mins}m {secs}s"
        else:
            eta_str = "Calc..."
            
        # 2. BUILD PROGRESS BAR
        percent = ((i + 1) / total_jobs) * 100
        bar_length = 25
        filled_length = int(bar_length * (i + 1) // total_jobs)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        
        # 3. PRINT STATUS (Overwriting line with \r)
        # We assume 15-20 chars for Company to avoid line wrapping
        comp_display = (job.company[:18] + '..') if len(job.company) > 18 else job.company
        
        sys.stdout.write(f"\r\033[K   ⏳ [{bar}] {int(percent)}% ({i+1}/{total_jobs}) | ETA: {eta_str} | Scoring: {comp_display}")
        sys.stdout.flush()
        
        # 4. PERFORM ACTION
        score, reason = score_job_match(cv_profile_json, job, score_prompt, scoring_mode)
        
        red_team_data = {}
        # Only run Red Team for high scores (e.g. >= 85)
        if score >= 85 and cv_text_raw:
             # CLEAR LINE & PRINT ALERT
             sys.stdout.write(f"\n\r\033[K   \033[1;32mHIGH MATCH DETECTED ({score}/100): {job.company} - {job.title}\033[0m\n")
             sys.stdout.write(f"   [Red Team] Engaged... (This takes a moment)\n")
             sys.stdout.flush()
             
             red_team_data = red_team_analysis(cv_text_raw, job)
             
             # Print completion of Red Team so user knows we are moving on
             sys.stdout.write(f"   [Red Team] Done.\n")
        
        scored.append((job, score, reason, red_team_data))
        
        if (i + 1) % 5 == 0:
            good_matches_temp = [x for x in scored if x[1] >= threshold]
            if good_matches_temp:
                export_jobs_html(good_matches_temp, strategy_data, report_filename)
                
    print("\n\n[Pipeline] Scoring complete.")

    good_matches = [x for x in scored if x[1] >= threshold]
    
    if good_matches:
        export_jobs_html(good_matches, strategy_data, report_filename)
        send_telegram_message(summarize_jobs(good_matches))
        print(f"[Success] Report saved to {report_filename}")
    else:
        print("No matches met the threshold.")

if __name__ == "__main__":
    asyncio.run(main())