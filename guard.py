#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: warn when a session gets expensive.

Every message re-sends the whole session, so cost per message grows with
context size. This hook reads the session transcript and:
  - warns once each time context crosses a level (see LEVELS)
  - optionally asks Jev whether the new prompt starts a different task;
    if so, blocks it once and suggests /handover + a new session.

Stdlib only. Fails open: any error -> exit 0, the prompt goes through.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

# Context sizes (tokens) that trigger a warning, once each per session.
LEVELS = [150_000, 300_000, 500_000]
# Jev new-task check only runs above this context size (below it a stray task is cheap).
JEV_MIN_CONTEXT = 80_000
# A wrong block costs the user a retype; a missed one costs a few cents. Err towards letting through.
JEV_THRESHOLD = 0.8
JEV_URL = "https://api.typesafe.ai/v1/systemone"
STATE_DIR = Path.home() / ".claude" / "session-guard"

# USD per 1M tokens: (input, cache read, cache write, output).
# GitHub Copilot AI-credit rates, checked 2026-09-27 against
# https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing
# Models with no cache-write price there are billed cache writes at the input price.
# Keys are model names lowercased with spaces and dots as "-" ("GPT-5.6 Sol" -> "gpt-5-6-sol").
PRICES = {
    # Anthropic
    "haiku-4-5": (1.00, 0.10, 1.25, 5.00),
    "sonnet-4": (3.00, 0.30, 3.75, 15.00),
    "sonnet-4-6": (3.00, 0.30, 3.75, 15.00),
    "sonnet-5": (2.00, 0.20, 2.50, 10.00),
    "opus-4-7": (5.00, 0.50, 6.25, 25.00),
    "opus-4-8": (5.00, 0.50, 6.25, 25.00),
    "opus-4-8-fast": (10.00, 1.00, 12.50, 50.00),
    "opus-5": (5.00, 0.50, 6.25, 25.00),
    "opus-5-5": (4.00, 0.20, 5.00, 20.00),
    "fable-5": (10.00, 1.00, 12.50, 50.00),
    "fable-5-1": (10.00, 0.25, 12.50, 50.00),
    # OpenAI
    "gpt-5-mini": (0.25, 0.025, 0.25, 2.00),
    "gpt-5-3-codex": (1.75, 0.175, 1.75, 14.00),
    "gpt-5-4": (2.50, 0.25, 2.50, 15.00),
    "gpt-5-4-mini": (0.75, 0.075, 0.75, 4.50),
    "gpt-5-4-nano": (0.20, 0.02, 0.20, 1.25),
    "gpt-5-5": (5.00, 0.50, 5.00, 30.00),
    "gpt-5-6-luna": (0.20, 0.02, 0.25, 1.20),
    "gpt-5-6-sol": (4.00, 0.40, 5.00, 20.00),
    "gpt-5-6-terra": (2.00, 0.20, 2.50, 12.00),
    "gpt-6-astra": (10.00, 1.00, 12.50, 50.00),
    "gpt-6-luna": (0.10, 0.01, 0.125, 0.50),
    "gpt-6-sol": (2.00, 0.20, 2.50, 10.00),
    # Google (3.6-3.8 Flash: promo price until 2026-12-31)
    "gemini-3-5-flash": (1.50, 0.15, 1.50, 9.00),
    "gemini-3-6-flash": (0.75, 0.075, 0.75, 3.75),
    "gemini-3-7-flash": (0.75, 0.075, 0.75, 3.75),
    "gemini-3-8-flash": (0.75, 0.075, 0.75, 3.75),
    # xAI
    "grok-4-5": (2.00, 0.50, 2.00, 6.00),
    "grok-4-6": (2.00, 0.50, 2.00, 6.00),
    "grok-4-7": (2.00, 0.50, 2.00, 6.00),
    # Microsoft, Moonshot
    "mai-code-1-1-flash": (0.20, 0.02, 0.20, 1.20),
    "kimi-k2-7-code": (0.95, 0.19, 0.95, 4.00),
    "kimi-k3": (3.00, 0.30, 3.00, 15.00),
}
# Long-context tier: above this many context tokens the whole call is billed at the second price.
LONG_CONTEXT = {
    "gpt-5-4": (272_000, (5.00, 0.50, 5.00, 22.50)),
    "gpt-5-5": (272_000, (10.00, 1.00, 10.00, 45.00)),
    "gpt-5-6-luna": (200_000, (0.40, 0.04, 0.50, 1.80)),
    "gpt-5-6-sol": (272_000, (8.00, 0.80, 10.00, 30.00)),
    "gpt-5-6-terra": (272_000, (4.00, 0.40, 5.00, 18.00)),
    "gpt-6-astra": (272_000, (20.00, 2.00, 25.00, 75.00)),
    "gpt-6-luna": (272_000, (0.20, 0.02, 0.25, 0.75)),
    "gpt-6-sol": (272_000, (4.00, 0.40, 5.00, 15.00)),
    "grok-4-5": (200_000, (4.00, 1.00, 4.00, 12.00)),
    "grok-4-6": (200_000, (4.00, 1.00, 4.00, 12.00)),
    "grok-4-7": (200_000, (4.00, 1.00, 4.00, 12.00)),
}
DEFAULT_PRICE = PRICES["opus-5"]  # unknown model: assume expensive rather than cheap


def price_for(model: str, context: int = 0) -> tuple:
    m = (model or "").lower().replace(".", "-").replace(" ", "-")
    # Longest key first so "gpt-5-4-mini" wins over "gpt-5-4".
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            tier = LONG_CONTEXT.get(key)
            return tier[1] if tier and context > tier[0] else PRICES[key]
    return DEFAULT_PRICE


def prompt_text(entry: dict) -> str | None:
    """Return the text of a real user prompt, or None for tool results / meta lines."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(content, str) or not content.strip() or content.lstrip().startswith("<"):
        return None
    return content.strip()


def read_session(transcript: Path) -> dict:
    """Context size of the latest call, cost so far, cost of the last message, user prompts."""
    seen = set()
    total = last_msg = 0.0
    context = 0
    prompts = []
    for line in transcript.open(errors="ignore"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        text = prompt_text(e)
        if text:
            prompts.append(text)
            last_msg = 0.0
            continue
        msg = e.get("message") or {}
        usage = msg.get("usage") if isinstance(msg, dict) else None
        if not usage or e.get("isSidechain"):
            continue
        # One API response is split over several lines that repeat the same usage.
        key = msg.get("id") or e.get("requestId") or e.get("uuid")
        if key in seen:
            continue
        seen.add(key)
        tin, tread, twrite, tout = (usage.get(k) or 0 for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        context = tin + tread + twrite
        p = price_for(msg.get("model"), context)
        cost = (tin * p[0] + tread * p[1] + twrite * p[2] + tout * p[3]) / 1e6
        total += cost
        last_msg += cost
    return {"context": context, "total": total, "last_msg": last_msg, "prompts": prompts}


def ask_jev(prompts: list[str], new_prompt: str, api_key: str) -> float | None:
    """Probability (0-1) that new_prompt starts a different task. None on any failure."""
    # Recent requests only: "new task" means different from what you were just doing.
    # Including the session's first prompt pushed follow-ups in drifted sessions to ~0.7.
    earlier = prompts[-5:]
    state = "Earlier requests in this coding session:\n" + "\n".join(f"- {p[:300]}" for p in earlier)
    state += f"\n\nNew request:\n{new_prompt[:600]}"
    body = {
        "model": "jev-latest",
        "state": state,
        "questions": {
            "new_task": {
                "type": "noul",
                "instructions": "Is the new request a different, unrelated task from the earlier "
                "requests, rather than a continuation or follow-up of the same work?",
            }
        },
    }
    req = urllib.request.Request(
        JEV_URL,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return float(json.load(r)["answers"]["new_task"]["noul"])
    except Exception:
        return None


def decide(hook: dict, session: dict, state: dict, jev=ask_jev, events: list | None = None) -> dict | None:
    """Return the hook's JSON output, or None to stay quiet. Mutates state; appends to events."""
    events = [] if events is None else events
    ctx, prompt = session["context"], hook.get("prompt", "")
    cost = f"~${session['total']:.2f} so far, last message ~${session['last_msg']:.2f}"

    # Jev: block a new task once. Sending the same prompt again lets it through.
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and os.environ.get("SESSION_GUARD_JEV") == "1" and ctx >= JEV_MIN_CONTEXT and session["prompts"]:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        if state.get("blocked") == digest:
            events.append({"event": "override", "ctx": ctx})
        else:
            p = jev(session["prompts"], prompt, key)
            if p is not None:
                state["jev"] = p  # shown by statusline.py
                events.append({"event": "jev", "p": round(p, 3), "ctx": ctx})
            if p is not None and p >= JEV_THRESHOLD:
                state["blocked"] = digest
                events.append({"event": "block", "p": round(p, 3), "ctx": ctx})
                return {
                    "decision": "block",
                    "reason": f"💸 This looks like a NEW task ({p:.0%} sure), but this session already has "
                    f"{ctx / 1000:.0f}k tokens of context ({cost}). Every message re-sends all of it.\n"
                    "Cheaper: /handover, then /clear and /continue.\n"
                    "To send it here anyway, press ↑ and submit the same prompt again.",
                }

    # After /compact the context shrinks; forget warnings above the new size so they can fire again.
    state["warned"] = min(state.get("warned", 0), max([lvl for lvl in LEVELS if lvl <= ctx], default=0))
    crossed = [lvl for lvl in LEVELS if ctx >= lvl and lvl > state["warned"]]
    if crossed:
        state["warned"] = crossed[-1]
        events.append({"event": "warn", "level": crossed[-1], "ctx": ctx})
        return {
            "systemMessage": f"💸 Session context is {ctx / 1000:.0f}k tokens ({cost}). "
            "Every message re-sends all of it. When you finish this step: /handover, then /clear and /continue."
        }
    return None


def load_env(path: Path) -> None:
    """Read KEY=value lines from a .env next to this script. Real env vars win."""
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> None:
    try:
        load_env(Path(__file__).resolve().parent / ".env")
        if os.environ.get("SESSION_GUARD_OFF") == "1":  # control weeks for before/after comparisons
            return
        hook = json.load(sys.stdin)
        transcript = Path(hook["transcript_path"])
        if not transcript.exists():
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file = STATE_DIR / f"{hook['session_id']}.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        events = []
        out = decide(hook, read_session(transcript), state, events=events)
        state_file.write_text(json.dumps(state))
        # Numbers only, never prompt text: read by report.py for before/after comparisons.
        with (STATE_DIR / "events.jsonl").open("a") as log:
            for e in events:
                log.write(json.dumps({"ts": time.time(), "session": hook["session_id"], **e}) + "\n")
        if out:
            print(json.dumps(out))
    except Exception:
        pass  # never break the user's session


if __name__ == "__main__":
    main()
