import json
import re
from typing import Any, Dict, List, Tuple

from hob_junter.config.prompts import (
    PROFILE_PROMPT_DEFAULT,
    RED_TEAM_PROMPT,
    SCORE_PROMPT_DEFAULT,
    STRATEGY_PROMPT,
)
from hob_junter.config.settings import LOCAL_LLM_URL, OPENAI_MODEL
from hob_junter.core import llm_engine
from hob_junter.core.scraper import JobRecord
from hob_junter.utils.helpers import safe_json_loads, with_retries


def extract_text_from_cv_pdf_with_gpt(client, pdf_path: str, ocr_prompt: str) -> str:
    if not pdf_path or not pdf_path.strip():
        raise FileNotFoundError("CV PDF path is required")

    upload = with_retries(lambda: llm_engine.upload_file_for_assistants(client, pdf_path))

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": ocr_prompt},
                {"type": "file", "file": {"file_id": upload.id}},
            ],
        }
    ]

    content = with_retries(
        lambda: llm_engine.openai_chat_content(
            client=client,
            messages=messages,
            model=OPENAI_MODEL,
        )
    )
    return content.strip()


def build_cv_profile(client, cv_text: str, profile_prompt: str = PROFILE_PROMPT_DEFAULT) -> str:
    prompt = profile_prompt.replace("{cv_text}", cv_text[:20000])

    content = with_retries(
        lambda: llm_engine.openai_chat_content(
            client=client,
            messages=[
                {"role": "system", "content": "Extract structured JSON candidate profiles."},
                {"role": "user", "content": prompt},
            ],
            model=OPENAI_MODEL,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    )

    parsed = json.loads(content)
    return json.dumps(parsed)


def consult_career_advisor_gpt(client, cv_text: str) -> Dict[str, Any]:
    prompt = STRATEGY_PROMPT.replace("{cv_text}", cv_text[:20000])

    content = with_retries(
        lambda: llm_engine.openai_chat_content(
            client=client,
            messages=[
                {"role": "system", "content": "You are an executive career strategist."},
                {"role": "user", "content": prompt},
            ],
            model=OPENAI_MODEL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )
    )
    try:
        return json.loads(content)
    except Exception as exc:  # noqa: BLE001
        print(f"[Advisor] Error parsing strategy response: {exc}")
        return {}


def score_job_match(
    client,
    cv_profile_json: str,
    job: JobRecord,
    score_prompt: str = SCORE_PROMPT_DEFAULT,
    scoring_mode: str = "local",
    local_llm_url: str = LOCAL_LLM_URL,
) -> Tuple[int, str]:
    clean_desc = re.sub("<[^<]+?>", " ", job.description)

    template_vars = {
        "cv_profile_json": cv_profile_json,
        "job_title": job.title,
        "job_company": job.company,
        "apply_url": job.apply_url,
        "job_raw": json.dumps(job.raw)[:2000],
        "job_description": clean_desc[:15000],
    }

    prompt = score_prompt
    for key, val in template_vars.items():
        prompt = prompt.replace("{" + key + "}", str(val))

    try:
        content = ""
        if scoring_mode == "openai":
            content = llm_engine.openai_chat_content(
                client=client,
                messages=[
                    {"role": "system", "content": "You are a talent intelligence engine. Output STRICT JSON."},
                    {"role": "user", "content": prompt},
                ],
                model=OPENAI_MODEL,
                temperature=0.0,
                max_tokens=512,
            )
        else:
            content = llm_engine.local_chat_content(
                local_llm_url=local_llm_url,
                messages=[
                    {"role": "system", "content": "You are a talent intelligence engine. Output STRICT JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=512,
            )

        content = llm_engine.strip_json_markdown(content)
        result = safe_json_loads(content)
        return int(result.get("score", 0)), str(result.get("reason", "No reason provided"))

    except Exception as exc:  # noqa: BLE001
        return 0, f"Error: {exc}"


def red_team_analysis(
    local_llm_url: str,
    cv_full_text: str,
    job: JobRecord,
    prompt_template: str = RED_TEAM_PROMPT,
) -> Dict[str, Any]:
    clean_desc = re.sub("<[^<]+?>", " ", job.description)

    prompt = prompt_template.replace("{job_title}", job.title)
    prompt = prompt.replace("{job_company}", job.company)
    prompt = prompt.replace("{job_description}", clean_desc[:10000])
    prompt = prompt.replace("{cv_full_text}", cv_full_text[:20000])

    content = llm_engine.local_chat_content(
        local_llm_url=local_llm_url,
        messages=[
            {
                "role": "system",
                "content": "You are a cynical, hostile hiring manager. Output STRICT JSON only. No markdown, no pre-amble.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1024,
        timeout=180,
    )

    content = llm_engine.strip_json_markdown(content)
    return safe_json_loads(content)
