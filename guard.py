#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: warn when a session gets expensive.

Every message re-sends the whole session, so cost per message grows with
context size. This hook reads the session transcript and:
  - warns once each time context crosses a level (see LEVELS)
  - optionally asks Jev whether the new prompt starts a different task;
    if so, blocks it once and suggests /handover + a new session.

Stdlib only. Fails open: any error -> exit 0, the prompt goes through.
"""

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

# Context sizes (tokens) that trigger a warning, once each per session.
LEVELS = [150_000, 300_000, 500_000]
# Jev new-task check only runs above this context size (below it a stray task is cheap).
JEV_MIN_CONTEXT = 80_000
JEV_THRESHOLD = 0.7
JEV_URL = "https://api.typesafe.ai/v1/systemone"
STATE_DIR = Path.home() / ".claude" / "session-guard"

# USD per 1M tokens: (input, cache read, cache write, output).
# GitHub Copilot AI-credit rates, Sep 2026. Verify against
# https://docs.github.com/en/copilot/reference/copilot-billing/models-and-pricing
PRICES = {
    "haiku-4-5": (1.00, 0.10, 1.25, 5.00),
    "sonnet-5": (2.00, 0.20, 2.50, 10.00),
    "sonnet-4": (3.00, 0.30, 3.75, 15.00),
    "opus-5-5": (4.00, 0.20, 5.00, 20.00),
    "opus-5": (5.00, 0.50, 6.25, 25.00),
    "opus-4": (5.00, 0.50, 6.25, 25.00),
}
DEFAULT_PRICE = PRICES["opus-5"]  # unknown model: assume expensive rather than cheap


def price_for(model: str) -> tuple:
    m = (model or "").lower().replace(".", "-")
    # Longest key first so "opus-5-5" wins over "opus-5".
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            return PRICES[key]
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
        p = price_for(msg.get("model"))
        cost = (tin * p[0] + tread * p[1] + twrite * p[2] + tout * p[3]) / 1e6
        total += cost
        last_msg += cost
        context = tin + tread + twrite
    return {"context": context, "total": total, "last_msg": last_msg, "prompts": prompts}


def ask_jev(prompts: list[str], new_prompt: str, api_key: str) -> float | None:
    """Probability (0-1) that new_prompt starts a different task. None on any failure."""
    earlier = prompts[:1] + prompts[-3:] if len(prompts) > 4 else prompts
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


def decide(hook: dict, session: dict, state: dict, jev=ask_jev) -> dict | None:
    """Return the hook's JSON output, or None to stay quiet. Mutates state."""
    ctx, prompt = session["context"], hook.get("prompt", "")
    cost = f"~${session['total']:.2f} so far, last message ~${session['last_msg']:.2f}"

    # Jev: block a new task once. Sending the same prompt again lets it through.
    key = os.environ.get("TYPESAFE_API_KEY")
    if key and os.environ.get("SESSION_GUARD_JEV") == "1" and ctx >= JEV_MIN_CONTEXT and session["prompts"]:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        if state.get("blocked") != digest:
            p = jev(session["prompts"], prompt, key)
            if p is not None and p >= JEV_THRESHOLD:
                state["blocked"] = digest
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
        hook = json.load(sys.stdin)
        transcript = Path(hook["transcript_path"])
        if not transcript.exists():
            return
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_file = STATE_DIR / f"{hook['session_id']}.json"
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
        out = decide(hook, read_session(transcript), state)
        state_file.write_text(json.dumps(state))
        if out:
            print(json.dumps(out))
    except Exception:
        pass  # never break the user's session


if __name__ == "__main__":
    main()
