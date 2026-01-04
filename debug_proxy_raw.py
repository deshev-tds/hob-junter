import requests
import http.client
import logging
from hob_junter.config.settings import load_run_settings

# 1. Включваме "God Mode" на логовете - виждаш всеки байт
http.client.HTTPConnection.debuglevel = 1

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)
requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True

def test_proxy_connection():
    settings = load_run_settings()
    proxy = settings.proxy_url
    
    if not proxy:
        print("NO PROXY CONFIGURED")
        return

    print(f"\n[DEBUG] Testing Proxy: {proxy.split('@')[-1]}")
    
    proxies = {
        "http": proxy,
        "https": proxy
    }

    # Тестваме с verify=False (еквивалент на curl -k)
    print("\n" + "="*50)
    print("TEST 1: Request to LinkedIn Guest API (verify=False)")
    print("="*50)
    
    try:
        # Това е URL-ът, който JobSpy се опитва да достъпи
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=Project+Manager&location=Bulgaria"
        
        resp = requests.get(
            url, 
            proxies=proxies, 
            verify=False, # <--- ТОВА Е КЛЮЧЪТ
            timeout=10,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        
        print(f"\n[RESULT] Status: {resp.status_code}")
        print(f"[RESULT] Headers: {dict(resp.headers)}")
        print(f"[RESULT] Body Snippet: {resp.text[:200]}")
        
    except Exception as e:
        print(f"\n[CRITICAL FAILURE] {e}")

if __name__ == "__main__":
    test_proxy_connection()