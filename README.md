# Hob Junter 3.3
(ATS-flavored, mildly hostile)

This exists because manually browsing job boards is a form of quiet, socially accepted self-harm.

## Modular architecture (current state)

The former 1,200-line `hob-junter.py` is now split into a package for sanity and testing:

- `main.py` – orchestration entrypoint; run with `python main.py`.
- `hob_junter/config/settings.py` – env + run config loader (`inputs.json`, defaults, thresholds, API keys).
- `hob_junter/config/prompts.py` – all prompt templates (OCR, profile, scoring, red-team).
- `hob_junter/core/llm_engine.py` – OpenAI/local LLM wrappers, JSON cleaning, file upload.
- `hob_junter/core/analyzer.py` – CV OCR/profile build, strategy advisor, scoring, red-team.
- `hob_junter/core/scraper.py` – Playwright job harvesting, search URL builder, interactive role/exclusion prompts.
- `hob_junter/core/reporter.py` – Telegram push + HTML report generation/summarization.
- `hob_junter/utils/helpers.py` – retries, debug printing, safe JSON, CV profile cache helpers.
- `inputs.json` – user/runtime config.
- `requirements.txt` – minimal dependencies.

How to use now:
1) Export `OPENAI_API_KEY` (plus `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` if you want alerts).  
2) Update `inputs.json` (CV path, search URL, Sheet ID, thresholds) or answer the prompts on first run.  
3) `python main.py` (uses Playwright to scrape hiring.cafe, then scores/report).  


**Create a small SQLIte DB to keep your applications state**

1.  **Install:**
    ```bash
    python -c "import sqlite3; conn = sqlite3.connect('jobs.db'); conn.execute('CREATE TABLE jobs (job_id TEXT PRIMARY KEY, title TEXT, company TEXT, url TEXT, score INTEGER, status TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)'); conn.commit(); conn.close(); print('Database initialized.')"
    ```


    ```bash
   
    ```

Legacy monolith (`hob-junter.py`/`hob-junter3.4.py`) remains for reference; new development should go through `main.py` and the package modules above.

## What this is

**Hob Junter** is a personal, ATS-style job matching system that:

- pulls job listings from job boards (currently hiring.cafe),
- normalizes and deduplicates them,
- scores them against a CV using a local LLM,
- aggressively filters out low-signal roles,
- and exposes only jobs that are statistically worth attention.

## Why it exists? 

Because:

- job searching is a numbers game,
- humans are terrible at consistent filtering,
- and ATS systems already treat candidates as structured data.

This just returns the favor.

**Hob Junter v3** is the "adult" version of the previous chaos. It is now a tiny personal job reconnaissance tool that:

- **Bypasses aggregators** entirely (RIP Hiring.cafe, we loved you, but we moved on).
- **Targets the source:** Queries the Google Search API directly for `greenhouse.io`, `lever.co`, `ashbyhq.com`, and friends.
- **Scores ruthlessly:** Uses an LLM (GPT-4o, qwen3 VI 30B, etc.)  to judge roles against your CV with the cynicism of a tired recruiter.
- **Protects your dignity:** Implements fuzzy deduplication so you don't apply to the same role twice like a desperate amateur.

Finding a new job is not a vibes and giggles process. It's a numbers, data, and API quota process. This tool:
- Scores.
- Reasons.
- Produces direct links.
- **Doesn't crash** because we finally killed the headless browser.

## The pivot (Why v3?)

The previous version relied on `Playwright` and hope. It was heavy, slow, and got blocked by Cloudflare if you looked at it wrong. Also, I sneezed near it once and got an OOM. I kid you not. 

**v3 is a Sniper:**
1.  **No browser:** I now use `requests` + `trafilatura`. It runs on a potato.
2.  **Circuit breakers:** If a domain (e.g., Greenhouse) returns a 429 (Too Many Requests), I stop hitting it instantly across all threads. I don't spam. 
3.  **Cleanliness:** I strip marketing tracking params (`utm_source`, `gclid`) but keep functional routing parameters. The URLs are clean.
4.  **Thread-Safe Concurrency:** Yes, I use locking. Yes, it's over-engineered. No, I don't care, because this is ther right thing to do! 

## What this is not

- **Polite.**
- **Optimized for feelings.** (I tried; didn't work).
- **A guarantee.**
- **Free.** You need API keys now for the upsrteam providers. Freedom costs tokens.

It will absolutely tell you:
> “Score: 15. Reason: You are a Director. This is an unpaid internship. Have some self-respect.”

## How it works

1.  **The profile Llock:** On first run, it reads your CV and generates a `cv_profile_master.json`. **You must review this.** It is the single point of truth. If this is wrong, every score will be wrong.
2.  **The hunt:** It uses Google dorking (`site:boards.greenhouse.io "Director" "Remote"`) to find jobs indexed in the last 7 days.
3.  **The filter:**
    * **Garbage detector:** Drops pages that scream "Enable JavaScript" or "Cloudflare Access Denied".
    * **Freshness gate:** Checks JSON-LD Schema to ensure the job wasn't actually posted 3 months ago and just bumped.
4.  **The score:** GPT-4o or whatever model you have chosen for this reads the *actual* text (not just the dork snippet) and outputs a JSON verdict.

## Setup

You need to be a verified human with a wallet.

1.  **Install:**
    ```bash
    pip install openai google-api-python-client google-auth-oauthlib requests trafilatura pypdf beautifulsoup4
    ```

2.  **Keys:**
    * **OpenAI API Key:** For the cloud brain, if you need one.
    * **Google Custom Search API Key:** For the eyes.
    * **Google CSE ID (cx):** Create a search engine that searches "The entire web".

3.  **Run:**
    ```bash
    python hob-junter3.4.py
    ```

## Ethics & disclaimers

- **I try not to be loud:** I hit ATS endpoints directly. The script attempts to be polite (rate limits, backooff logic, circuit breakers), but you are responsible for your IP reputation.
- **I am biased:** The scoring logic favors the user's success over the company's requirements.
- **I am cold:** If the text extraction fails, I cap the score at 65. I don't guess.

## Final note

If the brutality of the feedback loop makes you uncomfortable, that’s fine. The job market doesn't care - and it's honestly quite uncomfortable once you start understading it.

*Happy hunting.*
