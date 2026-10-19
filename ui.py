"""Look of the status line: colours, bars and the user's theme file.

The theme lives in guard.STATE_DIR / "theme.json". Every key is optional; anything
missing or invalid falls back to the default, so a bad file never breaks the status line.
`validate` is the strict version, used by `session-guard theme` to reject bad input.

Stdlib only.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import os
import re

import guard

RESET = "\033[0m"
ROLES = ("ok", "warn", "danger", "dim")
PRESETS = {  # name -> hex colour per role
    "default": {"ok": "#87af87", "warn": "#d7af5f", "danger": "#d75f5f", "dim": "#8a8a8a"},
    "ocean": {"ok": "#5eead4", "warn": "#60a5fa", "danger": "#f472b6", "dim": "#64748b"},
    "mono": {"ok": "#9e9e9e", "warn": "#d0d0d0", "danger": "#ffffff", "dim": "#6c6c6c"},
    "high-contrast": {"ok": "#00ff87", "warn": "#ffff00", "danger": "#ff5f5f", "dim": "#bcbcbc"},
    # Colour-blind safe (Okabe-Ito): blue, orange, magenta. No red/green pair.
    "colorblind": {"ok": "#56b4e9", "warn": "#e69f00", "danger": "#cc79a7", "dim": "#999999"},
}
BARS = {"blocks": "▰▱", "solid": "█░", "dots": "●○"}  # filled, empty
MODES = ("auto", "truecolor", "256")
SEGMENTS = ("ctx", "bar", "eta", "cost", "last", "jev", "grab", "hint")
DEFAULT_SEGMENTS = [list(SEGMENTS)]  # one line


def to256(color: str) -> int:
    """Nearest xterm-256 colour (6x6x6 cube or grey ramp; the 16 terminal colours are theme-dependent, so skipped)."""
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    return min(_PALETTE, key=lambda p: sum((a - b) ** 2 for a, b in zip(rgb, p[1])))[0]


_STEPS = (0, 95, 135, 175, 215, 255)
_PALETTE = [(16 + 36 * r + 6 * g + b, (_STEPS[r], _STEPS[g], _STEPS[b])) for r in range(6) for g in range(6) for b in range(6)]
_PALETTE += [(232 + i, (8 + 10 * i,) * 3) for i in range(24)]


def paint(text: str, role: str, theme: dict) -> str:
    """Colour text with the theme's colour for the role. Plain text if NO_COLOR is set.

    Not gated on isatty: the status line's stdout is a pipe.
    """
    if not text or os.environ.get("NO_COLOR"):
        return text
    color = theme["colors"][role]
    mode = theme["mode"]
    if mode == "auto":
        mode = "truecolor" if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit") else "256"
    if mode == "truecolor":
        code = "38;2;{};{};{}".format(*(int(color[i:i + 2], 16) for i in (1, 3, 5)))
    else:
        code = f"38;5;{to256(color)}"
    return f"\033[{code}m{text}{RESET}"


# Theme file ---------------------------------------------------------------------------------------

def _hex(v):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", v.strip()) if isinstance(v, str) else None
    if not m:
        raise ValueError(f"a colour must look like #rrggbb, got {v!r}")
    return "#" + m.group(1).lower()


def _one_of(options):
    def check(v):
        if not isinstance(v, str) or v not in options:
            raise ValueError(f"must be one of: {', '.join(options)} (got {v!r})")
        return v
    return check


def _width(v):
    if isinstance(v, bool) or not isinstance(v, int) or not 4 <= v <= 20:
        raise ValueError(f"must be a whole number from 4 to 20 (got {v!r})")
    return v


def _emoji(v):
    if not isinstance(v, bool):
        raise ValueError(f"must be on or off (true or false in JSON), got {v!r}")
    return v


def _segments(v):
    ok = isinstance(v, list) and v and all(isinstance(line, list) and line for line in v)
    if not ok or any(s not in SEGMENTS for line in v for s in line):
        raise ValueError(f"must be a list of lines, each a list of: {', '.join(SEGMENTS)} (got {v!r})")
    return v


CHECKS = {"preset": _one_of(tuple(PRESETS)), "bar": _one_of(tuple(BARS)), "bar_width": _width,
          "emoji": _emoji, "mode": _one_of(MODES), "segments": _segments}


def _clean(raw, strict: bool) -> tuple:
    """(valid part of raw, list of problems). Problems are only worth reporting when strict."""
    if not isinstance(raw, dict):
        return {}, ["the theme must be a JSON object"]
    out, errors = {}, []
    for key, value in raw.items():
        try:
            if key == "colors":
                if not isinstance(value, dict):
                    raise ValueError(f"must be an object with keys: {', '.join(ROLES)}")
                for role, c in value.items():
                    try:
                        if role not in ROLES:
                            raise ValueError(f"unknown colour name; valid: {', '.join(ROLES)}")
                        out.setdefault("colors", {})[role] = _hex(c)
                    except ValueError as e:
                        errors.append(f"colors.{role}: {e}")
            elif key in CHECKS:
                out[key] = CHECKS[key](value)
            else:
                raise ValueError(f"unknown key; valid keys: colors, {', '.join(CHECKS)}")
        except ValueError as e:
            errors.append(f"{key}: {e}")
    return out, errors


def validate(theme: dict) -> tuple:
    """(clean theme, None) or ({}, what is wrong and what is valid)."""
    out, errors = _clean(theme, True)
    return ({}, "\n".join(errors)) if errors else (out, None)


def effective(raw: dict) -> dict:
    """Full theme: defaults, then the preset, then the explicit colours. raw must be clean."""
    return {
        "preset": raw.get("preset", "default"),
        "colors": {**PRESETS["default"], **PRESETS[raw.get("preset", "default")], **raw.get("colors", {})},
        "bar": raw.get("bar", "blocks"),
        "bar_width": raw.get("bar_width", 10),
        "emoji": raw.get("emoji", True),
        "mode": raw.get("mode", "auto"),
        "segments": raw.get("segments", DEFAULT_SEGMENTS),
    }


def theme_file():
    return guard.STATE_DIR / "theme.json"


def load_raw() -> dict:
    """What the user's theme file sets, with invalid or unreadable parts left out. Never raises.

    Unknown segment names (say, one from an older version) are skipped; the rest of the layout is kept.
    """
    try:
        raw = json.loads(theme_file().read_text())
        if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
            lines = [[n for n in line if n in SEGMENTS] for line in raw["segments"] if isinstance(line, list)]
            raw["segments"] = [line for line in lines if line]
        return _clean(raw, False)[0]
    except Exception:
        return {}


def load() -> dict:
    return effective(load_raw())
