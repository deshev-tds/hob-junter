import json
from typing import Any, Dict, List, Optional

import requests
from openai import OpenAI


def create_openai_client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key)


def openai_chat_content(
    client: OpenAI,
    messages: List[Dict[str, Any]],
    model: str,
    temperature: float = 0.0,
    response_format: Optional[Dict[str, str]] = None,
    max_tokens: Optional[int] = None,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        response_format=response_format,
        max_completion_tokens=max_tokens,
    )
    if not resp or not resp.choices:
        return ""
    return resp.choices[0].message.content or ""


def local_chat_content(
    local_llm_url: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.0,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
) -> str:
    resp = requests.post(
        local_llm_url,
        json={
            "model": "local-model",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=timeout,
    )
    try:
        api_response = resp.json()
        return api_response["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return resp.text


def strip_json_markdown(raw: str) -> str:
    if "```" not in raw:
        return raw
    import re

    match = re.search(r"```(?:json)?(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return raw


def upload_file_for_assistants(client: OpenAI, path: str):
    return client.files.create(file=open(path, "rb"), purpose="assistants")
