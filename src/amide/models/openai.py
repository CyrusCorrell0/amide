"""OpenAI-compatible chat completions: OpenAI, DeepSeek, Mistral, Groq, xAI,
Together, OpenRouter, Ollama, vLLM, and anything else with the same endpoint."""

from __future__ import annotations

from typing import Any

from amide.models import http
from amide.models.base import (
    Adapter,
    ModelError,
    Reply,
    Request,
    TextSink,
    ToolCall,
    Usage,
    parse_arguments,
)

_STOPS = {
    "stop": "end",
    "tool_calls": "tool_calls",
    "length": "length",
    "content_filter": "refusal",
}


class OpenAIAdapter(Adapter):
    kind = "openai"

    def complete(self, request: Request) -> Reply:
        data = http.post_json(self._url("chat/completions"), self._body(request), self._headers())
        choices = data.get("choices") or []
        if not choices:
            raise ModelError(f"{request.model} returned no choices: {_brief(data)}")
        choice = choices[0]
        message = choice.get("message") or {}
        calls = [_call(entry) for entry in message.get("tool_calls") or []]
        return Reply(
            text=message.get("content") or "",
            tool_calls=calls,
            stop=_stop(choice.get("finish_reason"), calls),
            usage=_usage(data.get("usage")),
            model=data.get("model") or request.model,
            detail=_refusal(message),
            raw=None,
        )

    def stream(self, request: Request, on_text: TextSink) -> Reply:
        body = {**self._body(request), "stream": True, "stream_options": {"include_usage": True}}
        text: list[str] = []
        partial: dict[int, dict[str, Any]] = {}
        finish: str | None = None
        usage = Usage()
        model = request.model
        refusal = ""
        for event in http.post_sse(self._url("chat/completions"), body, self._headers()):
            if "error" in event and not event.get("choices"):
                raise ModelError(f"{request.model}: {_error(event['error'])}")
            model = event.get("model") or model
            if event.get("usage"):
                usage = _usage(event["usage"])
            for choice in event.get("choices") or []:
                delta = choice.get("delta") or {}
                piece = delta.get("content")
                if piece:
                    text.append(piece)
                    on_text(piece)
                if delta.get("refusal"):
                    refusal += delta["refusal"]
                for entry in delta.get("tool_calls") or []:
                    slot = partial.setdefault(
                        entry.get("index", len(partial)), {"id": "", "name": "", "arguments": ""}
                    )
                    slot["id"] = entry.get("id") or slot["id"]
                    function = entry.get("function") or {}
                    slot["name"] = function.get("name") or slot["name"]
                    slot["arguments"] += function.get("arguments") or ""
                finish = choice.get("finish_reason") or finish
        calls = []
        for index in sorted(partial):
            slot = partial[index]
            arguments, error = parse_arguments(slot["arguments"])
            calls.append(
                ToolCall(slot["id"] or f"call_{index}", slot["name"], arguments, error=error)
            )
        return Reply(
            text="".join(text),
            tool_calls=calls,
            stop=_stop(finish, calls),
            usage=usage,
            model=model,
            detail=refusal,
        )

    def list_models(self) -> list[str]:
        data = http.get_json(self._url("models"), self._headers())
        return sorted(str(entry["id"]) for entry in data.get("data") or [] if "id" in entry)

    # ---

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path}"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        headers.update(self.options.get("headers") or {})
        return headers

    def _body(self, request: Request) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(_message(m) for m in request.messages)
        body: dict[str, Any] = {"model": request.model, "messages": messages}
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in request.tools
            ]
        body[self._max_tokens_param()] = request.max_tokens
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.effort:
            body["reasoning_effort"] = request.effort
        return body

    def _max_tokens_param(self) -> str:
        # OpenAI's own reasoning models reject max_tokens; everything compatible
        # still takes it. Configurable per provider for the exceptions.
        chosen = self.options.get("max_tokens_param")
        if chosen:
            return str(chosen)
        return "max_completion_tokens" if "api.openai.com" in self.base_url else "max_tokens"


def _message(message) -> dict[str, Any]:
    if message.role == "assistant":
        entry: dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if message.tool_calls:
            import json

            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return entry
    if message.role == "tool":
        return {"role": "tool", "tool_call_id": message.tool_call_id, "content": message.content}
    return {"role": message.role, "content": message.content}


def _call(entry: dict[str, Any]) -> ToolCall:
    function = entry.get("function") or {}
    arguments, error = parse_arguments(function.get("arguments"))
    return ToolCall(
        str(entry.get("id") or f"call_{function.get('name', '')}"),
        str(function.get("name") or ""),
        arguments,
        error=error,
    )


def _stop(finish: str | None, calls: list[ToolCall]) -> str:
    if calls:
        return "tool_calls"
    return _STOPS.get(finish or "", "other" if finish else "end")


def _usage(data: dict[str, Any] | None) -> Usage:
    if not data:
        return Usage()
    return Usage(int(data.get("prompt_tokens") or 0), int(data.get("completion_tokens") or 0))


def _refusal(message: dict[str, Any]) -> str:
    return str(message.get("refusal") or "")


def _error(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def _brief(data: Any) -> str:
    import json

    return json.dumps(data)[:200]
