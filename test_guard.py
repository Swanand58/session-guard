"""Run: python3 test_guard.py"""

from __future__ import annotations  # Python 3.9 (macOS default) support

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

# Prices: every model resolves to its own row (no shorter key shadows it), names are normalized,
# long-context tiers apply above the threshold.
for k in guard.PRICES:
    assert guard.price_for(k) == guard.PRICES[k], k
assert guard.price_for("GPT-5.6 Sol") == guard.PRICES["gpt-5-6-sol"]
assert guard.price_for("gpt-5.4-mini") == guard.PRICES["gpt-5-4-mini"]
assert guard.price_for("grok-4.7", 150_000) == guard.PRICES["grok-4-7"]
assert guard.price_for("grok-4.7", 250_000) == guard.LONG_CONTEXT["grok-4-7"][1]
assert guard.price_for("claude-opus-4-8-20260101") == guard.PRICES["opus-4-8"]

# Event log: numbers only.
events = []
guard.decide({"prompt": "new"}, sess(90_000), {"warned": 500_000}, jev=new_task, events=events)
assert [e["event"] for e in events] == ["jev", "block"], events
st = {"warned": 500_000}
guard.decide({"prompt": "dup"}, sess(90_000), st, jev=new_task)
events = []
guard.decide({"prompt": "dup"}, sess(90_000), st, jev=new_task, events=events)
assert [e["event"] for e in events] == ["override"], events
events = []
guard.decide({}, sess(160_000), {}, events=events)
assert events == [{"event": "warn", "level": 150_000, "ctx": 160_000}], events
assert all("prompt" not in e for e in events)

# Report: per-session stats inside the period only; subagent files count toward their parent session.
import report  # noqa: E402
from datetime import date  # noqa: E402

root = Path(tempfile.mkdtemp())
(root / "proj" / "s1" / "subagents").mkdir(parents=True)
day_in, day_out = "2026-09-10T10:00:00Z", "2026-08-01T10:00:00Z"
rows = [
    {**prompt("fix bug"), "timestamp": day_in},
    {**call("a", 100_000), "timestamp": day_in},
    {**call("b", 400_000), "timestamp": day_in},
    {"type": "user", "message": {"content": "<command-name>/handover</command-name>"}, "timestamp": day_in},
    {**prompt("old work"), "timestamp": day_out},
    {**call("c", 900_000), "timestamp": day_out},
    {"type": "summary", "summary": "no timestamp on this line"},
]
(root / "proj" / "s1.jsonl").write_text("\n".join(json.dumps(x) for x in rows))
(root / "proj" / "s1" / "subagents" / "agent-1.jsonl").write_text(json.dumps({**call("d", 50_000), "timestamp": day_in}))
(root / "proj" / "s2.jsonl").write_text("\n".join(json.dumps(x) for x in [
    {**prompt("other"), "timestamp": day_in}, {**call("e", 50_000), "timestamp": day_in}]))
ev = root / "events.jsonl"
ev.write_text(json.dumps({"ts": 1789000000, "event": "warn"}) + "\n")  # 2026-09-10

no_copilot = Path(tempfile.mkdtemp())
r = report.analyse(date(2026, 9, 1), date(2026, 9, 30), projects=root, events_file=ev, copilot=no_copilot)
assert r["sessions"] == 2 and r["messages"] == 2 and r["handovers"] == 1, r
assert abs(r["cost"] - 0.12) < 1e-9, r  # sonnet-5 cache reads @ $0.20/M: s1 100k+400k+50k, s2 50k
assert r["cost_per_session"] == 0.06 and r["biggest_session_cost"] == 0.11, r
assert r["tokens_per_session"] == 300_000, r  # (550k + 50k) / 2
assert r["sessions_over_300k"] == 1 and r["guard_warnings"] == 1, r

# Copilot CLI: billed credits when present, else priced; crashed sessions counted separately.
cp = Path(tempfile.mkdtemp())
(cp / "abc").mkdir()
metrics = {
    "claude-sonnet-5": {"usage": {"inputTokens": 1_000_000, "cacheReadTokens": 800_000, "outputTokens": 10_000},
                        "totalNanoAiu": 50_000_000_000},  # billed: 50 credits = $0.50
    "gpt-6-sol": {"usage": {"inputTokens": 100_000}},  # no billing info: 100k fresh input @ $2/M = $0.20
}
(cp / "abc" / "events.jsonl").write_text("\n".join(json.dumps(x) for x in [
    {"type": "session.start", "timestamp": day_in, "data": {"sessionId": "abc"}},
    {"type": "user.message", "timestamp": day_in, "data": {"content": "hi"}},
    {"type": "user.message", "timestamp": day_in, "data": {"content": "more"}},
    {"type": "session.shutdown", "timestamp": day_in, "data": {"modelMetrics": metrics}},
    {"type": "session.shutdown", "timestamp": day_out, "data": {"modelMetrics": metrics}},  # outside period
]))
(cp / "old.jsonl").write_text(json.dumps({"type": "user.message", "timestamp": day_in, "data": {}}))
r = report.analyse(date(2026, 9, 1), date(2026, 9, 30), projects=root, events_file=ev, copilot=cp)
assert r["sessions"] == 3 and r["sessions_copilot_cli"] == 1 and r["sessions_claude_code"] == 2, r
assert r["copilot_sessions_without_usage"] == 1, r
assert abs(r["cost"] - (0.12 + 0.50 + 0.20)) < 1e-9, r
assert r["messages"] == 2 + 2, r  # the crashed session's message has no known cost, so it is left out
assert r["sessions_over_300k"] == 1 and r["share_cost_over_300k"] == round(0.08 / 0.12, 3), r  # Claude Code only

# Installer, against a temporary HOME: merges, keeps the old status line, safe twice, uninstall restores.
import subprocess  # noqa: E402
import sys  # noqa: E402

home = Path(tempfile.mkdtemp())
(home / ".claude").mkdir()
original = {"model": "opus", "statusLine": {"type": "command", "command": "~/my line.sh", "padding": 0},
            "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "mine.sh"}]}]}}
(home / ".claude" / "settings.json").write_text(json.dumps(original))
run = lambda *a: subprocess.run([sys.executable, "install.py", *a], env={**os.environ, "HOME": str(home)},
                                capture_output=True, text=True, check=True).stdout
run(); run()
s = json.loads((home / ".claude" / "settings.json").read_text())
cmds = [h["command"] for g in s["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
assert cmds[0] == "mine.sh" and len(cmds) == 2 and cmds[1].endswith("guard.py"), cmds
assert s["model"] == "opus" and s["statusLine"]["padding"] == 0, s
assert s["statusLine"]["command"].endswith("statusline.py sh -c '~/my line.sh'"), s["statusLine"]
assert (home / ".claude" / "commands" / "handover.md").exists()
assert len(list((home / ".claude").glob("settings.json.*.bak"))) == 1  # second run changed nothing
out = run("uninstall")
assert json.loads((home / ".claude" / "settings.json").read_text()) == original, out
assert not (home / ".claude" / "commands" / "handover.md").exists()
home = Path(tempfile.mkdtemp())  # fresh Mac: no ~/.claude yet
out = run()
assert "Jev new-task check is OFF" in out, out
assert json.loads((home / ".claude" / "settings.json").read_text())["statusLine"]["command"].endswith("statusline.py")
run("uninstall")
assert json.loads((home / ".claude" / "settings.json").read_text()) == {}

print("all good")
