#!/usr/bin/env python3
"""Claude Code status line: live session cost meter.

    💸 172k ctx ▰▰▱▱ · $4.61 (agents $0.67) · last msg $0.28 · 🧭 same task · /handover soon

Any arguments are run as another status line command first (its output is
kept above ours), so an existing status line keeps working:

    python3 statusline.py bash /path/to/other-statusline.sh
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import os
import subprocess
import sys
from pathlib import Path

import guard

GREEN, YELLOW, RED, DIM, RESET = "\033[38;5;108m", "\033[38;5;179m", "\033[38;5;167m", "\033[2m", "\033[0m"


def render(session: dict, state: dict) -> str:
    ctx = session["context"]
    if not ctx:
        return ""
    reached = sum(ctx >= lvl for lvl in guard.LEVELS)  # 0..3
    color = [GREEN, YELLOW, YELLOW, RED][reached]
    bar = "▰" * reached + "▱" * (len(guard.LEVELS) - reached)
    parts = [
        f"{color}💸 {ctx / 1000:.0f}k ctx {bar}{RESET}",
        f"${session['total']:.2f}" + (f" (agents ${session['agents']:.2f})" if session.get("agents", 0) >= 0.005 else ""),
        f"last msg ${session['last_msg']:.2f}",
    ]
    jev = state.get("jev")
    if jev is not None:
        if jev >= guard.JEV_THRESHOLD:
            parts.append(f"{RED}🧭 new task? {jev:.0%}{RESET}")
        elif jev >= 0.4:
            parts.append(f"{YELLOW}🧭 unsure {jev:.0%}{RESET}")
        else:
            parts.append(f"{GREEN}🧭 same task{RESET}")
    if reached == 1:
        parts.append(f"{YELLOW}/handover soon{RESET}")
    elif reached > 1:
        parts.append(f"{RED}/handover now{RESET}")
    return f" {DIM}·{RESET} ".join(parts)


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
        line = render(guard.read_session(transcript), state)
        if line:
            print(line)
    except Exception:
        pass  # a broken status line must never break Claude Code


if __name__ == "__main__":
    main()
