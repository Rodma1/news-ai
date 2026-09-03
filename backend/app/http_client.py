from __future__ import annotations

import json
import subprocess

from .config import settings

CURL_TIMEOUT = 60


def curl_get(url: str, timeout: int = 30, headers: dict | None = None) -> tuple[int, bytes]:
    cmd = ["curl", "-sS", "-L", "--compressed", "--max-time", str(timeout), "-w", "\n%{http_code}", url]
    for key, value in (headers or {}).items():
        cmd += ["-H", f"{key}: {value}"]
    cmd += ["-H", "User-Agent: Mozilla/5.0 (compatible; NewsAI/1.0)"]
    result = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
    out = result.stdout
    idx = out.rfind(b"\n")
    if idx == -1:
        return 0, b""
    try:
        status = int(out[idx + 1:].strip())
    except ValueError:
        status = 0
    return status, out[:idx]


def curl_post_json(url: str, payload: dict, timeout: int = CURL_TIMEOUT, headers: dict | None = None) -> tuple[int, bytes]:
    cmd = [
        "curl", "-sS", "-L", "--compressed", "--max-time", str(timeout),
        "-X", "POST", "-H", "Content-Type: application/json",
        "-w", "\n%{http_code}", url, "--data-binary", "@-",
    ]
    for key, value in (headers or {}).items():
        cmd += ["-H", f"{key}: {value}"]
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    result = subprocess.run(cmd, input=body, capture_output=True, timeout=timeout + 10)
    out = result.stdout
    idx = out.rfind(b"\n")
    if idx == -1:
        return 0, b""
    try:
        status = int(out[idx + 1:].strip())
    except ValueError:
        status = 0
    return status, out[:idx]