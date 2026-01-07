import asyncio
import json
import urllib.parse
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List

from playwright.async_api import async_playwright

from hob_junter.config.settings import HIRING_BASE, JOBS_ENDPOINT
from hob_junter.utils.helpers import debug_print


@dataclass
class JobRecord:
    raw: Dict[str, Any]
    job_id: str
    title: str
    company: str
    apply_url: str
    source_url: str
    description: str = ""
    strategy_name: str = "Default"

    @staticmethod
    def from_api(job: Dict[str, Any], strategy_name: str = "Default") -> "JobRecord":
        job_id = str(job.get("id") or job.get("objectID") or "")
        info = job.get("job_information", {})
        processed_job = job.get("v5_processed_job_data", {})
        processed_comp = job.get("v5_processed_company_data", {})

        title = (
            info.get("title")
            or info.get("job_title_raw")
            or processed_job.get("core_job_title")
            or ""
        )
        company = (
            processed_job.get("company_name")
            or processed_comp.get("name")
            or "Unknown Company"
        )
        apply_url = job.get("apply_url") or ""
        source_url = HIRING_BASE
        description = info.get("description", "")

        return JobRecord(job, job_id, title, company, apply_url, source_url, description, strategy_name)


# Kept for legacy interactive fallback if needed
def select_roles_interactive(ai_suggestions: List[Dict[str, str]]) -> List[str]:
    if not ai_suggestions:
        return []
    return [x["role"] for x in ai_suggestions]


def input_exclusions_interactive() -> List[str]:
    # Placeholder for legacy calls
    return []


def construct_search_url(roles: List[str], locations: List[str], departments: List[str], exclusions=None) -> str:
    """
    Constructs a Hiring.Cafe URL.
    NOW: Explicit 'departments' list control.
    """
    import json
    import urllib.parse

    # 1. Base Logic for Departments
    final_departments = departments if departments else []

    # 2. Build Job Title Query
    role_queries = [f'\\"{r.strip()}\\"' for r in roles if r and r.strip()]
    query_parts = " OR ".join(role_queries)
    full_query = f"({query_parts})"

    # 3. Handle Exclusions
    if exclusions:
        if isinstance(exclusions, str):
            excl_source = exclusions.split(",")
        else:
            excl_source = exclusions
            
        excl_list = [f'NOT \\"{e.strip()}\\"' for e in excl_source if e and e.strip()]
        
        if excl_list:
            full_query += " " + " ".join(excl_list)

    # 4. FORCE BULGARIA LOCATION
    bulgaria_location = {
        "id": "QxY1yZQBoEtHp_8UEq3V",
        "types": ["country"],
        "address_components": [
            {
                "long_name": "Bulgaria",
                "short_name": "BG",
                "types": ["country"]
            }
        ],
        "formatted_address": "Bulgaria",
        "population": 7000039,
        "workplace_types": [],
        "options": {
            "flexible_regions": ["anywhere_in_continent", "anywhere_in_world"]
        }
    }
    
    final_locations = [bulgaria_location]

    # 5. Construct State Object
    state = {
        "departments": final_departments,
        "jobTitleQuery": full_query,
        "locations": final_locations
    }

    # 6. Encode and Return
    json_str = json.dumps(state)
    encoded = urllib.parse.quote(json_str)
    
    return f"https://hiring.cafe/?searchState={encoded}"


def parse_hiring_cafe_search_state_from_url(url: str) -> Dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "searchState" not in qs:
        return {}
    raw = qs["searchState"][0]
    decoded = urllib.parse.unquote(raw)
    return json.loads(decoded)


async def fetch_jobs_via_browser(strategy_urls: List[str], debug: bool = False) -> List[JobRecord]:
    """
    Iterates through a list of Strategy URLs using a single browser instance.
    Deduplicates jobs across strategies using ID.
    """
    if not strategy_urls:
        return []

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False, args=["--disable-blink-features=AutomationControlled"]
    )
    context = await browser.new_context()
    
    # Store unique jobs keyed by ID to prevent dupes across strategies
    unique_jobs_map: Dict[str, JobRecord] = {} 
    
    try:
        page = await context.new_page()
        
        for idx, url in enumerate(strategy_urls):
            # Extract strategy name/meta if possible, or just use index
            print(f"\n[Hiring] Executing Strategy {idx+1}/{len(strategy_urls)}...")
            
            # --- Per-Strategy Scraping Logic ---
            
            jobs_found_in_strategy = []
            seen_in_strategy = set()
            captured_payload = {}
            captured_url = JOBS_ENDPOINT
            captured_headers = {}
            page_size = 1000
            template_ready = asyncio.Event()

            # Network interceptor specifically for this iteration
            async def process_response(resp):
                nonlocal captured_payload, captured_url, captured_headers, page_size
                try:
                    if resp.request.method != "POST": return
                    if not resp.url.endswith("/api/search-jobs"): return

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
                        if resp.request.url: captured_url = resp.request.url

                        req_headers = resp.request.headers or {}
                        captured_headers = {
                            k: v for k, v in req_headers.items()
                            if k.lower() not in {"content-length", "host", "connection"}
                        }
                        template_ready.set()

                    try:
                        data = await resp.json()
                    except Exception: return

                    batch = []
                    if isinstance(data, list): batch = data
                    else:
                        for key in ["results", "jobs", "data", "items", "content"]:
                            if key in data and isinstance(data[key], list):
                                batch = data[key]; break

                    for item in batch:
                        # Pass Strategy ID purely for debugging/tracing
                        jr = JobRecord.from_api(item, strategy_name=f"Strategy-{idx+1}")
                        if jr.job_id not in seen_in_strategy:
                            seen_in_strategy.add(jr.job_id)
                            jobs_found_in_strategy.append(jr)

                except Exception as exc:
                    debug_print(f"[Playwright] Response error: {exc}", enabled=debug)
            def response_handler(resp):
                asyncio.create_task(process_response(resp))

            page.on("response", response_handler)

            try:
                print(f"[Hiring] Opening search...")
                await page.goto(url, wait_until="networkidle")
                
                # Cookie banner check
                try:
                    await page.wait_for_timeout(1000)
                    banner_close = page.locator('button[aria-label="Close banner"]')
                    if await banner_close.count() > 0: await banner_close.first.click()
                except Exception: pass

                # Scroll
                print("[Hiring] Scrolling feed...")
                stagnant_height = 0
                try: last_height = await page.evaluate("document.body.scrollHeight")
                except: last_height = 0

                for _ in range(40): # Cap scroll attempts
                    if page.is_closed(): break
                    try:
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
                        await page.wait_for_timeout(1200)
                        new_height = await page.evaluate("document.body.scrollHeight")
                    except Exception: break

                    if new_height <= last_height: stagnant_height += 1
                    else: stagnant_height = 0
                    last_height = new_height

                    if template_ready.is_set() and stagnant_height >= 3: break
                    if len(jobs_found_in_strategy) > 2000: break

                # API Replay for missed pages
                if captured_payload:
                    print("[Hiring] Replaying API for missed pages...")
                    current_page = captured_payload.get("page", 1) + 1
                    empty_fetches = 0
                    
                    while empty_fetches < 2:
                        try:
                            payload = dict(captured_payload)
                            payload["page"] = current_page
                            resp = await context.request.post(
                                captured_url, data=json.dumps(payload), headers=captured_headers
                            )
                            if resp.status != 200: break

                            data = await resp.json()
                            batch = []
                            if isinstance(data, list): batch = data
                            else:
                                for key in ["results", "jobs", "data", "items", "content"]:
                                    if key in data and isinstance(data[key], list):
                                        batch = data[key]; break
                            
                            if not batch:
                                empty_fetches += 1
                            else:
                                count = 0
                                for item in batch:
                                    jr = JobRecord.from_api(item, strategy_name=f"Strategy-{idx+1}")
                                    if jr.job_id not in seen_in_strategy:
                                        seen_in_strategy.add(jr.job_id)
                                        jobs_found_in_strategy.append(jr)
                                        count += 1
                                empty_fetches = 0 if count > 0 else empty_fetches + 1
                            
                            current_page += 1
                            await asyncio.sleep(0.3)
                        except Exception: break

                print(f"[Hiring] Strategy {idx+1} yielded {len(jobs_found_in_strategy)} raw jobs.")
                
                # Merge into global dict (DEDUPLICATION POINT)
                for j in jobs_found_in_strategy:
                    dedup_key = j.job_id or f"{j.company}|{j.title}"
                    if dedup_key not in unique_jobs_map:
                        unique_jobs_map[dedup_key] = j
                
                # Sleep between strategies to avoid rate limits
                if idx < len(strategy_urls) - 1:
                    wait_time = random.uniform(5.0, 10.0)
                    print(f"[Hiring] Sleeping {wait_time:.1f}s before next strategy...")
                    await asyncio.sleep(wait_time)
            finally:
                page.remove_listener("response", response_handler)

    except Exception as e:
        print(f"[Error] Browser loop crashed: {e}")
    finally:
        await browser.close()
        await p.stop()

    all_jobs = list(unique_jobs_map.values())
    print(f"[Hiring] Total UNIQUE jobs across all strategies: {len(all_jobs)}")

    # Post-process descriptions if needed
    jobs_needing_scrape = [j for j in all_jobs if j.apply_url and len(j.description) < 200]
    if jobs_needing_scrape:
        print(f"[Hiring] Fetching full descriptions for {len(jobs_needing_scrape)} jobs...")
        # Re-launch browser just for scraping details if needed (cleaner state)
        p2 = await async_playwright().start()
        browser2 = await p2.chromium.launch(headless=True) # Headless is fine for text
        ctx2 = await browser2.new_context()
        sem = asyncio.Semaphore(5)

        async def fetch_desc(job: JobRecord):
            if not job.apply_url: return
            async with sem:
                page = await ctx2.new_page()
                try:
                    await page.goto(job.apply_url, timeout=40000, wait_until="domcontentloaded")
                    content = await page.evaluate("document.body.innerText")
                    clean = " ".join(content.split())
                    if len(clean) > 200: job.description = clean
                except: pass
                finally: await page.close()

        await asyncio.gather(*[fetch_desc(j) for j in jobs_needing_scrape])
        await browser2.close()
        await p2.stop()

    return all_jobs
