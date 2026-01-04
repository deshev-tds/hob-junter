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
# --- NEW IMPORT ---
from hob_junter.core.linkedin import fetch_linkedin_jobs
# ------------------
from hob_junter.core.sheets import get_gspread_client, log_job_to_sheet 
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

    # Sheets Init
    sheets_client = None
    if run_settings.spreadsheet_id and os.path.exists(run_settings.google_creds_path):
        sheets_client = get_gspread_client(run_settings.google_creds_path)
        if sheets_client:
            print(f"[Init] Connected to Google Sheets.")
    else:
        print("[Init] Google Sheets disabled (missing ID or creds file).")

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
        print(f"[Config] Generated Hiring.Cafe URL: {search_url}")

    # Phase 3 - Harvesting (Hiring Cafe)
    print_phase_header(3, "DEPLOYING SCRAPERS")
    jobs = []

    # 3.1 Hiring Cafe (Browser)
    if search_url:
        try:
            print("[Hiring.Cafe] Starting browser automation...")
            search_state = parse_hiring_cafe_search_state_from_url(search_url)
            hc_jobs = await fetch_jobs_via_browser(search_state, debug=debug)
            jobs.extend(hc_jobs)
        except Exception as exc:
            print(f"[Hiring.Cafe] Skipped or Failed: {exc}")

    # 3.2 LinkedIn (Bright Data API)
    # --- ENTERPRISE INTEGRATION ---
    if run_settings.linkedin_enabled and run_settings.brightdata_api_token:
        print_phase_header(3.5, "LINKEDIN HARVESTER (ENTERPRISE API)")
        
        # Determine query from strategy or fallback
        li_query = "Software Engineer"
        if strategy_data.get("final_roles"):
            # Use the first 2 roles combined or just the first one
            li_query = " ".join(strategy_data["final_roles"][:1])
        
        # Use location from CV or default to Bulgaria
        li_locs = cv_profile_data.get("locations", ["Bulgaria"])
        
        # CALL THE NEW MODULE
        li_jobs = fetch_linkedin_jobs(
            query=li_query,
            locations=li_locs,
            limit=run_settings.linkedin_limit or 5
        )
        
        if li_jobs:
            print(f"[Pipeline] Merging {len(li_jobs)} LinkedIn jobs into main feed...")
            jobs.extend(li_jobs)
    else:
        if run_settings.linkedin_enabled:
            print("[Pipeline] LinkedIn enabled but NO API TOKEN found in settings.")
    # -----------------------------

    if not jobs:
        print("[Summary] No jobs found anywhere. Exiting.")
        return

    # Phase 4 - Score & Red Team
    print_phase_header(4, "SCORING & RED TEAM ANALYSIS")
    valid_jobs = [j for j in jobs if j.apply_url and len(j.description) > 50]
    
    print(f"[Pipeline] Syncing {len(valid_jobs)} candidates with Database...")
    
    new_jobs = []
    known_count = 0
    
    for job in valid_jobs:
        if is_job_processed(db_conn, job):
            known_count += 1
        else:
            new_jobs.append(job)
            
    # --- REPORT STATS ---
    print(f"\n   [+] TOTAL FEED:  {len(valid_jobs)}")
    print(f"   [-] ALREADY SEEN: {known_count}")
    print(f"   [!] QUEUED FOR AI: {len(new_jobs)}\n")
    
    if not new_jobs:
        print("[Summary] System is up to date. No new jobs to process.")
        db_conn.close()
        return

    print(f"[Pipeline] Analyzing {len(new_jobs)} candidates...\n")

    scored = []
    start_time = time.time()
    total_new = len(new_jobs)
    
    sheet_count = 0

    for i, job in enumerate(new_jobs):
        elapsed = time.time() - start_time
        processed_count = i

        if processed_count > 0:
            avg_time_per_job = elapsed / processed_count
            remaining_jobs = total_new - processed_count
            est_remaining_seconds = avg_time_per_job * remaining_jobs
            mins, secs = divmod(int(est_remaining_seconds), 60)
            eta_str = f"{mins}m {secs}s"
        else:
            eta_str = "Calc..."

        percent = ((i + 1) / total_new) * 100
        bar_length = 25
        filled_length = int(bar_length * (i + 1) // total_new)
        bar = "=" * filled_length + "-" * (bar_length - filled_length)

        comp_display = (job.company[:18] + "..") if len(job.company) > 18 else job.company

        # Update progress bar
        sys.stdout.write(
            f"\r\033[K    [{bar}] {int(percent)}% ({i+1}/{total_new}) | ETA: {eta_str} | Scoring: {comp_display}"
        )
        sys.stdout.flush()

        # --- SCORING ---
        score, reason = score_job_match(
            client=client,
            cv_profile_json=cv_profile_json,
            job=job,
            score_prompt=run_settings.score_prompt,
            scoring_mode=run_settings.scoring_mode,
            local_llm_url=LOCAL_LLM_URL,
        )

        red_team_data = {}
        # --- RED TEAM ---
        if score >= run_settings.threshold and cv_text_raw:
            sys.stdout.write(f"\n\r\033[K   [MATCH] {score}/100: {job.company} - {job.title}\n")
            sys.stdout.write("   [Red Team] Audit in progress...\n")
            sys.stdout.flush()

            red_team_data = red_team_analysis(
                cv_full_text=cv_text_raw, 
                job=job, 
                mode=run_settings.red_team_mode, 
                local_llm_url=LOCAL_LLM_URL,
                client=client
            )
            # Log back to progress line
            sys.stdout.write("   [Red Team] Complete.\n")

        scored.append((job, score, reason, red_team_data))

        # -- DB SAVE --
        mark_job_as_processed(db_conn, job, score)

        # -- SHEETS SAVE (REAL-TIME) --
        if sheets_client and score >= run_settings.threshold:
            log_job_to_sheet(sheets_client, run_settings.spreadsheet_id, job, score, reason)
            sheet_count += 1

        # Intermediate report save
        if (i + 1) % 5 == 0:
            good_matches_temp = [x for x in scored if x[1] >= run_settings.threshold]
            if good_matches_temp:
                export_jobs_html(good_matches_temp, strategy_data, report_filename)

    print("\n\n[Pipeline] Analysis complete.")
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
        if sheets_client:
            print(f"[Success] {sheet_count} high-scoring jobs logged to Google Sheets.")
    else:
        print("No matches met the threshold.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())