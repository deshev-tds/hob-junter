import asyncio
import json
import os
import sys
import time
from datetime import datetime
from typing import List, Dict

from hob_junter.config.settings import (
    DEFAULT_CV_TEXT_PATH, 
    LOCAL_LLM_URL, 
    load_env_settings, 
    load_run_settings,
    TARGET_DEPARTMENTS
)
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
)
from hob_junter.core.sheets import get_gspread_client, log_job_to_sheet 
from hob_junter.utils.helpers import (
    load_cv_profile_from_json,
    print_phase_header,
    save_cv_profile_to_file,
)


def load_strategies(path: str) -> List[Dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Error] Failed to load strategies: {e}")
        return []


def save_strategies(path: str, strategies: List[Dict]):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(strategies, f, indent=2)
    print(f"[Config] Saved {len(strategies)} strategies to {path}")


def interactive_setup_wizard(cv_profile_data: Dict, client, run_settings) -> List[Dict]:
    print_phase_header(2, "STRATEGIC SETUP (AI ARCHITECT)")
    
    print("[Advisor] Analyzing CV against Hiring.Cafe taxonomy...")
    cv_text_summary = json.dumps(cv_profile_data, indent=2)
    
    # Calls the new STRATEGY_PROMPT which returns "strategies" list
    advisor_response = consult_career_advisor_gpt(client, cv_text_summary)
    
    archetype = advisor_response.get("archetype", "Candidate")
    suggested_strategies = advisor_response.get("strategies", [])
    
    print(f"\n[Analysis] Candidate Archetype: \033[1m{archetype}\033[0m")
    
    final_strategies = []

    # Review AI suggestions
    if suggested_strategies:
        print(f"[Advisor] Generated {len(suggested_strategies)} search configurations based on valid departments.")
        
        for i, strat in enumerate(suggested_strategies):
            print(f"\n--- Strategy Option {i+1}: {strat['name']} ---")
            print(f"    Keywords: {strat['roles']}")
            print(f"    Exclusions: {strat['exclusions']}")
            print(f"    Departments: {strat['departments']}")
            
            choice = input("    Keep this strategy? (Y/n/edit): ").strip().lower()
            
            if choice in ("n", "no"):
                continue
            elif choice == "edit":
                # Simple edit mode if AI missed something
                r_in = input(f"    New Keywords (current: {','.join(strat['roles'])}): ")
                if r_in: strat['roles'] = [x.strip() for x in r_in.split(",")]
                
                e_in = input(f"    New Exclusions (current: {','.join(strat['exclusions'])}): ")
                if e_in: strat['exclusions'] = [x.strip() for x in e_in.split(",")]
                
                final_strategies.append(strat)
            else:
                # Default YES
                final_strategies.append(strat)

    # Fallback / Manual Add
    if not final_strategies:
        print("\n[!] No AI strategies selected. Creating default fallback...")
        final_strategies.append({
            "name": "Broad Tech Leadership (Default)",
            "roles": ["Head", "Director", "VP", "Manager", "Lead", "Chief", "CTO", "Principal"],
            "exclusions": ["Sales", "Marketing", "HR", "Recruiter", "Intern", "Junior"],
            "departments": TARGET_DEPARTMENTS # Using the constant from settings
        })

    return final_strategies


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

    # Phase 1 - OCR / Cache
    print_phase_header(1, "CV INTELLIGENCE & OCR")
    cv_text_raw = ""

    if run_settings.cv_path.lower().endswith(".json"):
        cv_profile_json = load_cv_profile_from_json(run_settings.cv_path)
        cv_text_raw = cv_profile_json
    else:
        # Cache logic same as before...
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
            except Exception:
                pass
        else:
            print("[CV] Extracting text from PDF (Fresh Run)...")
            cv_text_raw = extract_text_from_cv_pdf_with_gpt(client, run_settings.cv_path, run_settings.ocr_prompt)
            with open(cv_text_path, "w", encoding="utf-8") as f: f.write(cv_text_raw)
            print("[CV] Building profile...")
            cv_profile_json = build_cv_profile(client, cv_text_raw, run_settings.profile_prompt)
            save_cv_profile_to_file(cv_profile_json, run_settings.cv_profile_path)

    cv_profile_data = json.loads(cv_profile_json)

    # Phase 2 - Strategy Loading / Setup
    strategies = load_strategies(run_settings.strategies_path)
    
    # Check if we need to run setup
    force_setup = "--setup" in sys.argv
    if not strategies or force_setup:
        print("\n[Config] No strategies found (or --setup flag used). Entering interactive setup...")
        strategies = interactive_setup_wizard(cv_profile_data, client, run_settings)
        if not strategies:
            print("[Error] No strategies defined. Exiting.")
            return
        save_strategies(run_settings.strategies_path, strategies)
    else:
        print(f"\n[Config] Loaded {len(strategies)} strategies from {run_settings.strategies_path}")

    # Generate URLs
    print_phase_header(2, "STRATEGIC ALIGNMENT")
    target_urls = []
    
    # We grab location from CV or default to Bulgaria inside construct_search_url
    locations = cv_profile_data.get("locations", []) 

    for strat in strategies:
        url = construct_search_url(
            roles=strat["roles"],
            locations=locations, # Currently ignored by construct_search_url in favor of hardcoded BG
            departments=strat["departments"],
            exclusions=strat["exclusions"]
        )
        print(f" [+] Strategy '{strat['name']}':")
        print(f"     Roles: {strat['roles']}")
        print(f"     Depts: {strat['departments']}")
        print(f"     URL:   {url[:60]}...")
        target_urls.append(url)

    # Phase 3 - Scrape
    print_phase_header(3, "DEPLOYING SCRAPERS (MULTI-STRATEGY)")
    
    # Pass LIST of URLs now
    jobs = await fetch_jobs_via_browser(target_urls, debug=debug)

    if not jobs:
        print("[Hiring] No jobs found across all strategies.")
        return

    # Phase 4 - Score & Red Team (Same as before)
    print_phase_header(4, "SCORING & RED TEAM ANALYSIS")
    valid_jobs = [j for j in jobs if j.apply_url] # Basic filter
    
    print(f"[Pipeline] Syncing {len(valid_jobs)} jobs with Database...")
    new_jobs = []
    known_count = 0
    
    for job in valid_jobs:
        if is_job_processed(db_conn, job):
            known_count += 1
        else:
            new_jobs.append(job)
            
    print(f"\n   [+] FEED:      {len(valid_jobs)} jobs found online.")
    print(f"   [-] KNOWN:     {known_count} jobs skipped.")
    print(f"   [!] NEW:       {len(new_jobs)} job(s) queued for analysis.\n")
    
    if not new_jobs:
        print("[Summary] System is up to date.")
        db_conn.close()
        return

    scored = []
    sheet_count = 0
    
    # Reuse strategy data structure for reporting
    strategy_report_data = {
        "advisor_response": {"archetype": "Multi-Strategy Execution"},
        "final_roles": [r for s in strategies for r in s["roles"]], # Aggregate for display
        "exclusions": [e for s in strategies for e in s["exclusions"]]
    }

    print(f"[Pipeline] Processing {len(new_jobs)} new candidates...\n")
    
    for i, job in enumerate(new_jobs):
        sys.stdout.write(f"\r\033[K    Processing {i+1}/{len(new_jobs)}: {job.company[:20]}")
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
        if score >= run_settings.threshold and cv_text_raw:
            sys.stdout.write(f"\n   HIGH MATCH ({score}): {job.title}\n")
            red_team_data = red_team_analysis(
                cv_full_text=cv_text_raw, 
                job=job, 
                mode=run_settings.red_team_mode, 
                local_llm_url=LOCAL_LLM_URL,
                client=client
            )

        scored.append((job, score, reason, red_team_data))
        mark_job_as_processed(db_conn, job, score)

        if sheets_client and score >= run_settings.threshold:
            log_job_to_sheet(sheets_client, run_settings.spreadsheet_id, job, score, reason)
            sheet_count += 1

        # Periodic save
        if (i + 1) % 5 == 0:
            good_matches_temp = [x for x in scored if x[1] >= run_settings.threshold]
            if good_matches_temp:
                export_jobs_html(good_matches_temp, strategy_report_data, report_filename)

    print("\n\n[Pipeline] Scoring complete.")
    db_conn.close()

    good_matches = [x for x in scored if x[1] >= run_settings.threshold]

    if good_matches:
        export_jobs_html(good_matches, strategy_report_data, report_filename)
        send_telegram_message(
            summarize_jobs(good_matches),
            bot_token=env_settings.telegram_bot_token,
            chat_id=env_settings.telegram_chat_id,
        )
        print(f"[Success] Report saved to {report_filename}")
        if sheets_client:
            print(f"[Success] {sheet_count} logged to Sheets.")
    else:
        print("No matches met the threshold.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())