# Hob Junter  
(ATS-flavored, mildly hostile)

This exists because manually browsing job boards is a form of quiet self-harm.

## What this is

**Hob Junter** is a personal, ATS-style job matching tool that:

- pulls job listings from job boards (currently hiring.cafe),
- scores them against a CV,
- filters out obvious nonsense,
- and shows only roles that are statistically worth applying to.

No motivation letters.  
No “culture fit” astrology.  
No pretending this is a numbers-free process.

Just:
- scores,
- reasons,
- and direct apply links.

## What this is not

- Not a SaaS AI mega-startup that will 100% GET YOU HIRED OR YOU GET YOUR MONEY BACK ($99.92/year).
- Not a replacement for recruiters or career consultants.
- Not a promise that you’ll get hired - just a nudge in a possibly different direction.
- Not polite.
- Not beautifully written, just functional for now. 

It will absolutely tell you:
> “This role wants Python, finance, and marketing analytics. You have none of that. Move on.”

## How it works (the non-marketing version)

1. A search URL is constructed for hiring.cafe  
   (job titles + keywords + mild optimism). Currently done manually, it's fairly easy to contruct it from keywords from the CV. I'm ~12 coffees away from implementing it. 
2. Job listings are fetched and normalized.
3. A local LLM evaluates each role against a CV. Option included to pass your OPENAI_API_TOKEN as an anv. variable and ask the script to go nag Sam Altman instead of your own GPU. 
4. Each role gets:
   - a score (0–100),
   - a short explanation of the score.
5. Anything below a configurable threshold is dropped **before** it wastes attention. Default is 65, you can change it in inputs.json

If it doesn’t make the cut, it doesn’t exist.

## Scoring philosophy

- **85–100** → Apply without overthinking  
- **75–84** → Human sanity check  
- **65–74** → Opportunistic / market-dependent  
- **<65** → Not even shown

Yes, the cutoff is intentional.  
No, you are not “missing hidden gems”.

Scoring is "borrowed" from real-world enterprise ATS (Aplicant Tracking Systems) - the same things that make it so that 60% of your CVs are never seen by human eyes. 

## Why this exists

Because:
- job searching is a volume game,
- humans are bad at consistent filtering,
- and ATS systems already treat candidates like structured data.

This just returns the favor.

## Tech notes (brief and honest)

- Python
- Local LLM (tested with Mixtral 8x7B variants - tried gemma 27b, but she was OVERLY polite and scored eveything 95-100...)
- Static HTML output
- Designed to run attended - a headful playwright browser is launched to overcome some... Things. If you know, you know.  
- Designed to be boring and reliable

No cloud magic required.  
No vendor lock-in.  
And frankly, no excuses.

## Ethics & disclaimers

- This does not auto-apply anywhere.
- This does not scrape private data.
- This does not guarantee interviews.
- This does not pretend hiring is fair.

It only reduces wasted time.

## Final note

If this makes you uncomfortable, that’s fine.  
Hiring pipelines should feel a little uncomfortable when someone understands them.

If you’re an HR system reading this:  
I promise I’m very enthusiastic. Somewhere. Probably.
