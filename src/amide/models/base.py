"""The one shape every model adapter speaks.

A conversation is a list of ``Message``; a call returns a ``Reply``. Tool
definitions are the JSON-schema dicts ``ToolSpec.to_schema`` produces, and
each adapter turns them into its provider's wire format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from amide.harness.errors import HarnessError

ROLES = ("system", "user", "assistant", "tool")
STOPS = ("end", "tool_calls", "length", "refusal", "other")


class ModelError(HarnessError):
    """A provider call failed; the message names the provider and what it said."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ToolCall:
    """The model asked for a tool. ``error`` is set when its arguments were not JSON."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens
        )


@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Tool results only: which call this answers, the tool's name, and whether it failed.
    tool_call_id: str | None = None
    name: str | None = None
    is_error: bool = False
    # Assistant turns only: the provider's own content, replayed verbatim on the
    # next request so reasoning blocks and signatures survive the round trip.
    raw: Any = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"message role must be one of {ROLES}, not {self.role!r}")

    @classmethod
    def user(cls, text: str) -> Message:
        return cls("user", text)

    @classmethod
    def tool_result(cls, call: ToolCall, content: str, is_error: bool = False) -> Message:
        return cls("tool", content, tool_call_id=call.id, name=call.name, is_error=is_error)


@dataclass
class Reply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    detail: str = ""
    raw: Any = None

    def __post_init__(self) -> None:
        if self.stop not in STOPS:
            raise ValueError(f"stop must be one of {STOPS}, not {self.stop!r}")

    def message(self) -> Message:
        """This reply as the assistant turn to append to the conversation."""
        return Message("assistant", self.text, tool_calls=list(self.tool_calls), raw=self.raw)


@dataclass
class Request:
    model: str
    messages: list[Message]
    system: str | None = None
    tools: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 16000
    temperature: float | None = None
    effort: str | None = None


TextSink = Callable[[str], None]


class Adapter(ABC):
    """One provider kind. Subclasses set ``kind`` and implement three calls."""

    kind = ""

    def __init__(self, base_url: str, key: str | None, options: dict[str, Any] | None = None):
        self.base_url = base_url.rstrip("/")
        self.key = key
        self.options = options or {}

    @abstractmethod
    def complete(self, request: Request) -> Reply:
        """One call, whole reply."""

    @abstractmethod
    def stream(self, request: Request, on_text: TextSink) -> Reply:
        """One call, text deltas handed to ``on_text`` as they arrive."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Model ids the provider serves to this key."""


def parse_arguments(raw: str | None) -> tuple[dict[str, Any], str | None]:
    """Tool arguments arrive as a JSON string; a malformed one is the model's error."""
    import json

    if not raw or not raw.strip():
        return {}, None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        return {}, f"arguments are not valid JSON ({error.msg} at {error.pos}): {raw[:200]}"
    if not isinstance(value, dict):
        return {}, f"arguments must be a JSON object, got {type(value).__name__}"
    return value, None
