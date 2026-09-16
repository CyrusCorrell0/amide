from pathlib import Path

import pytest

from amide import config


def test_config_path_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("AMIDE_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config.config_path() == tmp_path / "xdg" / "amide" / "config.toml"
    monkeypatch.setenv("AMIDE_CONFIG", str(tmp_path / "mine.toml"))
    assert config.config_path() == tmp_path / "mine.toml"


def test_missing_file_is_empty_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("AMIDE_RUNS_DIR", raising=False)
    loaded = config.load(tmp_path / "none.toml")
    assert not loaded.exists
    assert loaded.runs_dir == Path(".amide/runs")
    assert loaded.tool_paths == []
    assert loaded.providers == {}


def test_init_writes_template_and_refuses_overwrite(tmp_path):
    path = tmp_path / "c.toml"
    assert config.init(path) == path
    loaded = config.load(path)
    assert loaded.exists
    assert loaded.providers["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert loaded.providers["anthropic"]["api_key_env"] == "ANTHROPIC_API_KEY"
    with pytest.raises(FileExistsError, match="--force"):
        config.init(path)
    config.init(path, force=True)


def test_values_and_redaction(tmp_path, monkeypatch):
    path = tmp_path / "c.toml"
    path.write_text(
        '[runs]\ndir = "~/exp"\n[tools]\npaths = ["~/t", "/abs"]\n'
        '[providers.x]\nkind = "openai"\napi_key = "sk-secret"\n'
    )
    monkeypatch.delenv("AMIDE_RUNS_DIR", raising=False)
    loaded = config.load(path)
    assert loaded.runs_dir == Path("~/exp").expanduser()
    assert loaded.tool_paths == [Path("~/t").expanduser(), Path("/abs")]
    assert loaded.redacted()["providers"]["x"]["api_key"] == "<redacted>"
    assert "sk-secret" not in str(loaded.redacted())
    monkeypatch.setenv("AMIDE_RUNS_DIR", "/elsewhere")
    assert loaded.runs_dir == Path("/elsewhere")


def test_bad_toml_is_reported(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text("not = [valid\n")
    with pytest.raises(ValueError, match="c.toml"):
        config.load(path)
