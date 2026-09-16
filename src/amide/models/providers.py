"""Which provider serves a model, and with which key.

A model is named ``provider/model-id``. The provider half is looked up in the
config's ``[providers.*]`` tables, layered over a builtin set so the common
services work with nothing but their environment variable set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from amide.models.base import Adapter, ModelError

KINDS = ("openai", "anthropic", "gemini")

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

# Overridable per name from the config file; ``api_key_env = ""`` means no key.
BUILTIN: dict[str, dict[str, Any]] = {
    "anthropic": {"kind": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "openai": {"kind": "openai", "api_key_env": "OPENAI_API_KEY"},
    "gemini": {"kind": "gemini", "api_key_env": "GEMINI_API_KEY"},
    "deepseek": {
        "kind": "openai",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "mistral": {
        "kind": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "groq": {
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
    },
    "xai": {"kind": "openai", "base_url": "https://api.x.ai/v1", "api_key_env": "XAI_API_KEY"},
    "together": {
        "kind": "openai",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
    },
    "openrouter": {
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "local": {"kind": "openai", "base_url": "http://localhost:11434/v1", "api_key_env": ""},
}


@dataclass(frozen=True)
class Provider:
    name: str
    kind: str
    base_url: str
    api_key_env: str = ""
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str | None:
        return os.environ.get(self.api_key_env) or None if self.api_key_env else None

    @property
    def key_status(self) -> str:
        if not self.api_key_env:
            return "no key needed"
        return f"{self.api_key_env} set" if self.key else f"{self.api_key_env} not set"

    @property
    def usable(self) -> bool:
        return not self.api_key_env or self.key is not None

    def adapter(self) -> Adapter:
        """An adapter for this provider, or ModelError when its key is missing."""
        if self.api_key_env and not self.key:
            raise ModelError(
                f"provider {self.name} needs the {self.api_key_env} environment variable"
            )
        return _adapter_class(self.kind)(self.base_url, self.key, self.options)


def providers(config: Any = None) -> dict[str, Provider]:
    """Builtin providers with the config's ``[providers.*]`` layered on top."""
    entries: dict[str, dict[str, Any]] = {name: dict(data) for name, data in BUILTIN.items()}
    configured = config.providers if config is not None else {}
    for name, data in configured.items():
        merged = {**entries.get(name, {}), **data}
        entries[name] = merged
    return {name: _provider(name, data) for name, data in entries.items()}


def resolve(name: str | None, config: Any = None) -> tuple[Provider, str]:
    """``provider/model`` (or the config's default) to a provider and a model id."""
    if not name:
        name = (config.defaults.get("model") if config is not None else None) or ""
    if not name:
        raise ModelError(
            "no model given; pass --model provider/model-id or set [defaults] model in the config"
        )
    if "/" not in name:
        raise ModelError(
            f"model {name!r} needs a provider prefix, like anthropic/{name}; "
            f"providers: {', '.join(sorted(providers(config)))}"
        )
    provider_name, model_id = name.split("/", 1)
    known = providers(config)
    if provider_name not in known:
        raise ModelError(
            f"unknown provider {provider_name!r}; add [providers.{provider_name}] to the config "
            f"or use one of {', '.join(sorted(known))}"
        )
    if not model_id:
        raise ModelError(f"{name!r} names no model")
    return known[provider_name], model_id


def _provider(name: str, data: dict[str, Any]) -> Provider:
    kind = str(data.get("kind") or "openai")
    if kind not in KINDS:
        raise ModelError(f"provider {name}: kind must be one of {', '.join(KINDS)}, not {kind!r}")
    base_url = str(data.get("base_url") or DEFAULT_BASE_URLS[kind]).rstrip("/")
    options = {
        key: value
        for key, value in data.items()
        if key not in ("kind", "base_url", "api_key_env", "api_key")
    }
    return Provider(
        name=name,
        kind=kind,
        base_url=base_url,
        api_key_env=str(data.get("api_key_env") or ""),
        options=options,
    )


def _adapter_class(kind: str) -> type[Adapter]:
    if kind == "openai":
        from amide.models.openai import OpenAIAdapter

        return OpenAIAdapter
    if kind == "anthropic":
        from amide.models.anthropic import AnthropicAdapter

        return AnthropicAdapter
    from amide.models.gemini import GeminiAdapter

    return GeminiAdapter
