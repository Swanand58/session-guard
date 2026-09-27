"""Run: python3 test_guard.py"""

import json
import os
import tempfile
from pathlib import Path

import guard


def transcript(lines: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    f.write("\n".join(json.dumps(x) for x in lines))
    f.close()
    return Path(f.name)


def call(msg_id: str, cache_read: int, model: str = "claude-sonnet-5") -> dict:
    return {"type": "assistant", "message": {"id": msg_id, "model": model, "usage": {
        "input_tokens": 0, "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": 0, "output_tokens": 0}}}


def prompt(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


# Reading: duplicate lines counted once, tool results / meta lines aren't prompts.
t = transcript([
    prompt("fix the login bug"),
    call("a", 100_000), call("a", 100_000),  # same response split over two lines
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
    prompt("<command-name>/model</command-name>"),
    prompt("now also fix logout"),
    call("b", 200_000, "claude-opus-5.5"),
])
s = guard.read_session(t)
assert s["prompts"] == ["fix the login bug", "now also fix logout"], s["prompts"]
assert s["context"] == 200_000
assert abs(s["total"] - (0.02 + 0.04)) < 1e-9, s["total"]  # sonnet-5 100k@0.20 + opus-5.5 200k@0.20
assert abs(s["last_msg"] - 0.04) < 1e-9
assert guard.price_for("claude-opus-5-5-20260901") == guard.PRICES["opus-5-5"]
assert guard.price_for("some-new-model") == guard.DEFAULT_PRICE

# Levels: warn once per level, re-arm after compaction.
state = {}
sess = lambda ctx: {"context": ctx, "total": 1.0, "last_msg": 0.1, "prompts": ["x"]}
assert guard.decide({}, sess(100_000), state) is None
assert "systemMessage" in guard.decide({}, sess(160_000), state)
assert guard.decide({}, sess(170_000), state) is None  # same level: quiet
assert "systemMessage" in guard.decide({}, sess(310_000), state)
assert guard.decide({}, sess(40_000), state) is None  # after /compact
assert "systemMessage" in guard.decide({}, sess(160_000), state)  # warns again

# Jev: block a new task once, let the same prompt through the second time.
os.environ["TYPESAFE_API_KEY"], os.environ["SESSION_GUARD_JEV"] = "k", "1"
state = {"warned": 500_000}
new_task = lambda *a: 0.9
same_task = lambda *a: 0.1
hook = {"prompt": "add dark mode"}
assert guard.decide(hook, sess(90_000), state, jev=new_task)["decision"] == "block"
assert guard.decide(hook, sess(90_000), state, jev=new_task) is None  # resubmitted: allowed
assert guard.decide({"prompt": "continue"}, sess(90_000), state, jev=same_task) is None
assert guard.decide({"prompt": "x"}, sess(50_000), {}, jev=new_task) is None  # small session: no check
assert guard.decide({"prompt": "y"}, sess(90_000), {"warned": 500_000}, jev=lambda *a: None) is None  # Jev down
os.environ["SESSION_GUARD_JEV"] = "0"
assert guard.decide({"prompt": "z"}, sess(90_000), {"warned": 500_000}, jev=new_task) is None  # Jev off

# Jev score is saved for the status line.
os.environ["SESSION_GUARD_JEV"] = "1"
state = {"warned": 500_000}
guard.decide({"prompt": "q"}, sess(90_000), state, jev=same_task)
assert state["jev"] == 0.1

# Status line.
import statusline  # noqa: E402

assert statusline.render(sess(0), {}) == ""  # nothing before the first reply
line = statusline.render(sess(172_000), {"jev": 0.1})
assert "172k ctx" in line and "same task" in line and "/handover soon" in line, line
line = statusline.render(sess(320_000), {"jev": 0.93})
assert "new task? 93%" in line and "/handover now" in line, line
assert "/handover" not in statusline.render(sess(40_000), {})

print("all good")
