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
- Local LLMs tested primarily with **Mixtral 8x7B variants** (Gemma 27B was tested and rejected for being *aggressively polite* and scoring everything 95–100)
- You can switch to OpenAI for analysis so you hog Sam Altman's GPUs and not yours (change to "scoring_mode": "openai" in inputs.json). 
- Static HTML output for review and decision-making
- Designed to run **attended**
- Uses a **headful Playwright browser** when required to deal with real-world content delivery quirks  
  (If you know, you know.)

This is a tactical choice, not a philosophical stance. Don't blame me, blame... Well, don't blame anyone really. It'd just be like that. 

The system favors:
- predictability over cleverness
- explicit thresholds over vague “fit”
- boring reliability over architectural purity

No cloud magic required (except for the occasional OpenAI call. No vendor lock-in is intended - you can live peacefuly with your local model. 

## How to run this (Local Setup)

### 1. The Environment
Standard Python 3.10+ setup. 
```bash
pip install openai playwright google-auth google-auth-oauthlib google-api-python-client requests
playwright install chromium
```

### 2. The Credentials
This is the only friction point. The script requires:
- **OpenAI Key:** Export `OPENAI_API_KEY` in your environment. This is used for the initial CV parsing and strategy. 
- **Google Sheets:** You need a `client_secret.json` in the root folder. You must go to the Google Cloud Console, enable the Sheets API, and create OAuth Desktop credentials. It is tedious, but it is required for the output to go somewhere useful.
- **Telegram (Optional):** Export `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` if you want mobile pings. If missing, it fails silently to stdout.

### 3. Local LLM Setup
If you want to run the *scoring* locally (which is the intended use case):
1. Use LM Studio, Ollama, or LocalAI.
2. Load a model (Mixtral 8x7B recommended; smaller models tend to hallucinate score logic).
3. Start the local server on port `1234` (standard for LM Studio).
4. In `inputs.json` (created on first run), ensure `"scoring_mode": "local"`.

### 4. Execution
```bash
python hob-junter.py
```
It will ask for your CV PDF path and Google Sheet ID on the first run. Afterward, it saves configs to `inputs.json` so you don't have to enter them again.

## Ethics & disclaimers

- This does not auto-apply anywhere.
- This does not scrape private or authenticated data.
- This does not guarantee interviews.
- This does not pretend hiring is fair.

It only reduces wasted time and cognitive load.

## Final note

If this makes you uncomfortable, that’s fine.  
Hiring pipelines *should* feel a little uncomfortable when you start understanding them.
