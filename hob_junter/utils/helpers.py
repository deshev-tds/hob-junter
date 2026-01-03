import json
import sys
import time
from typing import Any


def print_phase_header(phase_num: int, title: str):
    """Pretty console header for pipeline phases."""
    print(f"\n\033[34m{'=' * 65}\033[0m")
    print(f"\033[1;34m 🚀 PHASE {phase_num}/4: {title}\033[0m")
    print(f"\033[34m{'=' * 65}\033[0m")


def with_retries(fn, attempts: int = 3, base_delay: float = 1.0):
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if i == attempts - 1:
                raise
            delay = base_delay * (2**i)
            print(f"[Retry] {i + 1}/{attempts} failed: {exc}. {delay:.1f}s...")
            time.sleep(delay)


def debug_print(msg: str, enabled: bool = False):
    if enabled:
        print(f"[DEBUG] {msg}")
        sys.stdout.flush()


def safe_json_loads(raw: str) -> Any:
    import json

    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}


def load_cv_profile_from_json(path: str) -> str:
    with open(path, "r") as f:
        data = f.read()
    try:
        parsed = json.loads(data)
        return json.dumps(parsed)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("CV JSON file is invalid JSON") from exc


def save_cv_profile_to_file(profile_json: str, path: str):
    try:
        with open(path, "w") as f:
            f.write(profile_json)
        print(f"[CV] Cached profile to {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[CV] Warning: failed to cache profile to {path}: {exc}")
