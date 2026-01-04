import sys
import os
from hob_junter.config.settings import load_run_settings
from hob_junter.core.linkedin import fetch_linkedin_jobs

def debug_run():
    # 1. Зареждаме настройките от inputs.json (да не хардкодваме пароли)
    print("[Debug] Loading configuration...")
    settings = load_run_settings()
    
    if not settings.proxy_url:
        print("[Error] No proxy_url found in inputs.json!")
        return

    print(f"[Debug] Proxy loaded: ...@{settings.proxy_url.split('@')[-1]}")
    print(f"[Debug] Limit set to: {settings.linkedin_limit}")

    # 2. Изпълняваме скрейпинга изолирано
    # Търсим нещо популярно като "Manager", за да сме сигурни, че ще има резултати
    jobs = fetch_linkedin_jobs(
        query="Project Manager", 
        locations=["Sofia, Bulgaria"],
        proxy_url=settings.proxy_url,
        limit=settings.linkedin_limit
    )

    # 3. Репорт
    print("\n" + "="*40)
    print(f"RESULTS: Found {len(jobs)} jobs")
    print("="*40)

    for i, job in enumerate(jobs):
        print(f"{i+1}. [{job.company}] {job.title}")
        print(f"    URL: {job.apply_url}")
        print(f"    Desc Length: {len(job.description)} chars")
        print("-" * 20)

if __name__ == "__main__":
    debug_run()