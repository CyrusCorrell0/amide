"""Google Gemini's native API over plain HTTP."""

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
)

_STOPS = {
    "STOP": "end",
    "MAX_TOKENS": "length",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
    "MALFORMED_FUNCTION_CALL": "other",
}


class GeminiAdapter(Adapter):
    kind = "gemini"

    def complete(self, request: Request) -> Reply:
        url = self._url(request.model, "generateContent")
        data = http.post_json(url, self._body(request), self._headers())
        parts, finish, usage, model = _candidate(data, request)
        return _reply(parts, finish, usage, model or request.model, data)

    def stream(self, request: Request, on_text: TextSink) -> Reply:
        url = self._url(request.model, "streamGenerateContent?alt=sse")
        parts: list[dict[str, Any]] = []
        finish: str | None = None
        usage = Usage()
        model = request.model
        blocked = ""
        for event in http.post_sse(url, self._body(request), self._headers()):
            if event.get("error"):
                raise ModelError(f"{request.model}: {_error(event)}")
            chunk_parts, chunk_finish, chunk_usage, chunk_model = _candidate(event, request, True)
            blocked = blocked or _block_reason(event)
            for part in chunk_parts:
                if "text" in part and not part.get("thought"):
                    on_text(part["text"])
                    if parts and "text" in parts[-1] and "functionCall" not in parts[-1]:
                        parts[-1]["text"] += part["text"]
                        continue
                parts.append(part)
            finish = chunk_finish or finish
            if chunk_usage.input_tokens or chunk_usage.output_tokens:
                usage = chunk_usage
            model = chunk_model or model
        if blocked and not parts:
            finish = "SAFETY"
        return _reply(parts, finish, usage, model, {"promptFeedback": {"blockReason": blocked}})

    def list_models(self) -> list[str]:
        data = http.get_json(f"{self.base_url}/models?pageSize=1000", self._headers())
        names = []
        for entry in data.get("models") or []:
            methods = entry.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
            names.append(str(entry.get("name", "")).removeprefix("models/"))
        return sorted(n for n in names if n)

    # ---

    def _url(self, model: str, method: str) -> str:
        return f"{self.base_url}/models/{model}:{method}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["x-goog-api-key"] = self.key
        headers.update(self.options.get("headers") or {})
        return headers

    def _body(self, request: Request) -> dict[str, Any]:
        body: dict[str, Any] = {"contents": _contents(request.messages)}
        if request.system:
            body["systemInstruction"] = {"parts": [{"text": request.system}]}
        if request.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": tool["name"],
                            "description": tool.get("description", ""),
                            "parameters": _schema(tool["input_schema"]),
                        }
                        for tool in request.tools
                    ]
                }
            ]
        config: dict[str, Any] = {"maxOutputTokens": request.max_tokens}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        body["generationConfig"] = config
        return body


def _schema(schema: Any) -> Any:
    """Gemini's schema subset: no ``default``, and arrays must say what they hold."""
    if isinstance(schema, dict):
        out = {k: _schema(v) for k, v in schema.items() if k != "default"}
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out
    if isinstance(schema, list):
        return [_schema(item) for item in schema]
    return schema


def _contents(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.role in ("user", "system"):
            out.append({"role": "user", "parts": [{"text": message.content}]})
        elif message.role == "assistant":
            out.append({"role": "model", "parts": _model_parts(message)})
        else:
            part = {
                "functionResponse": {
                    "name": message.name or "",
                    "response": _response_object(message.content, message.is_error),
                }
            }
            if out and out[-1]["role"] == "user" and "functionResponse" in out[-1]["parts"][0]:
                out[-1]["parts"].append(part)
            else:
                out.append({"role": "user", "parts": [part]})
    return out


def _model_parts(message: Message) -> list[dict[str, Any]]:
    if message.raw:
        return list(message.raw)
    parts: list[dict[str, Any]] = []
    if message.content:
        parts.append({"text": message.content})
    for call in message.tool_calls:
        parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
    return parts or [{"text": ""}]


def _response_object(content: str, is_error: bool) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict) and not is_error:
        return value
    return {"error": content} if is_error else {"result": content}


def _candidate(
    data: dict[str, Any], request: Request, streaming: bool = False
) -> tuple[list[dict[str, Any]], str | None, Usage, str]:
    if data.get("error"):
        raise ModelError(f"{request.model}: {_error(data)}")
    candidates = data.get("candidates") or []
    usage = _usage(data.get("usageMetadata"))
    model = str(data.get("modelVersion") or "")
    if not candidates:
        if streaming:
            return [], None, usage, model
        reason = _block_reason(data)
        return [], "SAFETY" if reason else None, usage, model
    candidate = candidates[0]
    parts = list((candidate.get("content") or {}).get("parts") or [])
    return parts, candidate.get("finishReason"), usage, model


def _reply(
    parts: list[dict[str, Any]], finish: str | None, usage: Usage, model: str, data: dict[str, Any]
) -> Reply:
    text = "".join(part.get("text") or "" for part in parts if not part.get("thought"))
    calls = []
    for index, part in enumerate(parts):
        call = part.get("functionCall")
        if call:
            calls.append(
                ToolCall(
                    str(call.get("id") or f"call_{index}"),
                    str(call.get("name") or ""),
                    dict(call.get("args") or {}),
                )
            )
    stop = "tool_calls" if calls else _STOPS.get(finish or "", "other" if finish else "end")
    detail = ""
    if stop == "refusal":
        detail = _block_reason(data) or f"finish reason {finish}"
    return Reply(
        text=text, tool_calls=calls, stop=stop, usage=usage, model=model, detail=detail, raw=parts
    )


def _block_reason(data: dict[str, Any]) -> str:
    feedback = data.get("promptFeedback") or {}
    return str(feedback.get("blockReason") or "")


def _usage(data: dict[str, Any] | None) -> Usage:
    if not data:
        return Usage()
    return Usage(
        int(data.get("promptTokenCount") or 0),
        int(data.get("candidatesTokenCount") or 0) + int(data.get("thoughtsTokenCount") or 0),
    )


def _error(data: dict[str, Any]) -> str:
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or json.dumps(error))
    return str(error)
