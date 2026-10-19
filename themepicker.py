"""Interactive picker for `session-guard theme`: arrow keys, Enter, `s` to save, `q` to cancel.

Three parts, so the logic can be tested without a terminal:
    handle_key(state, key) -> (state, action)   action is None, "save" or "cancel"
    render(state, columns) -> str               the whole screen as text with colour codes
    run(raw) -> theme dict or None              the thin terminal layer (needs termios, so not Windows)

The state is a theme (the shape of ui.effective) plus "row" (cursor) and "palette" (colour screen or None).
Plain ANSI, no curses, so the previews use the same truecolor or 256 codes as the status line.

Stdlib only.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import copy
import os
import re
import shutil
import string
import sys
import unicodedata

try:
    import select
    import termios
    import tty
    AVAILABLE = True
except ImportError:  # Windows
    AVAILABLE = False

import statusline
import ui

# Fake sessions on a 1M window, for previews: about 14%, 65% and 85% full.
PREVIEWS = [
    ({"context": ctx, "total": total, "last_msg": 0.14, "agents": agents, "prompts": ["x"], "peak": ctx,
      "prompt_ctx": [ctx - 3 * step, ctx - 2 * step, ctx - step, ctx]},
     {"window": 1_000_000, "jev": jev})
    for ctx, step, total, agents, jev in ((140_000, 25_000, 2.61, 0.34, 0.1), (650_000, 30_000, 9.40, 0, 0.5), (850_000, 40_000, 17.20, 1.1, 0.93))
]
ROWS = (["preset"] + [f"color:{r}" for r in ui.ROLES] + ["bar", "bar_width", "emoji"]
        + [f"seg:{s}" for s in ui.SEGMENTS] + ["save", "cancel"])
HELP = {"ctx": "context size", "bar": "how full the window is", "eta": "messages left to the next level",
        "cost": "cost so far", "last": "cost of your last message", "jev": "new-task check", "hint": "/handover hint"}
# Every preset colour, then a few more.
SWATCHES = list(dict.fromkeys([c for p in ui.PRESETS.values() for c in p.values()]
                              + ["#ff9900", "#a78bfa", "#f87171", "#22d3ee"]))
GRID = 8  # swatches per row
ANSI = re.compile(r"\033\[[0-9;]*m")


def width(text: str) -> int:
    """Columns the text takes on screen: colour codes take none, emoji take two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in ANSI.sub("", text))


def preview_lines(theme: dict, only: int | None = None, columns: int | None = None) -> list:
    """The status line for the fake sessions. Too wide for columns: segments are dropped from the end (display only)."""
    out = []
    for session, state in PREVIEWS if only is None else PREVIEWS[only:only + 1]:
        for names in theme["segments"]:
            names = list(names)
            while True:
                line = statusline.render(session, state, {**theme, "segments": [names]})
                if columns is None or len(names) < 2 or width(line) <= columns:
                    break
                names.pop()
            if line:
                out.append(line)
    return out


# State and keys -----------------------------------------------------------------------------------

def initial_state(raw: dict) -> dict:
    theme = ui.effective(raw)
    return {**theme, "colors": dict(theme["colors"]), "segments": [list(line) for line in theme["segments"]],
            "row": 0, "palette": None}


def to_theme(state: dict) -> dict:
    """The theme to save: the preset, then only what differs from it (or from the defaults)."""
    base = ui.effective({})
    out = {"preset": state["preset"]}
    colors = {r: c for r, c in state["colors"].items() if c != ui.PRESETS[state["preset"]][r]}
    if colors:
        out["colors"] = colors
    out.update({k: state[k] for k in ("bar", "bar_width", "emoji", "mode", "segments") if state[k] != base[k]})
    return out


def _toggle(state: dict, name: str) -> None:
    lines = state["segments"]
    shown = [n for line in lines for n in line]
    if name in shown:
        if len(shown) > 1:  # never hide the last one
            state["segments"] = [line for line in ([n for n in line if n != name] for line in lines) if line]
    else:  # back in the usual order: after the nearest segment that comes before it, else first
        idx = ui.SEGMENTS.index(name)
        before = [(ui.SEGMENTS.index(n), li, i) for li, line in enumerate(lines) for i, n in enumerate(line) if ui.SEGMENTS.index(n) < idx]
        if before:
            _, li, i = max(before)
            lines[li].insert(i + 1, name)
        else:
            lines[0].insert(0, name)


def _cycle(current, options, step):
    options = list(options)
    return options[(options.index(current) + step) % len(options)]


def handle_key(state: dict, key: str) -> tuple:
    """(new state, action). Keys: up down left right enter esc backspace ctrl-c, or the character itself."""
    s = copy.deepcopy(state)
    if key == "ctrl-c":
        return s, "cancel"
    if s["palette"]:
        return _palette_key(s, key)
    row = ROWS[s["row"]]
    if key in ("up", "k", "down", "j"):
        s["row"] = (s["row"] + (-1 if key in ("up", "k") else 1)) % len(ROWS)
    elif key == "s":
        return s, "save"
    elif key in ("q", "esc"):
        return s, "cancel"
    elif key in ("left", "h", "right", "l", "enter", " "):
        step = -1 if key in ("left", "h") else 1
        pick = key in ("enter", " ")
        if row == "preset":
            s["preset"] = _cycle(s["preset"], ui.PRESETS, step)
            s["colors"] = dict(ui.PRESETS[s["preset"]])
        elif row == "bar":
            s["bar"] = _cycle(s["bar"], ui.BARS, step)
        elif row == "bar_width":
            s["bar_width"] = min(20, max(4, s["bar_width"] + step))
        elif row == "emoji":
            s["emoji"] = not s["emoji"]
        elif pick and row.startswith("color:"):
            role = row[6:]
            old = s["colors"][role]
            grid = SWATCHES if old in SWATCHES else [old] + SWATCHES
            s["palette"] = {"role": role, "grid": grid, "index": grid.index(old), "old": old, "typing": None, "error": ""}
        elif pick and row.startswith("seg:"):
            _toggle(s, row[4:])
        elif pick and row in ("save", "cancel"):
            return s, row
    return s, None


def _palette_key(s: dict, key: str) -> tuple:
    p, role = s["palette"], s["palette"]["role"]
    if p["typing"] is not None:  # typing a hex colour
        if key == "esc":
            p["typing"], p["error"] = None, ""
        elif key == "enter":
            clean, error = ui.validate({"colors": {role: p["typing"]}})
            if error:
                p["error"] = "That is not a colour. Use 6 hex digits, like #ff9900."
            else:
                s["colors"][role], s["palette"] = clean["colors"][role], None
        elif key == "backspace":
            p["typing"], p["error"] = p["typing"][:-1], ""
        elif len(key) == 1 and (key in string.hexdigits or (key == "#" and not p["typing"])) and len(p["typing"]) < 7:
            p["typing"], p["error"] = p["typing"] + key, ""
        return s, None
    n = len(p["grid"])
    if key in ("left", "right", "up", "down"):
        i = p["index"] + {"left": -1, "right": 1, "up": -GRID, "down": GRID}[key]
        if 0 <= i < n:
            p["index"] = i
            s["colors"][role] = p["grid"][i]  # the preview follows
    elif key == "enter":
        s["palette"] = None
    elif key == "esc":
        s["colors"][role], s["palette"] = p["old"], None
    elif key in ("#", "h"):
        p["typing"] = "#"
    return s, None


# Screen -------------------------------------------------------------------------------------------

def _bold(text: str) -> str:
    return text if os.environ.get("NO_COLOR") else f"\033[1m{text}\033[0m"


def _swatch(color: str, state: dict) -> str:
    return ui.paint("██", "ok", {**state, "colors": {"ok": color}})


def _row(state: dict, name: str) -> tuple:
    """(label, value) of one row."""
    kind, _, arg = name.partition(":")
    if kind == "color":
        return arg, f"{ui.paint('██', arg, state)} {state['colors'][arg]}"
    if kind == "seg":
        return f"  [{'x' if any(arg in line for line in state['segments']) else ' '}] {arg}", HELP[arg]
    if name == "save" or name == "cancel":
        return f"[ {name.capitalize()} ]", ""
    value = {"preset": state["preset"], "bar": state["bar"], "bar_width": state["bar_width"],
             "emoji": "on" if state["emoji"] else "off"}[name]
    return {"preset": "Preset", "bar": "Bar style", "bar_width": "Bar width", "emoji": "Emoji"}[name], f"‹ {value} ›"


def render(state: dict, columns: int) -> str:
    dim = lambda text: ui.paint(text, "dim", state)
    pal = state["palette"]
    lines = [_bold("session-guard theme" + (f"  ›  {pal['role']} colour" if pal else "")), ""]
    lines += ["  " + line for line in preview_lines(state, columns=columns - 2)]
    lines.append("")
    if pal:
        for start in range(0, len(pal["grid"]), GRID):
            lines.append("  " + "".join(("[%s]" if i == pal["index"] else " %s ") % _swatch(c, state)
                                        for i, c in enumerate(pal["grid"][start:start + GRID], start)))
        lines += ["", f"  {state['colors'][pal['role']]}   (was {pal['old']})"]
        if pal["typing"] is not None:
            lines.append(f"  Type a hex colour: {pal['typing']}▏")
            lines.append("  " + (pal["error"] or dim("Enter confirms, Esc goes back")))
            lines.append("")
            lines.append("  " + dim("0-9 a-f type  Backspace delete"))
        else:
            lines += ["", "  " + dim("←→↑↓ choose  Enter pick  Esc undo  # hex")]
        return "\n".join(lines)
    for i, name in enumerate(ROWS):
        if name == "seg:" + ui.SEGMENTS[0]:
            lines.append("  Show")
        label, value = _row(state, name)
        label = label.ljust(12) if value else label
        lines.append("› " + _bold(label) + value if i == state["row"] else "  " + label + value)
    lines += ["", "  " + dim("↑↓ move  ←→ change  Enter/Space pick  s save  q cancel")]
    return "\n".join(lines)


# Terminal -----------------------------------------------------------------------------------------

ARROWS = {"A": "up", "B": "down", "C": "right", "D": "left"}


def parse_keys(data: bytes) -> list:
    """Key names from what the terminal sent. A lone Esc is "esc"; arrow sequences are \\x1b[A or \\x1bOA."""
    text, keys, i = data.decode("utf-8", "ignore"), [], 0
    while i < len(text):
        c = text[i]
        if c == "\x1b":
            m = re.match(r"\x1b[\[O][0-9;]*([A-Za-z~])", text[i:])
            nxt = text[i + 1:i + 2]
            if m:
                if m.group(1) in ARROWS:
                    keys.append(ARROWS[m.group(1)])
                i += m.end()
            elif nxt and nxt in "[O":  # a key we do not use, or cut off
                break
            else:
                keys.append("esc")
                i += 1
            continue
        keys.append({"\r": "enter", "\n": "enter", "\x7f": "backspace", "\x08": "backspace", "\x03": "ctrl-c"}.get(c, c))
        i += 1
    return keys


def _read(fd: int) -> list:
    """Wait up to 0.25s for keys; [] if none came."""
    if not select.select([fd], [], [], 0.25)[0]:
        return []
    data = os.read(fd, 64)
    if not data:
        return ["ctrl-c"]  # input closed
    if data.endswith(b"\x1b") and select.select([fd], [], [], 0.05)[0]:  # else it was a lone Esc
        data += os.read(fd, 64)  # the rest of an arrow key
    return parse_keys(data)


def run(raw: dict) -> dict | None:
    """Show the picker. The theme to save, or None if cancelled. Always puts the terminal back."""
    fd, out = sys.stdin.fileno(), sys.stdout
    old = termios.tcgetattr(fd)
    state, action, size, keys = initial_state(raw), None, None, [None]
    out.write("\033[?1049h\033[?25l")  # own screen, no cursor
    try:
        tty.setcbreak(fd)  # keys arrive at once; Ctrl-C still works
        while action is None:
            now = shutil.get_terminal_size()  # read again each time: the window may have been resized
            if keys or now != size:
                text = render(state, now.columns)
                if text.count("\n") >= now.lines:  # short window: drop the blank lines
                    text = text.replace("\n\n", "\n")
                out.write("\033[H" + text.replace("\n", "\033[K\n") + "\033[K\033[J")
                out.flush()
            size = now
            keys = _read(fd)
            for key in keys:
                state, action = handle_key(state, key)
                if action:
                    break
    except KeyboardInterrupt:
        action = "cancel"
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        out.write("\033[?25h\033[?1049l")
        out.flush()
    return to_theme(state) if action == "save" else None
