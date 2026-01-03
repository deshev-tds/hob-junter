import json
import os
import time
from datetime import datetime
import requests
from openai import OpenAI

def _log_traffic(source, messages, response_content):
    """
    Writes input/output to a local log file for debugging.
    This is the Black Box recorder.
    """
    try:
        log_entry = (
            f"\n{'='*30} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{source}] {'='*30}\n"
            f"--- PROMPT / MESSAGES ---\n"
            f"{json.dumps(messages, ensure_ascii=False, indent=2)}\n\n"
            f"--- RAW RESPONSE ---\n"
            f"{response_content}\n"
            f"{'='*80}\n"
        )
        with open("llm_traffic.log", "a", encoding="utf-8") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"[Log Error] Failed to write to llm_traffic.log: {e}")


def create_openai_client(api_key: str):
    return OpenAI(api_key=api_key)


def openai_chat_content(
    client,
    messages,
    model="gpt-4o",
    temperature=0.0,
    max_tokens=None,
    response_format=None,
):
    """
    Wrapper for OpenAI chat completion with AUTO-LOGGING.
    """
    params = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        params["max_tokens"] = max_tokens
    if response_format:
        params["response_format"] = response_format

    try:
        response = client.chat.completions.create(**params)
        content = response.choices[0].message.content
        
        # LOG IT!
        _log_traffic("OPENAI", messages, content)
        
        return content

    except Exception as e:
        _log_traffic("OPENAI_ERROR", messages, str(e))
        raise e


def local_chat_content(
    local_llm_url,
    messages,
    temperature=0.7,
    max_tokens=1024,
    timeout=120,
):
    """
    Wrapper for Local LLM (LM Studio / Ollama) via requests with AUTO-LOGGING.
    """
    payload = {
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False
    }
    
    try:
        resp = requests.post(
            local_llm_url, 
            json=payload, 
            headers={"Content-Type": "application/json"},
            timeout=timeout
        )
        resp.raise_for_status()
        data = resp.json()
        
        # Handle different local server response formats (Ollama vs LM Studio)
        if "choices" in data:
            content = data["choices"][0]["message"]["content"]
        elif "message" in data:
            content = data["message"]["content"]
        else:
            content = str(data)

        # LOG IT!
        _log_traffic("LOCAL_LLM", messages, content)
        
        return content

    except Exception as e:
        _log_traffic("LOCAL_ERROR", messages, str(e))
        return f'{{"error": "{str(e)}", "score": 0, "reason": "Local LLM connection failed"}}'


def upload_file_for_assistants(client, file_path):
    """
    Uploads a file to OpenAI for RAG/Assistants usage.
    """
    with open(file_path, "rb") as f:
        return client.files.create(file=f, purpose="assistants")


def strip_json_markdown(text):
    """
    Removes ```json ... ``` wrappers commonly returned by LLMs.
    """
    text = text.strip()
    if text.startswith("```"):
        # Find the first newline
        first_newline = text.find("\n")
        if first_newline != -1:
            # Check if the last line is ```
            if text.endswith("```"):
                return text[first_newline+1:-3].strip()
            return text[first_newline+1:].strip()
    return text