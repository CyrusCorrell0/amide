"""HTTP for the fetch tools. Tests monkeypatch ``fetch``."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from amide.harness.errors import ToolError

TIMEOUT = 60
_CHUNK = 1 << 16


def fetch(url: str, timeout: float = TIMEOUT) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "amide"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise ToolError(f"{url} returned {error.code}") from None
    except OSError as error:
        raise ToolError(f"could not reach {url}: {error}") from None


def fetch_json(url: str, timeout: float = TIMEOUT) -> Any:
    body = fetch(url, timeout)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        raise ToolError(f"{url} did not return JSON") from None


def download(url: str, dest: Path, timeout: float = TIMEOUT) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fetch(url, timeout))
    return dest
