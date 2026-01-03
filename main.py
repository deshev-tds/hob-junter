import asyncio
import json
import os
import sys
import time
from datetime import datetime

from hob_junter.config.settings import DEFAULT_CV_TEXT_PATH, LOCAL_LLM_URL, load_env_settings, load_run_settings
from hob_junter.core.analyzer import (
    build_cv_profile,
    consult_career_advisor_gpt,
    extract_text_from_cv_pdf_with_gpt,
    red_team_analysis,
    score_job_match,
)
from hob_junter.core.database import get_db_connection, is_job_processed, mark_job_as_processed
from hob_junter.core.llm_engine import create_openai_client
from hob_junter.core.reporter import export_jobs_html, send_telegram_message, summarize_jobs
from hob_junter.core.scraper import (
    construct_search_url,
    fetch_jobs_via_browser,
    input_exclusions_interactive,
    parse_hiring_cafe_search_state_from_url,
    select_roles_interactive,
)
from hob_junter.utils.helpers import (
    load_cv_profile_from_json,
    print_phase_header,
    save_cv_profile_to_file,
)


async def run_pipeline():
    env_settings = load_env_settings()
    run_settings = load_run_settings()
    
    # DB Connection init
    db_conn = get_db_connection(run_settings.db_path)

    client = create_openai_client(env_settings.openai_api_key)
    debug = run_settings.debug

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_filename = f"jobs_{timestamp}.html"
    print(f"\n[Init] Report will be saved to: {report_filename}")

    with open(report_filename, "w") as f:
        f.write(
            f"<html><body><h1>Job Search Started: {timestamp}</h1><p>Waiting for matches...</p></body></html>"
        )

    # Phase 1 - OCR / Cache
    print_phase_header(1, "CV INTELLIGENCE & OCR")
    cv_text_raw = ""

    if run_settings.cv_path.lower().endswith(".json"):
        cv_profile_json = load_cv_profile_from_json(run_settings.cv_path)
        cv_text_raw = cv_profile_json
    else:
        use_cache = False
        cv_text_path = DEFAULT_CV_TEXT_PATH

        if os.path.exists(run_settings.cv_profile_path) and os.path.exists(cv_text_path):
            try:
                if (
                    os.path.getmtime(run_settings.cv_profile_path) > os.path.getmtime(run_settings.cv_path)
                    and os.path.getmtime(cv_text_path) > os.path.getmtime(run_settings.cv_path)
                ):
                    use_cache = True
            except OSError:
                pass

        if use_cache:
            print(f"[CV] Using cached profile & text...")
            cv_profile_json = load_cv_profile_from_json(run_settings.cv_profile_path)
            try:
                with open(cv_text_path, "r", encoding="utf-8") as f:
                    cv_text_raw = f.read()
            except Exception as exc:  # noqa: BLE001
                print(f"[CV] Warning: Failed to read cached text: {exc}")
        else:
            print("[CV] Extracting text from PDF (Fresh Run)...")
            cv_text_raw = extract_text_from_cv_pdf_with_gpt(client, run_settings.cv_path, run_settings.ocr_prompt)

            try:
                with open(cv_text_path, "w", encoding="utf-8") as f:
                    f.write(cv_text_raw)
            except Exception as exc:  # noqa: BLE001
                print(f"[CV] Warning: Failed to cache raw text: {exc}")

            print("[CV] Building profile...")
            cv_profile_json = build_cv_profile(client, cv_text_raw, run_settings.profile_prompt)
            save_cv_profile_to_file(cv_profile_json, run_settings.cv_profile_path)

    cv_profile_data = json.loads(cv_profile_json)

    # Phase 2 - Strategy
    print_phase_header(2, "STRATEGIC ALIGNMENT")
    strategy_data = {}
    search_url = run_settings.search_url

    if not search_url:
        print("[Advisor] Initializing strategic analysis...")
        cv_text_for_strategy = cv_text_raw or json.dumps(cv_profile_data, indent=2)

        advisor_response = consult_career_advisor_gpt(client, cv_text_for_strategy)

        ai_suggestions = advisor_response.get("suggestions", [])
        industry = advisor_response.get("industry", "Unknown")
        is_tech_industry = advisor_response.get("is_tech_industry", False)

        print(f"\n[Advisor] Detected Industry: {industry}")
        final_roles = select_roles_interactive(ai_suggestions)
        final_exclusions = input_exclusions_interactive()

        strategy_data = {
            "advisor_response": advisor_response,
            "final_roles": final_roles,
            "exclusions": final_exclusions,
        }

        search_url = construct_search_url(
            final_roles, cv_profile_data.get("locations", []), is_tech_industry, final_exclusions
        )
        print(f"[Config] Generated URL: {search_url}")

        if not search_url:
            return

    # Phase 3 - Parse & Scrape
    print_phase_header(3, "DEPLOYING SCRAPERS (BROWSER AUTOMATION)")
    try:
        search_state = parse_hiring_cafe_search_state_from_url(search_url)
    except Exception as exc:  # noqa: BLE001
        print(f"[Error] Invalid URL: {exc}")
        return

    print("[Hiring] Starting browser automation...")
    jobs = await fetch_jobs_via_browser(search_state, debug=debug)

    if not jobs:
        print("[Hiring] No jobs found.")
        return

    # Phase 4 - Score & Red Team
    print_phase_header(4, "SCORING & RED TEAM ANALYSIS")
    valid_jobs = [j for j in jobs if j.apply_url and len(j.description) > 50]
    print(f"[Pipeline] Processing {len(valid_jobs)} jobs for scoring...\n")

    scored = []
    start_time = time.time()
    total_jobs = len(valid_jobs)

    for i, job in enumerate(valid_jobs):
        # -- DB CHECK START --
        if is_job_processed(db_conn, job.job_id):
             sys.stdout.write(f"\r\033[K   ⏭ [Skipped - Already Seen] {job.company} - {job.title}")
             sys.stdout.flush()
             continue
        # -- DB CHECK END --
        
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

        percent = ((i + 1) / total_jobs) * 100
        bar_length = 25
        filled_length = int(bar_length * (i + 1) // total_jobs)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)

        comp_display = (job.company[:18] + "..") if len(job.company) > 18 else job.company

        sys.stdout.write(
            f"\r\033[K   ⏳ [{bar}] {int(percent)}% ({i+1}/{total_jobs}) | ETA: {eta_str} | Scoring: {comp_display}"
        )
        sys.stdout.flush()

        score, reason = score_job_match(
            client=client,
            cv_profile_json=cv_profile_json,
            job=job,
            score_prompt=run_settings.score_prompt,
            scoring_mode=run_settings.scoring_mode,
            local_llm_url=LOCAL_LLM_URL,
        )

        red_team_data = {}
        if score >= 85 and cv_text_raw:
            sys.stdout.write(f"\n\r\033[K   \033[1;32mHIGH MATCH DETECTED ({score}/100): {job.company} - {job.title}\033[0m\n")
            sys.stdout.write("   [Red Team] Engaged... (This takes a moment)\n")
            sys.stdout.flush()

            red_team_data = red_team_analysis(LOCAL_LLM_URL, cv_text_raw, job)

            sys.stdout.write("   [Red Team] Done.\n")

        scored.append((job, score, reason, red_team_data))
        
        # -- DB SAVE --
        mark_job_as_processed(db_conn, job, score)

        if (i + 1) % 5 == 0:
            good_matches_temp = [x for x in scored if x[1] >= run_settings.threshold]
            if good_matches_temp:
                export_jobs_html(good_matches_temp, strategy_data, report_filename)

    print("\n\n[Pipeline] Scoring complete.")
    db_conn.close()

    good_matches = [x for x in scored if x[1] >= run_settings.threshold]

    if good_matches:
        export_jobs_html(good_matches, strategy_data, report_filename)
        send_telegram_message(
            summarize_jobs(good_matches),
            bot_token=env_settings.telegram_bot_token,
            chat_id=env_settings.telegram_chat_id,
        )
        print(f"[Success] Report saved to {report_filename}")
    else:
        print("No matches met the threshold.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())