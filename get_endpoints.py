import asyncio
import json
import sys
from playwright.async_api import async_playwright

# TARGET
URL = "https://hiring.cafe"
API_ENDPOINT = "api/search-jobs"

async def main():
    async with async_playwright() as p:
        # Launch visible browser
        browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        page = await browser.new_page()

        print(f"[*] Attaching interceptor for '{API_ENDPOINT}'...")

        # INTERCEPTOR: The moment we see the JSON, dump it and die
        async def handle_response(response):
            if API_ENDPOINT in response.url and response.request.method == "POST":
                try:
                    data = await response.json()
                    # Hiring.cafe usually wraps jobs in a list, sometimes nested
                    jobs = []
                    if isinstance(data, list): 
                        jobs = data
                    elif isinstance(data, dict):
                        # Try common keys
                        for k in ["results", "jobs", "data", "hits"]:
                            if k in data and isinstance(data[k], list):
                                jobs = data[k]
                                break
                    
                    if jobs:
                        first_job = jobs[0]
                        print("\n" + "="*50)
                        print(f" [SUCCESS] INTERCEPTED {len(jobs)} JOBS. DUMPING FIRST RECORD:")
                        print("="*50)
                        print(json.dumps(first_job, indent=2))
                        print("="*50 + "\n")
                        
                        # Exit immediately after capturing
                        await browser.close()
                        sys.exit(0)
                        
                except Exception as e:
                    print(f"[!] Error parsing JSON: {e}")

        page.on("response", handle_response)

        print(f"[*] Navigating to {URL}...")
        await page.goto(URL, wait_until="networkidle")
        
        # Scroll a tiny bit to force trigger if needed
        await page.evaluate("window.scrollTo(0, 500)")
        
        # Keep open briefly to allow network capture
        print("[*] Waiting for network traffic...")
        await asyncio.sleep(10)
        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        pass # Clean exit