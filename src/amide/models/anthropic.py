"""The Anthropic Messages API over plain HTTP.

Thinking is left to the model's default (adaptive on current models; the
parameter is omitted rather than configured). ``fallbacks: "default"`` is on
unless the provider sets ``fallbacks = false``: a request a safety classifier
declines is re-run server-side on a fallback model instead of stopping.
"""

from __future__ import annotations

import json
from typing import Any

from amide.models import http
from amide.models.base import (
    Adapter,
    Message,
    ModelError,
    Reply,
    Request,
    TextSink,
    ToolCall,
    Usage,
    parse_arguments,
)

VERSION = "2023-06-01"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
_STOPS = {
    "end_turn": "end",
    "stop_sequence": "end",
    "pause_turn": "end",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "refusal": "refusal",
}


class AnthropicAdapter(Adapter):
    kind = "anthropic"

    def complete(self, request: Request) -> Reply:
        data = http.post_json(self._url("v1/messages"), self._body(request), self._headers())
        if data.get("type") == "error":
            raise ModelError(f"{request.model}: {_error(data)}")
        content = data.get("content") or []
        return _reply(content, data.get("stop_reason"), data.get("stop_details"), data, request)

    def stream(self, request: Request, on_text: TextSink) -> Reply:
        body = {**self._body(request), "stream": True}
        blocks: dict[int, dict[str, Any]] = {}
        partial_json: dict[int, list[str]] = {}
        stop: str | None = None
        stop_details: Any = None
        usage = Usage()
        model = request.model
        for event in http.post_sse(self._url("v1/messages"), body, self._headers()):
            kind = event.get("type")
            if kind == "error":
                raise ModelError(f"{request.model}: {_error(event)}")
            if kind == "message_start":
                message = event.get("message") or {}
                model = message.get("model") or model
                usage = _usage(message.get("usage"))
            elif kind == "content_block_start":
                index = int(event["index"])
                block = dict(event.get("content_block") or {})
                if block.get("type") == "tool_use":
                    block["input"] = {}
                    partial_json[index] = []
                blocks[index] = block
            elif kind == "content_block_delta":
                index = int(event["index"])
                delta = event.get("delta") or {}
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                if delta.get("type") == "text_delta":
                    piece = delta.get("text") or ""
                    block["text"] = block.get("text", "") + piece
                    if piece:
                        on_text(piece)
                elif delta.get("type") == "input_json_delta":
                    partial_json.setdefault(index, []).append(delta.get("partial_json") or "")
                elif delta.get("type") == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + (delta.get("thinking") or "")
                elif delta.get("type") == "signature_delta":
                    block["signature"] = block.get("signature", "") + (delta.get("signature") or "")
            elif kind == "message_delta":
                delta = event.get("delta") or {}
                stop = delta.get("stop_reason") or stop
                stop_details = delta.get("stop_details") or stop_details
                if event.get("usage"):
                    usage = Usage(
                        int(event["usage"].get("input_tokens") or usage.input_tokens),
                        int(event["usage"].get("output_tokens") or 0),
                    )
        content = []
        for index in sorted(blocks):
            block = blocks[index]
            if block.get("type") == "tool_use":
                arguments, error = parse_arguments("".join(partial_json.get(index, [])))
                block["input"] = arguments
                if error:
                    block["_error"] = error
            content.append(block)
        return _reply(content, stop, stop_details, {"usage": None, "model": model}, request, usage)

    def list_models(self) -> list[str]:
        data = http.get_json(self._url("v1/models?limit=1000"), self._headers())
        return sorted(str(entry["id"]) for entry in data.get("data") or [] if "id" in entry)

    # ---

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": VERSION}
        if self.key:
            headers["x-api-key"] = self.key
        if self._fallbacks():
            headers["anthropic-beta"] = FALLBACK_BETA
        headers.update(self.options.get("headers") or {})
        return headers

    def _fallbacks(self) -> bool:
        return bool(self.options.get("fallbacks", True))

    def _body(self, request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": _messages(request.messages),
        }
        if request.system:
            body["system"] = request.system
        if request.tools:
            body["tools"] = [
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "input_schema": tool["input_schema"],
                }
                for tool in request.tools
            ]
        if request.effort:
            body["output_config"] = {"effort": request.effort}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if self._fallbacks():
            body["fallbacks"] = "default"
        return body


def _messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Assistant turns replay their raw content; consecutive tool results share one user turn."""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            # Anthropic takes the system prompt at the top level; a stray one becomes user text.
            out.append({"role": "user", "content": message.content})
        elif message.role == "user":
            out.append({"role": "user", "content": message.content})
        elif message.role == "assistant":
            out.append({"role": "assistant", "content": _assistant_content(message)})
        else:
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            if message.is_error:
                block["is_error"] = True
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _assistant_content(message: Message) -> Any:
    if message.raw:
        return [{k: v for k, v in block.items() if not k.startswith("_")} for block in message.raw]
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return blocks or [{"type": "text", "text": ""}]


def _reply(
    content: list[dict[str, Any]],
    stop: str | None,
    stop_details: Any,
    data: dict[str, Any],
    request: Request,
    usage: Usage | None = None,
) -> Reply:
    text = "".join(block.get("text") or "" for block in content if block.get("type") == "text")
    calls = [
        ToolCall(
            str(block.get("id") or ""),
            str(block.get("name") or ""),
            dict(block.get("input") or {}),
            error=block.get("_error"),
        )
        for block in content
        if block.get("type") == "tool_use"
    ]
    detail = ""
    if stop == "refusal":
        detail = _refusal_detail(stop_details)
    return Reply(
        text=text,
        tool_calls=calls,
        stop=("tool_calls" if calls else _STOPS.get(stop or "", "other" if stop else "end")),
        usage=usage if usage is not None else _usage(data.get("usage")),
        model=data.get("model") or request.model,
        detail=detail,
        raw=content,
    )


def _refusal_detail(details: Any) -> str:
    if not isinstance(details, dict):
        return "the request was declined"
    parts = [str(details[k]) for k in ("category", "explanation") if details.get(k)]
    return "; ".join(parts) or "the request was declined"


def _usage(data: dict[str, Any] | None) -> Usage:
    if not data:
        return Usage()
    inputs = sum(
        int(data.get(key) or 0)
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return Usage(inputs, int(data.get("output_tokens") or 0))


def _error(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or json.dumps(error))
    return str(error or data)
