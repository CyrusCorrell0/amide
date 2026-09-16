"""HTTP for the model adapters. Tests monkeypatch ``post_json``, ``post_sse``, ``get_json``.

Retries once on 429 and 5xx, honouring ``Retry-After`` up to a ceiling.
Streaming responses are Server-Sent Events; ``post_sse`` yields each event's
JSON ``data`` and stops at ``[DONE]``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from amide.models.base import ModelError

TIMEOUT = 600.0
RETRIES = 2
MAX_WAIT = 30.0
_RETRY_STATUSES = {408, 409, 429, 500, 502, 503, 504}
_sleep = time.sleep  # patched in tests


def post_json(
    url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = TIMEOUT
) -> dict[str, Any]:
    with _open(url, body, headers, timeout) as response:
        return _decode(url, response.read())


def get_json(url: str, headers: dict[str, str], timeout: float = TIMEOUT) -> dict[str, Any]:
    with _open(url, None, headers, timeout) as response:
        return _decode(url, response.read())


def post_sse(
    url: str, body: dict[str, Any], headers: dict[str, str], timeout: float = TIMEOUT
) -> Iterator[dict[str, Any]]:
    with _open(url, body, {**headers, "Accept": "text/event-stream"}, timeout) as response:
        yield from parse_sse(line.decode("utf-8", "replace") for line in response)


def parse_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Turn SSE text into the JSON of each event's ``data``."""
    data: list[str] = []
    for line in lines:
        line = line.rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].strip())
            continue
        if line == "" and data:
            payload = "\n".join(data)
            data = []
            if payload == "[DONE]":
                return
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                raise ModelError(
                    f"stream sent something that is not JSON: {payload[:200]}"
                ) from None
    if data:
        payload = "\n".join(data)
        if payload != "[DONE]":
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                raise ModelError(
                    f"stream sent something that is not JSON: {payload[:200]}"
                ) from None


def _open(url: str, body: dict[str, Any] | None, headers: dict[str, str], timeout: float):
    payload = None if body is None else json.dumps(body).encode()
    sent = {"User-Agent": "amide", **headers}
    if payload is not None:
        sent.setdefault("Content-Type", "application/json")
    attempt = 0
    while True:
        request = urllib.request.Request(url, data=payload, headers=sent)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            detail = _error_text(error)
            if error.code in _RETRY_STATUSES and attempt < RETRIES:
                attempt += 1
                _sleep(_wait(error, attempt))
                continue
            raise ModelError(f"{url} returned {error.code}: {detail}", error.code) from None
        except OSError as error:
            if attempt < RETRIES:
                attempt += 1
                _sleep(float(attempt))
                continue
            reason = getattr(error, "reason", None) or error
            raise ModelError(f"could not reach {url}: {reason}") from None


def _wait(error: urllib.error.HTTPError, attempt: int) -> float:
    after = error.headers.get("Retry-After") if error.headers else None
    if after:
        try:
            return min(float(after), MAX_WAIT)
        except ValueError:
            pass
    return float(2**attempt)


def _error_text(error: urllib.error.HTTPError) -> str:
    try:
        body = error.read().decode("utf-8", "replace")
    except OSError:
        return error.reason or ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.strip()[:500] or (error.reason or "")
    # OpenAI, Anthropic, and Gemini all put it under error.message.
    inner = data.get("error") if isinstance(data, dict) else None
    if isinstance(inner, dict) and inner.get("message"):
        return str(inner["message"])
    if isinstance(inner, str):
        return inner
    return body.strip()[:500]


def _decode(url: str, body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise ModelError(f"{url} did not return JSON: {body[:200]!r}") from None
    if not isinstance(data, dict):
        raise ModelError(f"{url} returned {type(data).__name__}, expected an object")
    return data
