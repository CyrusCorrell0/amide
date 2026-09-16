"""The user's config file: ``~/.config/amide/config.toml``.

Keys are named by the environment variable that holds them, never stored.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATE = """\
# amide configuration. Keys live in environment variables named here; this
# file only says which variable to read.

[runs]
dir = ".amide/runs"            # relative paths resolve against the working directory

[tools]
paths = []                     # extra directories of *.py and *.yaml tools

[defaults]
# model = "anthropic/claude-opus-5"   # `amide ask` and `amide experiment` without --model
# max_tokens = 16000

[agents]
# model = "anthropic/claude-opus-5"   # every agent role, unless [agents.models] says otherwise
# max_tokens = 2000000                # experiment budget defaults; the CLI flags override
# max_seconds = 3600
# max_dollars = 20

[agents.models]
# find = "deepseek/deepseek-chat"     # a cheaper model for a role
# review = "openai/gpt-5"

[pricing]
# "anthropic/claude-opus-5" = { input = 5.0, output = 25.0 }   # dollars per million tokens

# Providers. These builtins exist without being listed here; list one to
# change it, or add your own. `amide models list` shows them all.
#   anthropic  openai  gemini  deepseek  mistral  groq  xai  together
#   openrouter local (http://localhost:11434/v1, no key)

[providers.anthropic]
kind = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"
# fallbacks = false             # do not re-run declined requests on a fallback model

[providers.openai]
kind = "openai"
api_key_env = "OPENAI_API_KEY"

[providers.deepseek]
kind = "openai"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[providers.gemini]
kind = "gemini"
api_key_env = "GEMINI_API_KEY"

[providers.local]
kind = "openai"                # Ollama, vLLM, anything OpenAI-compatible
base_url = "http://localhost:11434/v1"
api_key_env = ""               # empty: no key is sent or required

# Runners execute protocol steps on another machine through a CLI you already
# use (ssh, brev, a cluster wrapper). amide copies the run directory over, runs
# `amide tools run` there, and copies the step directory back. Templates get
# {src}, {dst}, {host}, and {command} (already shell-quoted). Select one with
# `amide run --runner gpu`; steps cheaper than --remote-cost stay local.
[runners.gpu]
# host = "my-gpu-box"
# copy_to = "rsync -a {src}/ {host}:{dst}/"
# copy_from = "rsync -a {host}:{src}/ {dst}/"
# exec = "ssh {host} {command}"
# python = "python3"           # remote interpreter that has amide installed
# root = "/tmp/amide"          # remote directory that holds copied runs
"""

_SECRET_KEYS = {"api_key", "token", "secret", "password"}


@dataclass
class Config:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)
    exists: bool = False

    @property
    def runs_dir(self) -> Path:
        override = os.environ.get("AMIDE_RUNS_DIR")
        raw = override or self.raw.get("runs", {}).get("dir") or ".amide/runs"
        return Path(raw).expanduser()

    @property
    def tool_paths(self) -> list[Path]:
        paths = self.raw.get("tools", {}).get("paths") or []
        return [Path(entry).expanduser() for entry in paths]

    @property
    def defaults(self) -> dict[str, Any]:
        return dict(self.raw.get("defaults", {}))

    @property
    def providers(self) -> dict[str, dict[str, Any]]:
        return {name: dict(entry) for name, entry in self.raw.get("providers", {}).items()}

    @property
    def runners(self) -> dict[str, dict[str, Any]]:
        """``[runners.*]``: command templates that run a step on another machine."""
        return {name: dict(entry) for name, entry in self.raw.get("runners", {}).items()}

    @property
    def agents(self) -> dict[str, Any]:
        """``[agents]``: ``model`` for every role, ``[agents.models]`` per role, budget defaults."""
        return dict(self.raw.get("agents", {}))

    @property
    def pricing(self) -> dict[str, Any]:
        """``[pricing]``: ``"provider/model" = {input = $/M, output = $/M}``."""
        return dict(self.raw.get("pricing", {}))

    def redacted(self) -> dict[str, Any]:
        """The raw config with any literal secret replaced, for printing."""
        return _redact(self.raw)


def config_path() -> Path:
    override = os.environ.get("AMIDE_CONFIG")
    if override:
        return Path(override).expanduser()
    root = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(root) / "amide" / "config.toml"


def load(path: Path | None = None) -> Config:
    """Read the config file if it exists; an absent file is an empty config."""
    target = path or config_path()
    if not target.exists():
        return Config(path=target)
    with target.open("rb") as handle:
        try:
            raw = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{target}: {error}") from None
    return Config(path=target, raw=raw, exists=True)


def init(path: Path | None = None, force: bool = False) -> Path:
    """Write the template config. Refuses to overwrite unless ``force``."""
    target = path or config_path()
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists; pass --force to overwrite it")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(TEMPLATE)
    return target


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key in _SECRET_KEYS and val else _redact(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
