# Hob Junter  
(ATS-flavored, mildly hostile)

This exists because manually browsing job boards is a form of quiet, socially accepted self-harm.

## What this is

**Hob Junter** is a personal, ATS-style job matching system that:

- pulls job listings from job boards (currently hiring.cafe),
- normalizes and deduplicates them,
- scores them against a CV using a local LLM,
- aggressively filters out low-signal roles,
- and exposes only jobs that are statistically worth attention.

Finding a new job is not a vibes and giigle process, it's a numbers (and data, and some intuition...) process. This tools does only: 

- scores,
- reasons,
- and produces direct apply links.

## What this is not

- Not a SaaS AI mega-startup that will 100% GET YOU HIRED OR YOUR MONEY BACK ($99.99/year).
- Not a replacement for recruiters, HR, or career consultants.
- Not a promise of interviews.
- Not polite.
- Not optimized for feelings (I honestly tried - didn't work).

It will absolutely tell you:
> “This role wants Python, finance, and marketing analytics. You have none of that. Move on.”

And it will do so calmly and consistently.

## How it works (non-marketing version)

1. A search URL is constructed for hiring.cafe (they are the LAST cool place for folks looking to change careers. Please love them and use this type of tools sparingly <3 )
   (job titles + keywords + mild optimism).  
   Currently this can be provided manually. Also, extracting keywords directly from the CV is tested and confirmed as working - if you don't provide a url in the json file, you will be asked questions interactively so it is constructed live for you.
   You also get a "bonus" review of your CV and GPT pings you back in the terminal, suggesting what roles to apply to have better chances. You can either listen to it, or list your own in the interactive prompt. Those will be used to construct the job aggregator URL correctly. 

3. Job listings are fetched, normalized, and deduplicated.

4. Each role is evaluated against a CV using a **local LLM**.  
   Optionally, you can pass an `OPENAI_API_KEY` and ask the script to bother Sam Altman instead of your own GPU.

5. Each job receives:
   - a numeric score (0–100),
   - a short, explicit justification explaining the score.

6. Anything below a hard threshold is discarded **before** it reaches the UI.  
   The default cutoff is **65**, configurable via `inputs.json`.

If it doesn’t make the cut, it does not exist.

## Scoring philosophy

The system uses explicit score bands:

- **85–100** -> Apply without overthinking  
- **75–84** -> Human sanity check required  
- **65–74** -> Opportunistic / market-dependent  
- **<65** -> Discarded before presentation

Yes, the cutoff is intentional, and no, you are not “missing hidden gems”.

This scoring model is inspired by real-world enterprise ATS behavior -
the same systems responsible for 50–60% of CVs never reaching human eyes.

The difference is that here, the rules are visible.

## Why this exists

Because:
- job searching is a numbers game,
- humans are terrible at consistent filtering,
- and ATS systems already treat candidates as structured data.

This just returns the favor.

## Architecture & execution notes

- Python-based pipeline
- Local LLMs tested primarily with **Mixtral 8x7B and qwen3 VI 30B variants** (Gemma 27B was tested and rejected for being *aggressively polite* and scoring everything 95–100)
- You can switch to OpenAI for analysis so you hog Sam Altman's GPUs and not yours (change to "scoring_mode": "openai" in inputs.json). 
- Static HTML output for review and decision-making
- As of Jan 4, 2026: **Server Ready -** Designed to run **unattended** on a VPS (e.g., Hetzner, DigitalOcean, etc.).
- **Headless-Headful Browsing:** Uses a headful Playwright browser inside a virtual framebuffer (`xvfb`) to bypass... Engineering reality (If you know, you know.) while running on a server without a monitor.  This one is a tactical choice, not a philosophical stance. Don't blame me, blame... Well, don't blame anyone really. It'd just be like that. 

The system favors:
- predictability over cleverness
- explicit thresholds over vague “fit”
- boring reliability over architectural purity

No cloud magic required (except for the occasional OpenAI call. No vendor lock-in is intended - you can live peacefuly with your local model(s). 


## Modular architecture (current state)

The former 1,200-line `hob-junter.py` is now split into a package for sanity and testing:

- `main.py` – Orchestration entrypoint. Run this.
- `catch_up_db.py` – Utility to fast-scrape jobs and mark them as "seen" in the DB without spending tokens on scoring (useful for initialization).
- `restore_db_final.py` – Emergency utility to mine `llm_traffic.log` and resurrect jobs if the DB explodes.
- `migrate_db.py` – Schema migration tool if you update the code and the DB breaks.
- `hob_junter/config/settings.py` – Env + run config loader.
- `hob_junter/config/prompts.py` – All prompt templates (OCR, profile, scoring, red-team).
- `hob_junter/core/llm_engine.py` – LLM wrappers, traffic logging.
- `hob_junter/core/analyzer.py` – CV OCR, strategy advisor, scoring logic.
- `hob_junter/core/scraper.py` – Playwright job harvesting.
- `hob_junter/core/database.py` – SQLite logic and deduplication checks.
- `hob_junter/core/sheets.py` – Real-time logging to Google Sheets.
- `hob_junter/core/reporter.py` – Telegram push + HTML report generation.
- `inputs.json` – User/runtime config.

Legacy monoliths (`hob-junter.py`/`hob-junter3.4.py`) remains for reference; new development should go through `main.py` and the package modules above.

How to use now:
1) Export `OPENAI_API_KEY` (plus `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` if you want alerts).  
2) Update `inputs.json` (CV path, search URL, Sheet ID, thresholds) or answer the prompts on first run.  
3) `python main.py` (uses Playwright to scrape hiring.cafe, then scores/report).  


*Create a small SQLIte DB to keep your applications state**

1.  **DB Initialization:**

    ```bash
    python -c "import sqlite3, os; [os.remove('jobs.db') if os.path.exists('jobs.db') else None]; conn = sqlite3.connect('jobs.db'); conn.execute('CREATE TABLE jobs (job_id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, score INTEGER, status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, notes TEXT)'); conn.commit(); conn.close(); print('jobs.db purged and re-initialized with latest schema (v2).')"
    ```
2. **Install Dependencies:**

   ```bash
   pip install -r requirements.txt
   playwright install chromium
   playwright install-deps  # Critical on Linux to install system libraries (libgbm, etc.)
    ```

3. **Install xvfb**

   ```bash
   sudo apt-get install xvfb
    ```
... or address this with your package manager, depending on your distro. 

4.  **Next steps:**

I strongly suggest that you start with wizard.py. It's a wizard, Harry, etc. and will help you better understand the core prerequisites. It will also very kindly generate the correct inputs.json for your setup. 

5. **Go hunting:**

   ```bash
   xvfb-run --server-args="-screen 0 1920x1080x24" python3 main.py
    ```

## Ethics & disclaimers

- This does not auto-apply anywhere.
- This does not scrape private or authenticated data.
- This does not guarantee interviews.
- This does not pretend hiring is fair.

It only reduces wasted time and cognitive load.

## Final note

If this makes you uncomfortable, that’s fine.  
Hiring pipelines *should* feel a little uncomfortable when you start understanding them.
