from __future__ import annotations

import os
import sys

_RESET = "\x1b[0m"

_STYLES = {
    "cyan": "\x1b[36m",
    "green": "\x1b[32m",
    "red": "\x1b[31m",
    "yellow": "\x1b[33m",
    "bold": "\x1b[1m",
}


def _detect() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        if not sys.stdout.isatty():
            return False
    except Exception:
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


COLORS_ENABLED = _detect()


def colorize(text: str, style: str) -> str:
    """Wrap already-sanitized display text in an ANSI style when on a TTY."""
    if not COLORS_ENABLED:
        return text
    prefix = _STYLES.get(style)
    if prefix is None:
        return text
    return f"{prefix}{text}{_RESET}"
