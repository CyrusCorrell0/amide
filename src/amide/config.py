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
# model = "anthropic/claude-opus-5"

[providers.anthropic]
kind = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

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
kind = "openai"
base_url = "http://localhost:11434/v1"
api_key_env = "OLLAMA_API_KEY"
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
