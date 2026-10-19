#!/usr/bin/env python3
"""Claude Code status line: live session cost meter.

    💸 172k ctx · ▰▰▰▰▰▰▰▰▱▱ 86% · ~4 msgs to 180k · $4.61 (agents $0.67) · last msg $0.28 · 🧭 same task · /handover now

Colours and layout come from the theme: session-guard theme.
`ctx` links to the session page (session-guard view) in terminals that support it.

Any arguments are run as another status line command first (its output is
kept above ours), so an existing status line keeps working:

    python3 statusline.py bash /path/to/other-statusline.sh
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import math
import os
import subprocess
import sys
from itertools import groupby
from pathlib import Path

import guard
import ui


def eta(session: dict, ctx: int, lvls: list) -> str:
    """Text like ~12 msgs to 160k: tokens to the next level / average growth per prompt. Empty if unknown."""
    pc = session.get("prompt_ctx") or []
    growth = [b - a for a, b in zip(pc, pc[1:]) if b >= a]  # a drop is /compact: not growth
    nxt = [lvl for lvl in lvls if lvl > ctx]
    if not growth or not nxt or sum(growth) <= 0:
        return ""
    n = math.ceil((nxt[0] - ctx) / (sum(growth) / len(growth)))
    return f"~{n if n <= 99 else '99+'} {'msg' if n == 1 else 'msgs'} to {nxt[0] // 1000}k"


def top_grab(session: dict) -> str:
    """Text like 'top: Read report.py +38k': the biggest thing in the context. Empty if unknown."""
    if "grab" in session:  # given (the theme picker's fake sessions)
        return session["grab"]
    try:
        import grabs  # here, so a missing file only hides this segment
        top = grabs.tool_items(grabs.grabs(session["transcript"]))[0]
        return f"top: {top['label']} +{top['tokens'] / 1000:.0f}k"
    except Exception:
        return ""


def page_link(transcript: Path, session: dict) -> str | None:
    """URL of the session page (view.py), rewritten first if the transcript or the theme is newer. None on any failure."""
    try:
        import view  # here, so a missing file only drops the link
        page = view.page_path(transcript)
        newest = max(transcript.stat().st_mtime, ui.theme_file().stat().st_mtime if ui.theme_file().exists() else 0)
        if not page.exists() or page.stat().st_mtime < newest:
            view.write(transcript, session=session)
        return view.link(transcript)
    except Exception:
        return None


def render(session: dict, state: dict, theme: dict | None = None) -> str:
    ctx = session["context"]
    if not ctx:
        return ""
    theme = theme or ui.load()
    paint = lambda text, role: ui.paint(text, role, theme)
    emoji = lambda e: e + " " if theme["emoji"] else ""
    window = guard.window_for(session, state)
    lvls = guard.levels(window)
    reached = sum(ctx >= lvl for lvl in lvls)
    # Below level 1 ok, up to level 2 warn, beyond that danger.
    zone = lambda tokens: ("ok", "warn", "danger")[sum(tokens >= lvl for lvl in lvls[:2])]
    filled_char, empty_char = ui.BARS[theme["bar"]]
    width = theme["bar_width"]
    filled = min(width, math.ceil(ctx / window * width))
    cells = "".join(paint(filled_char * len(list(run)), z)  # one escape per run of same-zone cells
                    for z, run in groupby(zone(i / width * window) for i in range(filled)))
    ctx_text = paint(f"{emoji('💸')}{ctx / 1000:.0f}k ctx", zone(ctx))
    if session.get("link") and theme.get("link", True):  # OSC 8 hyperlink: not a colour, so NO_COLOR keeps it
        ctx_text = f"\033]8;;{session['link']}\a{ctx_text}\033]8;;\a"
    seg = {
        "ctx": ctx_text,
        "bar": cells + paint(empty_char * (width - filled), "dim") + " " + paint(f"{ctx / window:.0%}", zone(ctx)),
        "eta": eta(session, ctx, lvls),
        "cost": f"${session['total']:.2f}" + (f" (agents ${session['agents']:.2f})" if session.get("agents", 0) >= 0.005 else ""),
        "last": f"last msg ${session['last_msg']:.2f}",
        "jev": "",
        "grab": paint(top_grab(session), "dim") if reached and any("grab" in names for names in theme["segments"]) else "",
        "hint": paint("/handover soon", "warn") if reached == 1 else paint("/handover now", "danger") if reached > 1 else "",
    }
    jev = state.get("jev")
    if jev is not None:
        if jev >= guard.JEV_THRESHOLD:
            seg["jev"] = paint(f"{emoji('🧭')}new task? {jev:.0%}", "danger")
        elif jev >= 0.4:
            seg["jev"] = paint(f"{emoji('🧭')}unsure {jev:.0%}", "warn")
        else:
            seg["jev"] = paint(f"{emoji('🧭')}same task", "ok")
    sep = paint(" · ", "dim")
    lines = (sep.join(seg[name] for name in names if seg[name]) for names in theme["segments"])
    return "\n".join(line for line in lines if line)


def main() -> None:
    raw = sys.stdin.read()
    if len(sys.argv) > 1:
        try:
            other = subprocess.run(sys.argv[1:], input=raw, capture_output=True, text=True, timeout=2)
            if other.stdout.strip():
                print(other.stdout.rstrip("\n"))
        except Exception:
            pass
    try:
        guard.load_env(Path(guard.__file__).resolve().parent / ".env")
        if os.environ.get("SESSION_GUARD_OFF") == "1":
            return
        data = json.loads(raw)
        transcript = Path(data["transcript_path"])
        if not transcript.exists():
            return
        state_file = guard.STATE_DIR / f"{data['session_id']}.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        # Save the model's window size for guard.py (the hook input does not have it).
        window = (data.get("context_window") or {}).get("context_window_size")
        if isinstance(window, int) and window > 0 and state.get("window") != window:
            state["window"] = window
            try:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(json.dumps(state))
            except OSError:
                pass  # still draw the line
        theme = ui.load()
        session = {**guard.read_session(transcript), "transcript": transcript}
        if theme["link"] and session["context"]:
            session["link"] = page_link(transcript, session)
        line = render(session, state, theme)
        if line:
            print(line)
    except Exception:
        pass  # a broken status line must never break Claude Code


if __name__ == "__main__":
    main()
