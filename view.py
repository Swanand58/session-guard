"""The session page: one local HTML file with the context chart, the biggest grabs, cost and a theme studio.

    build(transcript, theme=None)  -> dict   the page's data (ints, numbers, tool names and file base names only)
    render_html(data)              -> str    web/view.html with the data embedded
    write(transcript, theme=None)  -> Path   ~/.claude/session-guard/view/<session id>.html (atomic, owner-only)

Never prompt text, commands or file contents: the data comes from guard.read_session and grabs.grabs, which keep none.
The page makes no network requests. Stdlib only.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import os
import time
from pathlib import Path

import grabs
import guard
import ui

TEMPLATE = Path(__file__).resolve().parent / "web" / "view.html"
PLACEHOLDER = "{{SESSION_DATA}}"
CALLS = 200  # chart points; the status line's grab segment keeps 60


def page_path(transcript: Path) -> Path:
    return guard.STATE_DIR / "view" / f"{Path(transcript).stem}.html"


def build(transcript: Path, theme: dict | None = None, session: dict | None = None) -> dict:
    """JSON-safe data for the page. session: the result of guard.read_session, if the caller has it already."""
    transcript = Path(transcript)
    session = session or guard.read_session(transcript)
    try:
        state = json.loads((guard.STATE_DIR / f"{transcript.stem}.json").read_text())
    except (OSError, ValueError):
        state = {}
    state = state if isinstance(state, dict) else {}
    window = guard.window_for(session, state)
    ctx = session["context"]
    g = grabs.grabs(transcript, keep=CALLS)
    jev = state.get("jev")
    return {
        "session": transcript.stem,
        "window": window,
        "levels": guard.levels(window),
        "context": ctx,
        "percent": ctx / window,
        "cost": {"total": session["total"], "agents": session["agents"], "last_msg": session["last_msg"]},
        "prompt_ctx": session["prompt_ctx"],  # context at the end of each of the last prompts: for the eta text
        "jev": jev if isinstance(jev, (int, float)) and not isinstance(jev, bool) else None,
        "baseline": g["baseline"],
        "calls": g["calls"],
        "items": g["items"],
        "tool_items": grabs.tool_items(g),
        "theme": theme or ui.load(),
        # What the page's controls are built from, so they cannot drift from ui.py.
        "schema": {"roles": ui.ROLES, "presets": ui.PRESETS, "bars": ui.BARS, "bar_width": ui.BAR_WIDTH,
                   "segments": ui.SEGMENTS, "default_segments": ui.DEFAULT_SEGMENTS},
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def render_html(data: dict) -> str:
    """The template with the data in its <script type="application/json"> block. < > & and U+2028/9 are escaped."""
    payload = json.dumps(data, separators=(",", ":"))  # ASCII only, so U+2028/9 are already escaped
    for ch in "<>&":
        payload = payload.replace(ch, "\\u%04x" % ord(ch))  # only ever inside JSON strings (file names)
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, payload, 1)


def write(transcript: Path, theme: dict | None = None, session: dict | None = None) -> Path:
    """Write the page atomically (the browser or the status line never sees half a file). Owner-only."""
    path = page_path(transcript)
    html = render_html(build(transcript, theme, session))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path

