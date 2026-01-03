import asyncio
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Dict, List

import time
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

    @staticmethod
    def from_api(job: Dict[str, Any]) -> "JobRecord":
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

        return JobRecord(job, job_id, title, company, apply_url, source_url, description)


def select_roles_interactive(ai_suggestions: List[Dict[str, str]]) -> List[str]:
    if not ai_suggestions:
        print("[Advisor] AI returned no suggestions.")
        return []

    print("\n" + "=" * 60)
    print(" CAREER STRATEGY ADVISOR (GPT)")
    print("=" * 60)
    print("Based on your CV, I recommend targeting these roles:\n")

    for i, item in enumerate(ai_suggestions):
        role = item.get("role", "Unknown")
        reason = item.get("reason", "No reason provided")
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
        return [x["role"] for x in ai_suggestions]

    parts = [p.strip() for p in user_input.split(",")]
    custom_roles = []

    for p in parts:
        if p.isdigit():
            idx = int(p) - 1
            if 0 <= idx < len(ai_suggestions):
                final_roles.append(ai_suggestions[idx]["role"])
        else:
            if p:
                custom_roles.append(p)
                final_roles.append(p)

    if custom_roles:
        print(f" WARNING: Added custom roles: {', '.join(custom_roles)}")
        time.sleep(1.0)

    return list(set(final_roles))


def input_exclusions_interactive() -> List[str]:
    print("\n" + "=" * 60)
    print(" EXCLUSION PROTOCOL (Signal-to-Noise Filter)")
    print("=" * 60)
    print("Enter keywords to explicitly EXCLUDE from search results.")

    user_input = input("\nExclude Keywords (comma separated) > ").strip()

    if not user_input:
        return []

    exclusions = [x.strip() for x in user_input.split(",") if x.strip()]
    return exclusions


def construct_search_url(roles: List[str], locations: List[str], is_tech: bool, exclusions=None) -> str:
    """
    Constructs a Hiring.Cafe URL with forced Bulgaria location
    and robust type handling for exclusions.
    """
    import json
    import urllib.parse

    # 1. Base Logic for Departments
    departments = [
        "Engineering",
        "Software Development",
        "Information Technology",
        "Data and Analytics"
    ] if is_tech else []

    # 2. Build Job Title Query
    # Ensure roles are strings and quoted
    role_queries = [f'\\"{r.strip()}\\"' for r in roles if r and r.strip()]
    query_parts = " OR ".join(role_queries)
    full_query = f"({query_parts})"

    # 3. Handle Exclusions (Fixing the Crash)
    if exclusions:
        # If it's already a list, use it. If it's a string, split it.
        if isinstance(exclusions, str):
            excl_source = exclusions.split(",")
        else:
            excl_source = exclusions
            
        excl_list = [f'NOT \\"{e.strip()}\\"' for e in excl_source if e and e.strip()]
        
        if excl_list:
            full_query += " " + " ".join(excl_list)

    # 4. FORCE BULGARIA LOCATION (The Geolocation Fix)
    # This specific object tells Hiring.Cafe "Look in Bulgaria", regardless of your IP.
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
    
    # We override whatever locations came from the CV with this hardcoded target
    final_locations = [bulgaria_location]

    # 5. Construct State Object
    state = {
        "departments": departments,
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
        print("[Warning] No searchState in URL, scraping default view.")
        return {}
    raw = qs["searchState"][0]
    decoded = urllib.parse.unquote(raw)
    return json.loads(decoded)


async def fetch_jobs_via_browser(search_state: Dict[str, Any], debug: bool = False) -> List[JobRecord]:
    encoded = urllib.parse.quote(json.dumps(search_state))
    url = f"{HIRING_BASE}/?searchState={encoded}"

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=False, args=["--disable-blink-features=AutomationControlled"]
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
                    k: v
                    for k, v in req_headers.items()
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

        except Exception as exc:  # noqa: BLE001
            debug_print(f"[Playwright] Response processing error: {exc}", enabled=debug)

    page.on("response", lambda r: asyncio.create_task(process_response(r)))

    print(f"[Hiring] Opening {url}")
    await page.goto(url, wait_until="networkidle")

    try:
        banner_close = page.locator('button[aria-label="Close banner"]')
        if await banner_close.count() > 0:
            await banner_close.first.click()
    except Exception:
        pass

    print("[Hiring] Scrolling to load all JSON pages...")
    stagnant_height = 0
    last_height = await page.evaluate("document.body.scrollHeight")

    for _ in range(60):
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

    if captured_payload:
        print("[Hiring] Checking for missed pages via API replay...")
        current_page = captured_payload.get("page", 1) + 1
        empty_fetches = 0

        while empty_fetches < 3:
            try:
                payload = dict(captured_payload)
                payload["page"] = current_page
                resp = await session.post(
                    captured_url, data=json.dumps(payload), headers=captured_headers
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
            except Exception as exc:  # noqa: BLE001
                debug_print(f"[Playwright] Pagination replay error: {exc}", enabled=debug)
                break

    print(f"[Hiring] Total unique jobs found: {len(jobs)}")

    await page.close()
    jobs_needing_scrape = [j for j in jobs if j.apply_url and len(j.description) < 200]

    if jobs_needing_scrape:
        print(f"[Hiring] {len(jobs_needing_scrape)} jobs need external scraping.")
        sem = asyncio.Semaphore(5)

        async def fetch_external_desc(job: JobRecord):
            if not job.apply_url:
                return

            async with sem:
                p = await context.new_page()
                try:
                    await p.goto(job.apply_url, wait_until="domcontentloaded", timeout=45000)
                    content = ""
                    for _ in range(5):
                        try:
                            content = await p.evaluate(
                                """() => {
                                const clone = document.body.cloneNode(true);
                                const junk = clone.querySelectorAll('script, style, noscript, svg, nav, header, footer, button, iframe');
                                junk.forEach(el => el.remove());
                                let text = clone.innerText || '';
                                if (text.length < 800) text = clone.textContent || '';
                                return text;
                            }"""
                            )
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
