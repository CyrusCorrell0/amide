"""Locate the amide viewer binary, downloading it from GitHub on first use."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import amide

_RELEASE_URL = "https://github.com/CyrusCorrell0/amide/releases/download"
_TIMEOUT = 30
_CHUNK = 1 << 16

# platform.system() and a normalized platform.machine() -> release asset target.
_ASSETS = {
    ("Darwin", "arm64"): ("darwin", "arm64"),
    ("Darwin", "x86_64"): ("darwin", "amd64"),
    ("Linux", "x86_64"): ("linux", "amd64"),
    ("Linux", "arm64"): ("linux", "arm64"),
    ("Windows", "x86_64"): ("windows", "amd64"),
}
_MACHINES = {"amd64": "x86_64", "aarch64": "arm64"}
_SUPPORTED = "Darwin/arm64, Darwin/x86_64, Linux/x86_64, Linux/aarch64, Windows/AMD64"
_UNREACHABLE = (
    "Couldn't reach GitHub to fetch the viewer. "
    "Try again online, or set AMIDE_TUI to a local binary."
)


class TuiUnavailable(Exception):
    """The viewer binary could not be found, downloaded, or verified."""


def asset_name() -> str:
    """Name of the release asset for this machine."""
    os_, arch = _target()
    return f"amide-tui_{amide.__version__}_{os_}_{arch}{'.exe' if os_ == 'windows' else ''}"


def binary_path() -> Path:
    """Path to the viewer binary, downloading and verifying it if needed."""
    override = os.environ.get("AMIDE_TUI")
    if override:
        return Path(override)
    os_, arch = _target()
    cached = _cache_dir() / ("amide-tui.exe" if os_ == "windows" else "amide-tui")
    return cached if cached.exists() else _download(cached, os_, arch)


def _target() -> tuple[str, str]:
    system, machine = platform.system(), platform.machine()
    key = (system, _MACHINES.get(machine.lower(), machine.lower()))
    if key not in _ASSETS:
        raise TuiUnavailable(
            f"There is no amide viewer build for {system}/{machine}; "
            f"the supported platforms are {_SUPPORTED}."
        )
    return _ASSETS[key]


def _cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(root) / "amide" / "tui" / amide.__version__


def _download(dest: Path, os_: str, arch: str) -> Path:
    asset = asset_name()
    base = f"{_RELEASE_URL}/v{amide.__version__}"
    expected = _checksum(f"{base}/checksums.txt", asset)
    dest.parent.mkdir(parents=True, exist_ok=True)

    response = _open(f"{base}/{asset}")
    print(
        f"Fetching viewer {amide.__version__} for {os_}-{arch}{_size(response)}...",
        file=sys.stderr,
    )
    digest = hashlib.sha256()
    handle, name = tempfile.mkstemp(dir=dest.parent)
    partial = Path(name)
    try:
        with os.fdopen(handle, "wb") as out, response:
            for chunk in iter(lambda: response.read(_CHUNK), b""):
                digest.update(chunk)
                out.write(chunk)
    except OSError:
        partial.unlink(missing_ok=True)
        raise TuiUnavailable(_UNREACHABLE) from None
    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise TuiUnavailable(
            "Downloaded viewer failed checksum verification; "
            "the partial file was deleted. Try again."
        )
    partial.chmod(0o755)
    os.replace(partial, dest)
    return dest


def _checksum(url: str, asset: str) -> str:
    with _open(url) as response:
        lines = response.read().decode().splitlines()
    for line in lines:
        digest, _, name = line.partition("  ")
        if name.strip() == asset:
            return digest.strip()
    raise TuiUnavailable(f"The release for v{amide.__version__} lists no checksum for {asset}.")


def _open(url: str):
    try:
        return urllib.request.urlopen(url, timeout=_TIMEOUT)
    except urllib.error.HTTPError as error:
        raise TuiUnavailable(
            f"GitHub returned {error.code} for {url}, so the viewer for this release "
            "is unavailable."
        ) from None
    except OSError:
        raise TuiUnavailable(_UNREACHABLE) from None


def _size(response) -> str:
    length = response.headers.get("Content-Length", "")
    if not length.isdigit():
        return ""
    return f" ({max(1, round(int(length) / (1 << 20)))} MB)"
