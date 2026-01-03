"""
Prompt templates used throughout the pipeline.
Separated to keep the main orchestration lean.
"""

OCR_PROMPT_DEFAULT = (
    "Extract all human-readable text from this PDF. No formatting, no comments, "
    "no summary. Return ONLY the plain text content of the CV."
)

STRATEGY_PROMPT = """You are an elite Executive Headhunter and Career Strategist.
Analyze the candidate's CV text below. 

1. Classify the candidate's PRIMARY TARGET INDUSTRY (e.g. "Technology", "Finance", "Healthcare", "Manufacturing").
2. Determine if the target roles fall under "Information Technology", "Software Engineering", "Data", or "Product" (Tech). Set 'is_tech_industry' to true if yes.
3. Identify the 3-5 Highest Probability Job Titles this candidate should target.
4. Draft a "Candidate Archetype" summary (2 sentences) explaining their unique value proposition and professional identity.

OUTPUT REQUIREMENTS:
- Return STRICT JSON format.
- Structure: 
{
  "industry": "Industry Name",
  "is_tech_industry": true, 
  "archetype": "Brief executive summary of the candidate's persona (e.g. 'Battle-hardened DevOps Director specializing in high-frequency trading platforms...')",
  "suggestions": [ 
    { "role": "Exact Job Title", "reason": "Why this fits" } 
  ]
}

CV TEXT:
\"\"\"{cv_text}\"\"\"
"""

PROFILE_PROMPT_DEFAULT = """Extract a structured, ATS-oriented candidate profile from the CV text.

Return STRICT JSON with the following keys ONLY:
- summary
- skills[]
- experience[]
- preferred_roles[]
- locations[]
- seniority

HARD RULES:
- Do NOT embellish language.
- Do NOT infer skills, seniority, scope, or intent.
- Do NOT normalize leadership into IC roles.
- If information is unclear or not explicitly stated, leave the field empty.
- Prefer factual signals over descriptive language.

OUTPUT RULES:
- STRICT JSON ONLY.

CV TEXT:
\"\"\"{cv_text}\"\"\"
"""

SCORE_PROMPT_DEFAULT = """You are an enterprise-grade Talent Intelligence Engine designed for objective candidate assessment.
The job of the candidate is to prove to you that they are a match with the job description provided. Yours is to evaluate that match fairly and strictly based on EVIDENTIARY SUPPORT from the candidate profile.

Your task is to assess whether this candidate would realistically PASS or FAIL the screening stage for THIS specific job.

EVALUATION PROTOCOL:
- Compare PRIMARY FUNCTION of Job vs. Candidate.
- Check for DIRECT EVIDENCE of Mandatory Hard Skills.
- Check for SENIORITY & ARCHETYPE ALIGNMENT.

OUTPUT INSTRUCTIONS:
- Return STRICT JSON ONLY.
- The "reason" must be professional, evidence-based, and concise.

JSON FORMAT:
{
  "score": <number>,
  "reason": "<concise explanation focused on functional mismatches or strong transferable signals>"
}

Candidate Profile:
{cv_profile_json}

Job:
{job_title} @ {job_company}
Apply Link: {apply_url}

JOB DESCRIPTION:
{job_description}
"""

RED_TEAM_PROMPT = """ROLE: You are a skeptical, cynical Hiring Manager for a high-stakes role.
TASK: Review this Full CV against the Job Description. You are looking for reasons to REJECT.
Do NOT be polite. Find the weak spots.

JOB: {job_title} @ {job_company}

JOB DESCRIPTION:
{job_description}

FULL CANDIDATE CV:
{cv_full_text}

OUTPUT:
Return a STRICT JSON with:
1. "interview_questions": 3 "Kill Questions" specifically designed to expose weaknesses or verify vague claims.
2. "outreach_hook": A 1-sentence "Sniper" cold message to the hiring manager. It must identify their biggest likely pain point (from JD) and offer a specific solution/experience (from CV). No "I hope you are well". Straight to value.

Format:
{
  "interview_questions": ["Question 1", "Question 2", "Question 3"],
  "outreach_hook": "Your concise sniper message here."
}
"""
