"""Tests for the viewer binary resolver."""

from __future__ import annotations

import collections
import hashlib
import http.server
import os
import platform
import socket
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import amide
from amide import tui

SUPPORTED = [
    ("Darwin", "arm64", "darwin", "arm64"),
    ("Darwin", "x86_64", "darwin", "amd64"),
    ("Linux", "x86_64", "linux", "amd64"),
    ("Linux", "aarch64", "linux", "arm64"),
    ("Windows", "AMD64", "windows", "amd64"),
]


def _set_platform(monkeypatch, system: str, machine: str) -> None:
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """No inherited override, and a cache root that is never the real one."""
    monkeypatch.delenv("AMIDE_TUI", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


@pytest.fixture
def release(monkeypatch, tmp_path):
    """A local stand-in for the GitHub release, counting hits per asset."""
    root = tmp_path / "release"
    version_dir = root / f"v{amide.__version__}"
    version_dir.mkdir(parents=True)
    asset = f"amide-tui_{amide.__version__}_linux_amd64"
    payload = b"#!/bin/sh\necho amide viewer\n" * 128
    (version_dir / asset).write_bytes(payload)
    (version_dir / "checksums.txt").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {asset}\n"
        f"{'0' * 64}  amide-tui_{amide.__version__}_darwin_arm64\n"
    )
    hits: collections.Counter[str] = collections.Counter()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root), **kwargs)

        def do_GET(self):
            hits[Path(self.path).name] += 1
            super().do_GET()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(tui, "_RELEASE_URL", f"http://127.0.0.1:{server.server_port}")
    _set_platform(monkeypatch, "Linux", "x86_64")
    try:
        yield SimpleNamespace(dir=version_dir, asset=asset, payload=payload, hits=hits)
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize(("system", "machine", "os_", "arch"), SUPPORTED)
def test_asset_name(monkeypatch, system, machine, os_, arch):
    _set_platform(monkeypatch, system, machine)
    suffix = ".exe" if os_ == "windows" else ""
    assert tui.asset_name() == f"amide-tui_{amide.__version__}_{os_}_{arch}{suffix}"


def test_asset_name_unsupported_names_the_platform(monkeypatch):
    _set_platform(monkeypatch, "Linux", "i686")
    with pytest.raises(tui.TuiUnavailable) as caught:
        tui.asset_name()
    message = str(caught.value)
    assert "Linux/i686" in message
    for supported in ("Darwin/arm64", "Linux/x86_64", "Windows/AMD64"):
        assert supported in message


def test_env_override_wins_without_touching_the_network(monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("binary_path() must not open a connection")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    stub = tmp_path / "elsewhere" / "amide-tui"
    monkeypatch.setenv("AMIDE_TUI", str(stub))
    assert tui.binary_path() == stub
    assert capsys.readouterr() == ("", "")


def test_download_verifies_then_caches(release, capsys):
    path = tui.binary_path()

    assert path == tui._cache_dir() / "amide-tui"
    assert path.read_bytes() == release.payload
    assert release.hits[release.asset] == 1
    line = capsys.readouterr().err
    assert line == f"Fetching viewer {amide.__version__} for linux-amd64 (1 MB)...\n"
    if os.name == "posix":
        assert os.access(path, os.X_OK)

    assert tui.binary_path() == path
    assert release.hits[release.asset] == 1
    assert capsys.readouterr() == ("", "")


def test_checksum_mismatch_leaves_nothing_behind(release):
    (release.dir / "checksums.txt").write_text(f"{'a' * 64}  {release.asset}\n")

    with pytest.raises(tui.TuiUnavailable, match="checksum"):
        tui.binary_path()

    cache = tui._cache_dir()
    assert not (cache / "amide-tui").exists()
    assert list(cache.iterdir()) == []


def test_missing_asset_reports_the_status_code(release):
    (release.dir / "checksums.txt").unlink()
    with pytest.raises(tui.TuiUnavailable, match="404"):
        tui.binary_path()


def test_unreachable_server_explains_the_offline_case(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(tui, "_RELEASE_URL", f"http://127.0.0.1:{port}")
    _set_platform(monkeypatch, "Linux", "x86_64")

    with pytest.raises(tui.TuiUnavailable, match="Couldn't reach GitHub"):
        tui.binary_path()
