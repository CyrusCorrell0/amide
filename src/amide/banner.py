"""The amide wordmark, and the short flourish that draws it.

Every glyph here is 7-bit ASCII on purpose. A Windows console hands Python a
cp1252 stdout the moment output is piped, and box-drawing characters raise
UnicodeEncodeError there -- so the logo that survives `amide > log.txt` is the
same one Windows Terminal draws.
"""

MARGIN = "  "
GUTTER = "    "

#: The amide bond itself: a carbonyl carbon double-bonded to O, single-bonded
#: to an N-H, with R groups standing in for the rest of the chain.
MOLECULE = (
    "    O",
    "    ||",
    " R--C--N--R'",
    "       |",
    "       H",
)

WORDMARK = (
    "                 _     _",
    "  __ _ _ __ ___ (_) __| | ___",
    " / _` | '_ ` _ \\| |/ _` |/ _ \\",
    "| (_| | | | | | | | (_| |  __/",
    " \\__,_|_| |_| |_|_|\\__,_|\\___|",
)

TAGLINE = "molecular dynamics in your terminal"
HINT = "run `amide -h` to see commands"

BONDS = "-|"

# ANSI, written by hand rather than through rich: importing rich costs 150-350ms
# on a cold Windows run, and every one of those milliseconds is a blank screen
# before the first frame. The flourish has to start sooner than that.
RESET = "\x1b[0m"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
ERASE_LINE = "\x1b[2K"

#: CPK colours, the ones every chemist already reads: O red, N blue, H white.
ATOM_STYLES = {
    "O": "\x1b[1;31m",
    "N": "\x1b[1;94m",
    "C": "\x1b[1;37m",
    "H": "\x1b[37m",
    "R": "\x1b[2;37m",
    "'": "\x1b[2;37m",
}
BOND_STYLE = "\x1b[2m"
WORD_STYLE = "\x1b[1;37m"
EDGE_STYLE = "\x1b[1;36m"
TEXT_STYLE = "\x1b[2m"

# The flourish reads as punctuation, never as a wait. It is driven off the clock
# rather than off a frame count, because time.sleep on Windows rounds up to the
# 15.6ms timer tick and a counted animation drifts past a second there.
DURATION = 0.55
TICK = 1 / 30

# When each stage lands, as a fraction of DURATION.
BONDS_AT = 0.18
WORDMARK_AT = 0.32
TAGLINE_AT = 0.58
SETTLED_AT = 0.90

SWEEP = 5  # width of the bright edge that wipes the wordmark in

# Below this the logo wraps, and a wrapped frame cannot be redrawn in place.
MIN_WIDTH = 60
MIN_HEIGHT = 12

_MOLECULE_WIDTH = max(map(len, MOLECULE))
# One sweep past the end, so the bright edge trails off instead of sticking.
_WORDMARK_WIDTH = max(map(len, WORDMARK)) + SWEEP


def footer(version: str) -> str:
    """The version, and the one thing to try next."""
    return f"v{version}  |  {HINT}"


def logo(version: str) -> str:
    """The finished banner, exactly as the animation leaves it."""
    rows = [
        f"{MARGIN}{mol:<{_MOLECULE_WIDTH}}{GUTTER}{word}".rstrip()
        for mol, word in zip(MOLECULE, WORDMARK)
    ]
    return "\n".join(["", *rows, "", MARGIN + TAGLINE, MARGIN + footer(version), ""])


def show(version: str) -> None:
    """Draw the banner: animated on a live terminal, instant anywhere else."""
    if _plain():
        print(logo(version))
    else:
        _animate(version)


def _plain() -> bool:
    """True when the caller is a pipe, a log, or anything that hates cursor moves."""
    import os
    import sys

    if not sys.stdout.isatty():
        return True
    return any(
        os.environ.get(name) for name in ("NO_COLOR", "CI", "AMIDE_NO_ANIMATION")
    )


def _animate(version: str) -> None:
    import sys
    import time

    width, height = _terminal_size()
    if width < MIN_WIDTH or height < MIN_HEIGHT or not _enable_ansi():
        print(logo(version))
        return

    out = sys.stdout
    rows = len(_at(version, 0.0))
    out.write(HIDE_CURSOR + "\n" * rows)  # claim the space, scrolling if we must
    try:
        start = time.perf_counter()
        while True:
            progress = min(1.0, (time.perf_counter() - start) / DURATION)
            frame = "".join(f"{ERASE_LINE}{line}\n" for line in _at(version, progress))
            out.write(f"\x1b[{rows}A{frame}")
            out.flush()
            if progress == 1.0:
                return
            time.sleep(TICK)
    finally:
        out.write(SHOW_CURSOR)
        out.flush()


def _at(version: str, progress: float) -> list[str]:
    """The frame `progress` of the way through: atoms, bonds, a wipe, a type-out."""
    return _draw(
        version,
        bonds=progress >= BONDS_AT,
        word=round(_WORDMARK_WIDTH * _span(progress, WORDMARK_AT, TAGLINE_AT)),
        tag=round(len(TAGLINE) * _span(progress, TAGLINE_AT, SETTLED_AT)),
        done=progress >= SETTLED_AT,
    )


def _span(progress: float, start: float, end: float) -> float:
    """0.0 up to `start`, 1.0 from `end` on, a straight line between."""
    return min(1.0, max(0.0, (progress - start) / (end - start)))


def _draw(
    version: str, *, bonds: bool, word: int = 0, tag: int = 0, done: bool = False
) -> list[str]:
    """One frame's lines: the molecule, then the wordmark wiped in to `word` columns."""
    rows = [""]
    for mol, mark in zip(MOLECULE, WORDMARK):
        row = [MARGIN]
        for char in mol.ljust(_MOLECULE_WIDTH):
            if char == " " or (char in BONDS and not bonds):
                row.append(" ")
            elif char in BONDS:
                row.append(_paint(char, BOND_STYLE))
            else:
                row.append(_paint(char, ATOM_STYLES.get(char, WORD_STYLE)))
        edge = max(0, word - SWEEP)
        row.append(GUTTER)
        row.append(_paint(mark[:edge], WORD_STYLE))
        row.append(_paint(mark[edge:word], EDGE_STYLE))
        rows.append("".join(row))
    rows.append("")
    rows.append(_paint(MARGIN + TAGLINE[:tag], TEXT_STYLE))
    rows.append(_paint(MARGIN + footer(version), TEXT_STYLE) if done else "")
    rows.append("")
    return rows


def _paint(text: str, style: str) -> str:
    return f"{style}{text}{RESET}" if text else ""


def _terminal_size() -> tuple[int, int]:
    """Columns and lines, honouring COLUMNS/LINES as shutil does. (0, 0) if unknown."""
    import os

    try:
        return int(os.environ["COLUMNS"]), int(os.environ["LINES"])
    except (KeyError, ValueError):
        pass
    try:
        size = os.get_terminal_size()
    except OSError:
        return 0, 0
    return size.columns, size.lines


def _enable_ansi() -> bool:
    """Windows consoles need VT processing switched on; every other terminal has it."""
    import sys

    if sys.platform != "win32":
        return True

    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    enable_virtual_terminal_processing = 0x0004
    return bool(
        kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal_processing)
    )
