import json
import urllib.error
from pathlib import Path

import pytest

from amide import config as config_module
from amide.harness.tool import ToolContext
from amide.models import (
    Message,
    ModelError,
    Reply,
    Request,
    ToolCall,
    Usage,
    http,
    providers,
    resolve,
)
from amide.models.anthropic import AnthropicAdapter
from amide.models.base import parse_arguments
from amide.models.gemini import GeminiAdapter
from amide.models.loop import converse, run_call
from amide.models.openai import OpenAIAdapter

WEATHER = {
    "name": "add",
    "description": "Add two integers.",
    "input_schema": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer", "default": 1}},
        "required": ["a"],
    },
}


class FakeHttp:
    """Records every request and answers from canned responses."""

    def __init__(self, monkeypatch):
        self.calls: list[dict] = []
        self.json: list[dict] = []
        self.sse: list[str] = []
        self.gets: list[dict] = []
        monkeypatch.setattr(http, "post_json", self.post_json)
        monkeypatch.setattr(http, "post_sse", self.post_sse)
        monkeypatch.setattr(http, "get_json", self.get_json)

    def post_json(self, url, body, headers, timeout=None):
        self.calls.append({"url": url, "body": body, "headers": headers})
        return self.json.pop(0)

    def post_sse(self, url, body, headers, timeout=None):
        self.calls.append({"url": url, "body": body, "headers": headers})
        text = self.sse.pop(0)
        yield from http.parse_sse(iter(text.splitlines(keepends=True)))

    def get_json(self, url, headers, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        return self.gets.pop(0)


@pytest.fixture
def fake(monkeypatch):
    return FakeHttp(monkeypatch)


def _request(**kwargs) -> Request:
    defaults = {
        "model": "m",
        "messages": [Message.user("add 2 and 3")],
        "system": "be brief",
        "tools": [WEATHER],
        "max_tokens": 100,
    }
    return Request(**{**defaults, **kwargs})


def _sse(*events: dict) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


# --- base --------------------------------------------------------------------


def test_parse_arguments():
    assert parse_arguments('{"a": 1}') == ({"a": 1}, None)
    assert parse_arguments("") == ({}, None)
    assert parse_arguments(None) == ({}, None)
    args, error = parse_arguments('{"a": ')
    assert args == {} and "not valid JSON" in error
    args, error = parse_arguments("[1]")
    assert args == {} and "JSON object" in error


def test_message_and_reply_validate_roles():
    with pytest.raises(ValueError, match="role"):
        Message("robot")
    with pytest.raises(ValueError, match="stop"):
        Reply(stop="whenever")
    assert Usage(1, 2) + Usage(3, 4) == Usage(4, 6)


# --- http ----------------------------------------------------------------------


def test_parse_sse_handles_multiline_data_and_done():
    text = 'event: x\ndata: {"a":\ndata: 1}\n\n: comment\ndata: [DONE]\n\ndata: {"b": 2}\n\n'
    assert list(http.parse_sse(iter(text.splitlines(keepends=True)))) == [{"a": 1}]
    with pytest.raises(ModelError, match="not JSON"):
        list(http.parse_sse(iter(["data: nope\n", "\n"])))


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def read(self):
        return self.body

    def close(self):
        pass

    def __iter__(self):
        return iter(self.body.splitlines(keepends=True))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(code: int, body: bytes, headers: dict | None = None):
    import email.message

    message = email.message.Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("http://x", code, "err", message, _Response(body))


def test_http_retries_then_gives_up(monkeypatch):
    attempts = []
    slept = []
    responses = [
        _http_error(429, b'{"error": {"message": "slow down"}}', {"Retry-After": "7"}),
        _http_error(500, b"<html>boom</html>"),
        _http_error(503, b'{"error": "still bad"}'),
    ]

    def fake_urlopen(request, timeout=None):
        attempts.append(request)
        raise responses.pop(0)

    monkeypatch.setattr(http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(http, "_sleep", slept.append)
    with pytest.raises(ModelError, match="503: still bad") as info:
        http.post_json("http://x", {"a": 1}, {"h": "1"})
    assert info.value.status == 503
    assert len(attempts) == 3
    assert slept == [7.0, 4.0]
    assert attempts[0].get_header("H") == "1"
    assert attempts[0].get_header("Content-type") == "application/json"


def test_http_does_not_retry_client_errors(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda request, timeout=None: (_ for _ in ()).throw(
            _http_error(401, b'{"error": {"message": "bad key"}}')
        ),
    )
    with pytest.raises(ModelError, match="401: bad key"):
        http.get_json("http://x", {})


def test_http_success_and_non_json(monkeypatch):
    monkeypatch.setattr(
        http.urllib.request, "urlopen", lambda request, timeout=None: _Response(b'{"ok": 1}')
    )
    assert http.post_json("http://x", {}, {}) == {"ok": 1}
    monkeypatch.setattr(
        http.urllib.request, "urlopen", lambda request, timeout=None: _Response(b"<html>")
    )
    with pytest.raises(ModelError, match="did not return JSON"):
        http.get_json("http://x", {})
    monkeypatch.setattr(
        http.urllib.request,
        "urlopen",
        lambda request, timeout=None: _Response(b'data: {"a": 1}\n\ndata: [DONE]\n\n'),
    )
    assert list(http.post_sse("http://x", {}, {})) == [{"a": 1}]


def test_http_connection_error_is_reported(monkeypatch):
    def fail(request, timeout=None):
        raise OSError("refused")

    monkeypatch.setattr(http.urllib.request, "urlopen", fail)
    monkeypatch.setattr(http, "_sleep", lambda s: None)
    with pytest.raises(ModelError, match="could not reach http://x: refused"):
        http.post_json("http://x", {}, {})


# --- openai ------------------------------------------------------------------------


def test_openai_complete_builds_request_and_parses_tool_calls(fake):
    fake.json.append(
        {
            "model": "gpt-x",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    adapter = OpenAIAdapter("https://api.openai.com/v1", "sk-test")
    reply = adapter.complete(_request())
    assert reply.stop == "tool_calls"
    assert reply.tool_calls == [ToolCall("call_1", "add", {"a": 2, "b": 3})]
    assert reply.usage == Usage(10, 5)
    assert reply.model == "gpt-x"

    call = fake.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    body = call["body"]
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "add 2 and 3"},
    ]
    assert body["tools"][0]["function"]["name"] == "add"
    assert body["tools"][0]["function"]["parameters"] == WEATHER["input_schema"]
    assert body["max_completion_tokens"] == 100 and "max_tokens" not in body


def test_openai_compatible_hosts_get_max_tokens_and_no_auth_without_key(fake):
    fake.json.append({"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]})
    adapter = OpenAIAdapter("http://localhost:11434/v1/", None, {"headers": {"X-Extra": "1"}})
    reply = adapter.complete(_request(tools=[], system=None, temperature=0.2))
    assert reply.text == "hi" and reply.stop == "end" and reply.usage == Usage()
    body = fake.calls[0]["body"]
    assert body["max_tokens"] == 100 and body["temperature"] == 0.2 and "tools" not in body
    assert "Authorization" not in fake.calls[0]["headers"]
    assert fake.calls[0]["headers"]["X-Extra"] == "1"
    fake.json.append({"choices": [{"finish_reason": "stop", "message": {"content": "hi"}}]})
    OpenAIAdapter("https://api.openai.com/v1", "k", {"max_tokens_param": "max_tokens"}).complete(
        _request()
    )
    assert fake.calls[1]["body"]["max_tokens"] == 100


def test_openai_replays_history(fake):
    fake.json.append({"choices": [{"finish_reason": "stop", "message": {"content": "5"}}]})
    call = ToolCall("call_1", "add", {"a": 2, "b": 3})
    messages = [
        Message.user("add"),
        Message("assistant", "", tool_calls=[call]),
        Message.tool_result(call, '{"sum": 5}'),
        Message.tool_result(ToolCall("call_2", "add", {}), "bad", is_error=True),
    ]
    OpenAIAdapter("https://x/v1", "k").complete(_request(messages=messages))
    sent = fake.calls[0]["body"]["messages"][1:]
    assert sent[1] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "add", "arguments": '{"a": 2, "b": 3}'},
            }
        ],
    }
    assert sent[2] == {"role": "tool", "tool_call_id": "call_1", "content": '{"sum": 5}'}
    assert sent[3]["tool_call_id"] == "call_2"


def test_openai_bad_json_arguments_and_no_choices(fake):
    fake.json.append(
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [{"id": "c", "function": {"name": "add", "arguments": "{"}}]
                    },
                }
            ]
        }
    )
    reply = OpenAIAdapter("https://x/v1", "k").complete(_request())
    assert reply.tool_calls[0].error and "not valid JSON" in reply.tool_calls[0].error
    fake.json.append({"error": "nope"})
    with pytest.raises(ModelError, match="no choices"):
        OpenAIAdapter("https://x/v1", "k").complete(_request())


def test_openai_stream_text_and_tool_calls(fake):
    fake.sse.append(
        _sse(
            {"model": "gpt-x", "choices": [{"delta": {"role": "assistant", "content": "Sum"}}]},
            {"choices": [{"delta": {"content": "ming."}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_9", "function": {"name": "add"}},
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"a":'}}]}}
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": " 4}"}}]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}},
        )
        + "data: [DONE]\n\n"
    )
    pieces = []
    reply = OpenAIAdapter("https://x/v1", "k").stream(_request(), pieces.append)
    assert pieces == ["Sum", "ming."]
    assert reply.text == "Summing."
    assert reply.tool_calls == [ToolCall("call_9", "add", {"a": 4})]
    assert reply.stop == "tool_calls" and reply.usage == Usage(7, 3) and reply.model == "gpt-x"
    body = fake.calls[0]["body"]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


def test_openai_stream_error_and_length(fake):
    fake.sse.append(_sse({"error": {"message": "over quota"}}))
    with pytest.raises(ModelError, match="over quota"):
        OpenAIAdapter("https://x/v1", "k").stream(_request(), lambda s: None)
    fake.sse.append(_sse({"choices": [{"delta": {"content": "x"}, "finish_reason": "length"}]}))
    reply = OpenAIAdapter("https://x/v1", "k").stream(_request(), lambda s: None)
    assert reply.stop == "length"


def test_openai_list_models(fake):
    fake.gets.append({"data": [{"id": "b-model"}, {"id": "a-model"}, {"object": "x"}]})
    assert OpenAIAdapter("https://x/v1", "k").list_models() == ["a-model", "b-model"]
    assert fake.calls[0]["url"] == "https://x/v1/models"


# --- anthropic -----------------------------------------------------------------


def test_anthropic_complete_request_shape_and_reply(fake):
    fake.json.append(
        {
            "model": "claude-opus-5",
            "stop_reason": "tool_use",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "Adding."},
                {"type": "tool_use", "id": "toolu_1", "name": "add", "input": {"a": 2, "b": 3}},
            ],
            "usage": {"input_tokens": 20, "output_tokens": 8, "cache_read_input_tokens": 5},
        }
    )
    adapter = AnthropicAdapter("https://api.anthropic.com", "sk-ant")
    reply = adapter.complete(_request(effort="high"))
    assert reply.text == "Adding."
    assert reply.tool_calls == [ToolCall("toolu_1", "add", {"a": 2, "b": 3})]
    assert reply.stop == "tool_calls" and reply.usage == Usage(25, 8)
    assert reply.raw[0]["type"] == "thinking"

    call = fake.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"
    body = call["body"]
    assert body["system"] == "be brief"
    assert body["max_tokens"] == 100
    assert body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "high"}
    assert "thinking" not in body and "temperature" not in body
    assert body["tools"] == [WEATHER]
    assert body["messages"] == [{"role": "user", "content": "add 2 and 3"}]


def test_anthropic_fallbacks_can_be_turned_off(fake):
    fake.json.append({"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
    AnthropicAdapter("https://api.anthropic.com", "k", {"fallbacks": False}).complete(_request())
    assert "fallbacks" not in fake.calls[0]["body"]
    assert "anthropic-beta" not in fake.calls[0]["headers"]


def test_anthropic_replays_raw_content_and_merges_tool_results(fake):
    fake.json.append({"stop_reason": "end_turn", "content": [{"type": "text", "text": "5"}]})
    raw = [
        {"type": "thinking", "thinking": "hmm", "signature": "s"},
        {"type": "tool_use", "id": "t1", "name": "add", "input": {"a": 1}, "_error": None},
        {"type": "tool_use", "id": "t2", "name": "add", "input": {"a": 2}},
    ]
    c1, c2 = ToolCall("t1", "add", {"a": 1}), ToolCall("t2", "add", {"a": 2})
    messages = [
        Message.user("go"),
        Message("assistant", "", tool_calls=[c1, c2], raw=raw),
        Message.tool_result(c1, '{"sum": 2}'),
        Message.tool_result(c2, "boom", is_error=True),
        Message.user("thanks"),
        Message("assistant", "sure", tool_calls=[c1]),
    ]
    AnthropicAdapter("https://api.anthropic.com", "k").complete(_request(messages=messages))
    sent = fake.calls[0]["body"]["messages"]
    assert sent[1]["role"] == "assistant"
    assert sent[1]["content"][0] == raw[0]
    assert "_error" not in sent[1]["content"][1]
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"sum": 2}'},
            {"type": "tool_result", "tool_use_id": "t2", "content": "boom", "is_error": True},
        ],
    }
    assert sent[3] == {"role": "user", "content": "thanks"}
    assert sent[4]["content"] == [
        {"type": "text", "text": "sure"},
        {"type": "tool_use", "id": "t1", "name": "add", "input": {"a": 1}},
    ]


def test_anthropic_refusal_and_errors(fake):
    fake.json.append(
        {
            "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "bio", "explanation": "no"},
            "content": [],
        }
    )
    reply = AnthropicAdapter("https://api.anthropic.com", "k").complete(_request())
    assert reply.stop == "refusal" and reply.detail == "bio; no" and reply.text == ""
    fake.json.append({"type": "error", "error": {"type": "x", "message": "bad model"}})
    with pytest.raises(ModelError, match="m: bad model"):
        AnthropicAdapter("https://api.anthropic.com", "k").complete(_request())
    fake.json.append({"stop_reason": "max_tokens", "content": [{"type": "text", "text": "x"}]})
    assert AnthropicAdapter("https://api.anthropic.com", "k").complete(_request()).stop == "length"


def test_anthropic_stream(fake):
    fake.sse.append(
        _sse(
            {
                "type": "message_start",
                "message": {"model": "claude-opus-5", "usage": {"input_tokens": 12}},
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "..."},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "abc"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Let"},
            },
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": " me."},
            },
            {
                "type": "content_block_start",
                "index": 2,
                "content_block": {"type": "tool_use", "id": "t1", "name": "add", "input": {}},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": '{"a": '},
            },
            {
                "type": "content_block_delta",
                "index": 2,
                "delta": {"type": "input_json_delta", "partial_json": "9}"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"output_tokens": 30},
            },
            {"type": "message_stop"},
        )
    )
    pieces = []
    reply = AnthropicAdapter("https://api.anthropic.com", "k").stream(_request(), pieces.append)
    assert pieces == ["Let", " me."]
    assert reply.text == "Let me."
    assert reply.tool_calls == [ToolCall("t1", "add", {"a": 9})]
    assert reply.stop == "tool_calls" and reply.usage == Usage(12, 30)
    assert reply.model == "claude-opus-5"
    assert reply.raw == [
        {"type": "thinking", "thinking": "...", "signature": "abc"},
        {"type": "text", "text": "Let me."},
        {"type": "tool_use", "id": "t1", "name": "add", "input": {"a": 9}},
    ]
    assert fake.calls[0]["body"]["stream"] is True


def test_anthropic_stream_error_event(fake):
    fake.sse.append(_sse({"type": "error", "error": {"message": "overloaded"}}))
    with pytest.raises(ModelError, match="overloaded"):
        AnthropicAdapter("https://api.anthropic.com", "k").stream(_request(), lambda s: None)


def test_anthropic_list_models(fake):
    fake.gets.append({"data": [{"id": "claude-b"}, {"id": "claude-a"}]})
    assert AnthropicAdapter("https://api.anthropic.com", "k").list_models() == [
        "claude-a",
        "claude-b",
    ]


# --- gemini ------------------------------------------------------------------------


def test_gemini_complete_request_shape_and_reply(fake):
    fake.json.append(
        {
            "modelVersion": "gemini-x",
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "role": "model",
                        "parts": [
                            {"text": "thinking", "thought": True},
                            {"text": "Adding."},
                            {"functionCall": {"name": "add", "args": {"a": 2, "b": 3}}},
                        ],
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 9,
                "candidatesTokenCount": 4,
                "thoughtsTokenCount": 2,
            },
        }
    )
    adapter = GeminiAdapter("https://generativelanguage.googleapis.com/v1beta", "g-key")
    reply = adapter.complete(_request(temperature=0.5))
    assert reply.text == "Adding."
    assert reply.tool_calls == [ToolCall("call_2", "add", {"a": 2, "b": 3})]
    assert reply.stop == "tool_calls" and reply.usage == Usage(9, 6) and reply.model == "gemini-x"

    call = fake.calls[0]
    assert call["url"].endswith("/v1beta/models/m:generateContent")
    assert call["headers"]["x-goog-api-key"] == "g-key"
    body = call["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert body["generationConfig"] == {"maxOutputTokens": 100, "temperature": 0.5}
    declaration = body["tools"][0]["functionDeclarations"][0]
    assert declaration["name"] == "add"
    assert "default" not in declaration["parameters"]["properties"]["b"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": "add 2 and 3"}]}]


def test_gemini_schema_arrays_get_items():
    from amide.models.gemini import _schema

    assert _schema({"type": "array", "default": [1]}) == {
        "type": "array",
        "items": {"type": "string"},
    }


def test_gemini_replays_history(fake):
    fake.json.append({"candidates": [{"content": {"parts": [{"text": "5"}]}}]})
    c1 = ToolCall("call_0", "add", {"a": 1})
    raw = [{"functionCall": {"name": "add", "args": {"a": 1}}, "thoughtSignature": "zz"}]
    messages = [
        Message.user("go"),
        Message("assistant", "", tool_calls=[c1], raw=raw),
        Message.tool_result(c1, '{"sum": 2}'),
        Message.tool_result(ToolCall("call_1", "add", {}), "boom", is_error=True),
        Message("assistant", "done", tool_calls=[c1]),
    ]
    GeminiAdapter("https://g/v1beta", "k").complete(_request(messages=messages))
    contents = fake.calls[0]["body"]["contents"]
    assert contents[1] == {"role": "model", "parts": raw}
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "add", "response": {"sum": 2}}},
            {"functionResponse": {"name": "add", "response": {"error": "boom"}}},
        ],
    }
    assert contents[3]["parts"] == [
        {"text": "done"},
        {"functionCall": {"name": "add", "args": {"a": 1}}},
    ]


def test_gemini_blocked_and_errors(fake):
    fake.json.append({"promptFeedback": {"blockReason": "SAFETY"}})
    reply = GeminiAdapter("https://g/v1beta", "k").complete(_request())
    assert reply.stop == "refusal" and reply.detail == "SAFETY"
    fake.json.append({"error": {"code": 400, "message": "bad request"}})
    with pytest.raises(ModelError, match="bad request"):
        GeminiAdapter("https://g/v1beta", "k").complete(_request())
    fake.json.append({"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}]})
    assert GeminiAdapter("https://g/v1beta", "k").complete(_request()).stop == "length"


def test_gemini_stream(fake):
    fake.sse.append(
        _sse(
            {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}], "modelVersion": "g-1"},
            {"candidates": [{"content": {"parts": [{"text": "lo."}]}}]},
            {
                "candidates": [
                    {
                        "content": {"parts": [{"functionCall": {"name": "add", "args": {"a": 1}}}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
            },
        )
    )
    pieces = []
    reply = GeminiAdapter("https://g/v1beta", "k").stream(_request(), pieces.append)
    assert pieces == ["Hel", "lo."]
    assert reply.text == "Hello." and reply.model == "g-1"
    assert reply.tool_calls == [ToolCall("call_1", "add", {"a": 1})]
    assert reply.stop == "tool_calls" and reply.usage == Usage(3, 2)
    assert reply.raw == [{"text": "Hello."}, {"functionCall": {"name": "add", "args": {"a": 1}}}]
    assert fake.calls[0]["url"].endswith(":streamGenerateContent?alt=sse")


def test_gemini_stream_blocked(fake):
    fake.sse.append(_sse({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}))
    reply = GeminiAdapter("https://g/v1beta", "k").stream(_request(), lambda s: None)
    assert reply.stop == "refusal" and reply.detail == "PROHIBITED_CONTENT"


def test_gemini_list_models(fake):
    fake.gets.append(
        {
            "models": [
                {"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-a"},
            ]
        }
    )
    assert GeminiAdapter("https://g/v1beta", "k").list_models() == ["gemini-a", "gemini-b"]


# --- providers ----------------------------------------------------------------------


def test_builtin_providers_and_config_overlay(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text(
        '[defaults]\nmodel = "mine/llama"\n'
        '[providers.mine]\nkind = "openai"\nbase_url = "http://box:8000/v1/"\napi_key_env = ""\n'
        'max_tokens_param = "max_tokens"\n'
        "[providers.anthropic]\nfallbacks = false\n"
    )
    config = config_module.load(path)
    known = providers(config)
    assert known["mine"].base_url == "http://box:8000/v1"
    assert known["mine"].options == {"max_tokens_param": "max_tokens"}
    assert known["mine"].key_status == "no key needed" and known["mine"].usable
    assert known["anthropic"].kind == "anthropic"
    assert known["anthropic"].options == {"fallbacks": False}
    assert known["deepseek"].base_url == "https://api.deepseek.com"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert known["anthropic"].key_status == "ANTHROPIC_API_KEY not set"
    assert not known["anthropic"].usable
    with pytest.raises(ModelError, match="needs the ANTHROPIC_API_KEY"):
        known["anthropic"].adapter()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk")
    adapter = known["anthropic"].adapter()
    assert isinstance(adapter, AnthropicAdapter) and adapter.key == "sk"
    assert isinstance(known["mine"].adapter(), OpenAIAdapter)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert isinstance(known["gemini"].adapter(), GeminiAdapter)

    provider, model_id = resolve(None, config)
    assert (provider.name, model_id) == ("mine", "llama")
    provider, model_id = resolve("anthropic/claude-opus-5", config)
    assert (provider.name, model_id) == ("anthropic", "claude-opus-5")
    provider, model_id = resolve("openrouter/meta/llama-3", config)
    assert (provider.name, model_id) == ("openrouter", "meta/llama-3")


def test_resolve_errors(tmp_path):
    config = config_module.load(tmp_path / "none.toml")
    with pytest.raises(ModelError, match="no model given"):
        resolve(None, config)
    with pytest.raises(ModelError, match="needs a provider prefix"):
        resolve("gpt-5", config)
    with pytest.raises(ModelError, match="unknown provider 'zzz'"):
        resolve("zzz/model", config)
    with pytest.raises(ModelError, match="names no model"):
        resolve("openai/", config)
    bad = tmp_path / "bad.toml"
    bad.write_text('[providers.x]\nkind = "carrier-pigeon"\n')
    with pytest.raises(ModelError, match="kind must be one of"):
        providers(config_module.load(bad))


# --- loop ----------------------------------------------------------------------------


class ScriptedAdapter:
    """Replies in order; records requests as they were at each call."""

    kind = "scripted"

    def __init__(self, *replies: Reply):
        self.replies = list(replies)
        self.seen: list[list[Message]] = []
        self.streamed = 0

    def complete(self, request):
        self.seen.append(list(request.messages))
        return self.replies.pop(0)

    def stream(self, request, on_text):
        self.streamed += 1
        reply = self.complete(request)
        if reply.text:
            on_text(reply.text)
        return reply

    def list_models(self):
        return ["one"]


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(workdir=tmp_path, run_dir=tmp_path)


def test_converse_runs_tools_until_the_model_stops(stub_registry, ctx):
    c1, c2 = ToolCall("1", "add", {"a": 2, "b": 3}), ToolCall("2", "write", {"text": "hi"})
    adapter = ScriptedAdapter(
        Reply(text="Working.", tool_calls=[c1, c2], stop="tool_calls", usage=Usage(10, 5)),
        Reply(text="Done: 5", stop="end", usage=Usage(20, 2)),
    )
    request = Request(model="m", messages=[Message.user("go")])
    text, calls = [], []
    outcome = converse(
        adapter,
        request,
        stub_registry,
        ctx,
        on_text=text.append,
        on_call=lambda call, result, ok: calls.append((call.name, ok)),
    )
    assert outcome.stop == "end" and outcome.turns == 2
    assert outcome.usage == Usage(30, 7)
    assert text == ["Working.", "Done: 5"]
    assert calls == [("add", True), ("write", True)]
    assert [m.role for m in request.messages] == ["user", "assistant", "tool", "tool", "assistant"]
    assert request.messages[2].content == '{"sum": 5}'
    assert json.loads(request.messages[3].content)["path"].endswith("out.txt")
    assert adapter.streamed == 2
    # The second call saw both results.
    assert [m.role for m in adapter.seen[1]] == ["user", "assistant", "tool", "tool"]


def test_converse_without_streaming_and_max_turns(stub_registry, ctx):
    call = ToolCall("1", "add", {"a": 1})
    adapter = ScriptedAdapter(*[Reply(tool_calls=[call], stop="tool_calls")] * 3)
    request = Request(model="m", messages=[Message.user("go")])
    outcome = converse(adapter, request, stub_registry, ctx, max_turns=2, stream=False)
    assert outcome.stop == "max_turns" and outcome.turns == 2 and len(outcome.calls) == 2
    assert adapter.streamed == 0 and len(adapter.replies) == 1


def test_run_call_error_paths(stub_registry, ctx, tmp_path):
    log = []
    ctx.log = log.append

    def check(call, **kwargs):
        record = run_call(stub_registry, call, ctx, **kwargs)
        assert not record.ok
        return record.result

    assert "not valid JSON" in check(ToolCall("1", "add", {}, error="not valid JSON: {"))
    assert "no tool named 'nope'" in check(ToolCall("1", "nope", {}))
    assert "add needs a" in check(ToolCall("1", "add", {}))
    assert "a must be integer" in check(ToolCall("1", "add", {"a": "x"}))
    assert "does not take zzz" in check(ToolCall("1", "add", {"a": 1, "zzz": 2}))
    assert "missing python module unicorn_module_xyz" in check(ToolCall("1", "needs_unicorn", {}))
    assert "There is no such thing" in check(ToolCall("1", "needs_unicorn", {}))
    assert "not approved" in check(ToolCall("1", "pricey", {}))
    assert "not approved" in check(ToolCall("1", "pricey", {}), approve=lambda spec, args: False)
    assert "boom" in check(ToolCall("1", "boom", {"message": "kaboom"}))
    assert "crashed: RuntimeError: kaboom" in check(ToolCall("1", "boom", {"message": "kaboom"}))
    assert any(line.startswith("error boom") for line in log)


def test_run_call_approval(stub_registry, ctx):
    asked = []

    def approve(spec, args):
        asked.append(spec.name)
        return True

    assert run_call(stub_registry, ToolCall("1", "pricey", {}), ctx, approve=approve).ok
    assert run_call(stub_registry, ToolCall("1", "pricey", {}), ctx, yes=True).ok
    assert asked == ["pricey"]
    record = run_call(stub_registry, ToolCall("1", "add", {"a": 1}), ctx)
    assert record.ok and record.result == '{"sum": 2}' and record.seconds >= 0
