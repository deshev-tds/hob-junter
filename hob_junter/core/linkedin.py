import requests
import time
import urllib.parse
from typing import List, Dict
from bs4 import BeautifulSoup
from hob_junter.core.scraper import JobRecord

# Endpoint на Bright Data Web Unlocker API
BRIGHTDATA_ENDPOINT = "https://api.brightdata.com/request"

def _brightdata_request(url: str, api_token: str, zone: str) -> str:
    """
    Helper to send any URL to Bright Data API and get back the raw HTML.
    Handles retries and basic error logging.
    """
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "zone": zone,
        "url": url,
        "format": "raw" 
    }

    print(f"   [API] >>> Requesting: {url.split('?')[0]}...")
    
    try:
        start_t = time.time()
        # Bright Data API може да отнеме време, защото реално отваря браузър отзад
        resp = requests.post(BRIGHTDATA_ENDPOINT, json=payload, headers=headers, timeout=90)
        duration = time.time() - start_t
        
        if resp.status_code == 200:
            print(f"   [API] <<< Success ({duration:.1f}s). Body len: {len(resp.text)}")
            return resp.text
        else:
            print(f"   [API] <<< Error {resp.status_code}: {resp.text[:200]}")
            if "x-brd-err-code" in resp.headers:
                print(f"   [API] X-BRD-Error: {resp.headers.get('x-brd-err-code')}")
            return ""
            
    except Exception as e:
        print(f"   [API] Exception: {e}")
        return ""

def _parse_search_results(html: str) -> List[Dict]:
    """
    Parses the HTML returned by LinkedIn Guest Search API.
    Returns a list of partial job dicts.
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    
    # LinkedIn Guest Search Cards
    cards = soup.select("li")
    
    for card in cards:
        try:
            # 1. Title
            title_tag = card.select_one(".base-search-card__title")
            title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"
            
            # 2. Company
            company_tag = card.select_one(".base-search-card__subtitle")
            company = company_tag.get_text(strip=True) if company_tag else "Unknown Company"
            
            # 3. Link & ID
            link_tag = card.select_one("a.base-card__full-link")
            url = link_tag['href'] if link_tag else ""
            
            # Clean URL to remove tracking params
            if "?" in url:
                url = url.split("?")[0]
            
            # Extract ID from URL
            job_id = ""
            if "view/" in url:
                job_id = url.split("view/")[1].split("/")[0]
            elif "-" in url:
                 job_id = url.split("-")[-1]
            
            if not job_id:
                continue # Skip if we can't get an ID
                
            jobs.append({
                "job_id": job_id,
                "title": title,
                "company": company,
                "apply_url": url,
                "description": "" # Metadata only at this stage
            })
            
        except Exception:
            continue
            
    return jobs

def _fetch_full_description(url: str, api_token: str, zone: str) -> str:
    """
    Fetches the specific job page to get the description.
    """
    html = _brightdata_request(url, api_token, zone)
    if not html:
        return ""
        
    soup = BeautifulSoup(html, "html.parser")
    
    # Selectors for description content
    selectors = [
        '.show-more-less-html__markup',
        '.description__text', 
        '.core-section-container__content',
        'div.description',
        'article'
    ]
    
    for s in selectors:
        el = soup.select_one(s)
        if el:
            text = el.get_text(separator="\n").strip()
            if len(text) > 50:
                return text
                
    # Fallback to body text if structure is weird
    return soup.get_text(separator="\n")[:5000]

def fetch_linkedin_jobs(
    query: str, 
    locations: List[str] = None, 
    proxy_url: str = None, 
    limit: int = 10,
) -> List[JobRecord]:
    
    # Dynamic settings load to get API token
    from hob_junter.config.settings import load_run_settings
    settings = load_run_settings()
    api_token = settings.brightdata_api_token
    zone = settings.brightdata_zone
    
    if not api_token:
        print("[LinkedIn] ERROR: No 'brightdata_api_token' in inputs.json. Skipping.")
        return []

    if not locations:
        locations = ["Bulgaria"]

    print(f"[LinkedIn] Enterprise Mode: Using Bright Data Web Unlocker API")
    print(f"[LinkedIn] Query: {query} | Limit: {limit}")

    all_records = []
    
    for loc in locations:
        # Construct Guest Search URL
        # e.g. https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=...
        params = {
            "keywords": query,
            "location": loc,
            "start": 0
        }
        encoded_params = urllib.parse.urlencode(params)
        search_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{encoded_params}"
        
        # 1. DISCOVERY (API Request)
        html_response = _brightdata_request(search_url, api_token, zone)
        
        if not html_response:
            print(f"[LinkedIn] Search failed for {loc}")
            continue
            
        found_jobs = _parse_search_results(html_response)
        print(f"[LinkedIn] Found {len(found_jobs)} candidates in {loc}. Fetching descriptions...")
        
        # Apply limit
        found_jobs = found_jobs[:limit]

        # 2. ENRICHMENT (API Request per Job)
        for job_meta in found_jobs:
            # Check if we already have it (Optional optimization: pass DB here later)
            
            # Fetch Description
            # Use the ID-based URL for cleaner fetching
            # https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id} is lighter than view/
            enrich_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_meta['job_id']}"
            
            desc = _fetch_full_description(enrich_url, api_token, zone)
            
            if len(desc) < 50:
                 # Fallback to main URL if guest API fails
                 desc = _fetch_full_description(job_meta['apply_url'], api_token, zone)

            # Create Record
            record = JobRecord(
                raw=job_meta,
                job_id=job_meta['job_id'],
                title=job_meta['title'],
                company=job_meta['company'],
                apply_url=job_meta['apply_url'],
                source_url="https://linkedin.com",
                description=desc if len(desc) > 50 else "[Desc Missing]"
            )
            
            print(f"   [+] Processed: {record.company} ({len(record.description)} chars)")
            all_records.append(record)
            
            # Be polite to API rate limits if necessary, though Unlocker handles concurrency well
            time.sleep(0.5)

    print(f"[LinkedIn] Harvest complete. Total: {len(all_records)}")
    return all_records