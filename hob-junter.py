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

OPENAI_MODEL = "gpt-5.1" # Adjust if needed
CONFIG_FILE = "inputs.json"
DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes", "on")
DEFAULT_CV_PROFILE_PATH = "cv_profile.json"

OCR_PROMPT_DEFAULT = "Extract all human-readable text from this PDF. No formatting, no comments, no summary. Return ONLY the plain text content of the CV."

# --- STRATEGY PROMPT ---
STRATEGY_PROMPT = """You are an elite Executive Headhunter and Career Strategist.
Analyze the candidate's CV text below. 

1. Classify the candidate's PRIMARY TARGET INDUSTRY (e.g. "Technology", "Finance", "Healthcare", "Manufacturing").
2. Determine if the target roles fall under "Information Technology", "Software Engineering", "Data", or "Product" (Tech). Set 'is_tech_industry' to true if yes.
3. Identify the 3-5 Highest Probability Job Titles this candidate should target.

OUTPUT REQUIREMENTS:
- Return STRICT JSON format.
- Structure: 
{
  "industry": "Industry Name",
  "is_tech_industry": true, 
  "suggestions": [ 
    { "role": "Exact Job Title", "reason": "Why this fits" } 
  ]
}

- "industry": Be specific.
- "is_tech_industry": Boolean. True ONLY if the role requires technical filters (Engineering/IT/Data departments).
- "role": Must be a specific, searchable job title.
- "reason": A brief, punchy explanation.

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

EXTRACTION GUIDELINES (VERY IMPORTANT):
- Preserve explicit numbers, years, dates, team sizes, budgets, SLAs, percentages, and scale indicators.
- Preserve explicit ownership and authority phrases (e.g. "led", "owned", "reported to", "appointed by", "responsible for").
- Preserve explicit scope markers (e.g. "global", "cross-regional", "enterprise", "24/7", "regulated").
- Preserve explicit domain indicators (e.g. gaming, fintech, banking, healthcare, security).

FIELD-SPECIFIC RULES:

summary:
- 2-4 sentences maximum.
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
- Include ONLY specific, searchable job titles.

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
The job of the candidate is to prove to you that they are a match with the job description provided. Yours is to evaluate that match fairly and strictly based on EVIDENTIARY SUPPORT from the candidate profile.

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
- If there is an inferred industry/domain match (e.g., Banking, Healthcare, Gaming) -> Consider as a strong positive signal.

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
# Job model (REVISED FROM API DUMP)
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
        # 1. ID
        job_id = str(job.get("id") or job.get("objectID") or "")
        
        # 2. Information Block
        info = job.get("job_information", {})
        processed_job = job.get("v5_processed_job_data", {})
        processed_comp = job.get("v5_processed_company_data", {})
        
        # 3. Title (Try nested info first, then processed)
        title = info.get("title") or info.get("job_title_raw") or processed_job.get("core_job_title") or ""
        
        # 4. Company (Try processed job data, then company data, then info)
        company = processed_job.get("company_name") or processed_comp.get("name") or "Unknown Company"
        
        # 5. URLs
        apply_url = job.get("apply_url") or ""
        source_url = HIRING_BASE
        
        # 6. Description (Now available directly in JSON!)
        description = info.get("description", "")
        # Clean up HTML tags if desired, but scoring prompt handles text. 
        # For simple text extraction we can just let the LLM handle the HTML or strip it later.

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
    print("Useful for: 'Intern', 'Junior', 'Support', 'Wordpress', 'Sales', etc.")
    print("Leave empty to skip.")
    
    user_input = input("\nExclude Keywords (comma separated) > ").strip()
    
    if not user_input:
        return []
    
    exclusions = [x.strip() for x in user_input.split(",") if x.strip()]
    if exclusions:
        print(f"[Config] Adding NOT clauses for: {', '.join(exclusions)}")
    
    return exclusions

def construct_search_url(roles: List[str], locations: List[str], is_tech_industry: bool, exclusions: List[str] = None) -> str:
    if not roles:
        print("[Config] No roles provided to build URL.")
        return ""

    search_state = {}

    # Apply IT Skeleton if Model confirmed it is a Tech industry
    if is_tech_industry:
        print(f"[Config] Tech industry detected. Applying IT Skeleton filters.")
        search_state["departments"] = [
            "Engineering",
            "Software Development",
            "Information Technology",
            "Data and Analytics"
        ]

    # Clean and Quote Roles
    cleaned_roles = [f'\\"{r.strip()}\\"' for r in roles if r.strip()]
    if not cleaned_roles:
        return ""

    # Build Query String
    query_string = f"({' OR '.join(cleaned_roles)})"
    
    # Negative Clauses
    if exclusions:
        negative_clauses = [f'NOT \\"{e}\\"' for e in exclusions]
        query_string = f"{query_string} {' '.join(negative_clauses)}"

    search_state["jobTitleQuery"] = query_string
    
    # Remote/Location
    locs_lower = [loc.lower() for loc in locations]
    if any("remote" in l for l in locs_lower):
        search_state["remote"] = "Remote"

    # Encode
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
                debug_print("[Playwright] Captured valid search pagination template.")

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
    
    # 4. EXTERNAL DESCRIPTION SCRAPING (Optimized)
    await page.close() 
    
    # Identify jobs that lack a good description from the API
    jobs_needing_scrape = [j for j in jobs if j.apply_url and len(j.description) < 200]
    
    if jobs_needing_scrape:
        print(f"[Hiring] {len(jobs_needing_scrape)} jobs need external scraping (others had data in JSON).")
        sem = asyncio.Semaphore(5) # Max 5 concurrent tabs

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
                        debug_print(f"[External] Fetched {len(clean_text)} chars for {job.company}")
                    else:
                        debug_print(f"[External] Text too short for {job.company}.")
                except Exception as e:
                    debug_print(f"[External] Timeout/Error {job.company}: {str(e)[:100]}")
                finally:
                    await p.close()

        tasks = [fetch_external_desc(j) for j in jobs_needing_scrape]
        if tasks:
            await asyncio.gather(*tasks)
    else:
        print("[Hiring] All jobs contain descriptions from the API. Skipping external scrape.")

    await browser.close()
    await p.stop()
    return jobs


# ==========================================
# Job matching (LLM scoring)
# ==========================================

def score_job_match(cv_profile_json: str, job: JobRecord, score_prompt: str, scoring_mode: str) -> Tuple[int, str]:
    # Sanitize HTML description for the prompt if needed (simple removal of tags)
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
    
    print(f"[SCORING] Sending {job.company} - {job.title}...", flush=True)
    try:
        content = ""
        
        # --- OPENAI MODE ---
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

        # --- LOCAL MODE ---
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
            
            if resp.status_code != 200:
                print(f"[SCORING] API Error: {resp.status_code} {resp.text}")
                return 0, "API Error"
            
            try:
                api_response = resp.json()
                content = api_response['choices'][0]['message']['content']
            except (KeyError, ValueError):
                content = resp.text

        # Parse JSON from Markdown
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
# Telegram & Reports
# ==========================================

def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_BOT_TOKEN}/sendMessage"
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
    sorted_jobs = sorted(jobs_with_scores, key=lambda x: x[1], reverse=True)
    
    for job, score, reason in sorted_jobs[:10]:
        msg += f"\n {score}/100 - {job.title} @ {job.company}\nLink: {job.apply_url}\nReason: {reason[:100]}...\n"
    
    if len(sorted_jobs) > 10:
        msg += f"\n...and {len(sorted_jobs)-10} more in the HTML report."
        
    return msg


def export_jobs_html(jobs_with_scores, path: str):
    if not jobs_with_scores:
        return
    
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

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"jobs_{timestamp}.html"
    print(f"[Init] Report will be saved to: {report_filename}")

    with open(report_filename, "w") as f:
        f.write(f"<html><body><h1>Job Search Started: {timestamp}</h1><p>Waiting for matches...</p></body></html>")

    # Step 1 - OCR / Cache
    if cv_path.lower().endswith(".json"):
        cv_profile_json = load_cv_profile_from_json(cv_path)
    else:
        use_cache = False
        if os.path.exists(cv_profile_path):
            try:
                if os.path.getmtime(cv_profile_path) > os.path.getmtime(cv_path):
                    use_cache = True
            except OSError:
                pass

        if use_cache:
            print(f"[CV] Using cached profile: {cv_profile_path}")
            cv_profile_json = load_cv_profile_from_json(cv_profile_path)
        else:
            print("[CV] Extracting text from PDF...")
            cv_text = extract_text_from_cv_pdf_with_gpt(cv_path, ocr_prompt)
            print("[CV] Building profile...")
            cv_profile_json = build_cv_profile(cv_text, profile_prompt)
            save_cv_profile_to_file(cv_profile_json, cv_profile_path)
    
    cv_profile_data = json.loads(cv_profile_json)
    
    # Step 2 - Strategy
    if not search_url:
        print("\n[Advisor] Initializing strategic analysis...")
        if 'cv_text' not in locals():
            cv_text_for_strategy = json.dumps(cv_profile_data, indent=2)
        else:
            cv_text_for_strategy = cv_text

        advisor_response = consult_career_advisor_gpt(cv_text_for_strategy)
        
        ai_suggestions = advisor_response.get("suggestions", [])
        industry = advisor_response.get("industry", "Unknown")
        is_tech_industry = advisor_response.get("is_tech_industry", False)
        
        print(f"\n[Advisor] Detected Industry: {industry}")
        final_roles = select_roles_interactive(ai_suggestions)
        final_exclusions = input_exclusions_interactive()
        
        search_url = construct_search_url(final_roles, cv_profile_data.get("locations", []), is_tech_industry, final_exclusions)
        print(f"[Config] Generated URL: {search_url}")
        
        if not search_url:
            return

    # Step 3 - Parse & Scrape
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

    # Step 4 - Score
    valid_jobs = [j for j in jobs if j.apply_url and len(j.description) > 50]
    print(f"[Pipeline] Processing {len(valid_jobs)} jobs for scoring...")

    scored = []
    
    for i, job in enumerate(valid_jobs):
        print(f"[{i+1}/{len(valid_jobs)}] Scoring: {job.title}...", end="\r")
        score, reason = score_job_match(cv_profile_json, job, score_prompt, scoring_mode)
        scored.append((job, score, reason))
        
        if (i + 1) % 5 == 0:
            good_matches_temp = [(j, s, r) for (j, s, r) in scored if s >= threshold]
            if good_matches_temp:
                export_jobs_html(good_matches_temp, report_filename)
                
    print("\n[Pipeline] Scoring complete.")

    good_matches = [(j, s, r) for (j, s, r) in scored if s >= threshold]
    
    if good_matches:
        export_jobs_html(good_matches, report_filename)
        send_telegram_message(summarize_jobs(good_matches))
        print(f"[Success] Report saved to {report_filename}")
    else:
        print("No matches met the threshold.")

if __name__ == "__main__":
    asyncio.run(main())