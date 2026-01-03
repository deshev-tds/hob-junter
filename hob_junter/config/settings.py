import json
import os
from dataclasses import dataclass
from typing import Optional

from hob_junter.config.prompts import (
    OCR_PROMPT_DEFAULT,
    PROFILE_PROMPT_DEFAULT,
    SCORE_PROMPT_DEFAULT,
)

LOCAL_LLM_URL = "http://127.0.0.1:1234/v1/chat/completions"
HIRING_BASE = "https://hiring.cafe"
JOBS_ENDPOINT = f"{HIRING_BASE}/api/search-jobs"
OPENAI_MODEL = "gpt-4o"
CONFIG_FILE = "inputs.json"
DEFAULT_CV_PROFILE_PATH = "cv_profile.json"
DEFAULT_CV_TEXT_PATH = "cv_full_text.txt"
DEFAULT_DB_PATH = "jobs.db"
DEFAULT_CREDS_PATH = "service_account.json"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes", "on")


@dataclass
class EnvSettings:
    openai_api_key: str
    telegram_bot_token: Optional[str]
    telegram_chat_id: Optional[str]


@dataclass
class RunSettings:
    cv_path: str
    search_url: Optional[str]
    spreadsheet_id: str
    threshold: int
    cv_profile_path: str
    ocr_prompt: str
    profile_prompt: str
    score_prompt: str
    scoring_mode: str
    debug: bool
    db_path: str
    google_creds_path: str


def load_env_settings() -> EnvSettings:
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required")

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    return EnvSettings(
        openai_api_key=openai_api_key,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
    )


def load_run_settings(config_file: str = CONFIG_FILE) -> RunSettings:
    config = {}
    if os.path.exists(config_file):
        try:
            with open(config_file, "r") as f:
                config = json.load(f)
        except Exception as exc:
            print(f"[Config] Failed to read {config_file}: {exc}. Ignoring.")
            config = {}

    cv_path = config.get("cv_path")
    search_url = config.get("search_url")
    spreadsheet_id = config.get("spreadsheet_id")
    threshold = config.get("threshold")
    debug_cfg = config.get("debug", _env_flag("DEBUG"))
    cv_profile_path = config.get("cv_profile_path") or DEFAULT_CV_PROFILE_PATH
    ocr_prompt = config.get("ocr_prompt") or OCR_PROMPT_DEFAULT
    profile_prompt = config.get("profile_prompt") or PROFILE_PROMPT_DEFAULT
    score_prompt = config.get("score_prompt") or SCORE_PROMPT_DEFAULT
    scoring_mode = config.get("scoring_mode") or "local"
    db_path = config.get("db_path") or DEFAULT_DB_PATH
    google_creds_path = config.get("google_creds_path") or DEFAULT_CREDS_PATH

    if not cv_path:
        cv_path = input("Path to CV PDF: ").strip()
    if not spreadsheet_id:
        spreadsheet_id = input("Google Sheet ID: ").strip()
    if threshold is None or threshold == "":
        threshold_raw = input("Minimum score for Telegram (default 65): ").strip()
        threshold = int(threshold_raw or 65)
    if debug_cfg is None:
        debug_raw = input("Enable debug logging? (y/N): ").strip().lower()
        debug_cfg = debug_raw in ("y", "yes", "1", "true", "on")

    new_config = {
        "cv_path": cv_path,
        "search_url": search_url,
        "spreadsheet_id": spreadsheet_id,
        "threshold": threshold,
        "debug": bool(debug_cfg),
        "cv_profile_path": cv_profile_path,
        "ocr_prompt": ocr_prompt,
        "profile_prompt": profile_prompt,
        "score_prompt": score_prompt,
        "scoring_mode": scoring_mode,
        "db_path": db_path,
        "google_creds_path": google_creds_path,
    }

    try:
        with open(config_file, "w") as f:
            json.dump(new_config, f, indent=2)
    except Exception as exc:
        print(f"[Config] Warning: failed to write {config_file}: {exc}")

    return RunSettings(
        cv_path=cv_path,
        search_url=search_url,
        spreadsheet_id=spreadsheet_id,
        threshold=threshold,
        cv_profile_path=cv_profile_path,
        ocr_prompt=ocr_prompt,
        profile_prompt=profile_prompt,
        score_prompt=score_prompt,
        scoring_mode=scoring_mode,
        debug=bool(debug_cfg),
        db_path=db_path,
        google_creds_path=google_creds_path,
    )