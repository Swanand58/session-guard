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
    prompt("[Request interrupted by user]"),
    {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}},
    prompt("now also fix logout"),
    call("b", 200_000, "claude-opus-5.5"),
])
s = guard.read_session(t)
assert s["prompts"] == ["fix the login bug", "now also fix logout"], s["prompts"]
assert s["context"] == 200_000
assert abs(s["total"] - (0.02 + 0.04)) < 1e-9, s["total"]  # sonnet-5 100k@0.20 + opus-5.5 200k@0.20
assert abs(s["last_msg"] - 0.04) < 1e-9
assert guard.price_for("claude-opus-5-5-20260901") == guard.PRICES["opus-5-5"]

# Subagents: their cost counts (total, agents, and last message if they ran after the last prompt),
# their context does not. Covers both the subagents/ folder and old inline isSidechain lines.
at = lambda row, ts: {**row, "timestamp": ts}
t = transcript([
    at(prompt("first"), "2026-09-10T10:00:00Z"),
    at(call("a", 100_000), "2026-09-10T10:00:05Z"),
    at(prompt("second"), "2026-09-10T11:00:00Z"),
    at(call("b", 120_000), "2026-09-10T11:00:05Z"),
    at({**call("side", 50_000), "isSidechain": True}, "2026-09-10T11:00:06Z"),
])
agents_dir = t.with_suffix("") / "subagents"
agents_dir.mkdir(parents=True)
(agents_dir / "agent-old.jsonl").write_text(json.dumps(at({**call("x", 300_000), "isSidechain": True}, "2026-09-10T10:30:00Z")))
(agents_dir / "agent-new.jsonl").write_text("\n".join(json.dumps(at({**call(i, c), "isSidechain": True}, "2026-09-10T11:30:00Z"))
                                                      for i, c in (("y", 100_000), ("z", 200_000))))
s = guard.read_session(t)
assert s["context"] == 120_000, s  # the main session's own context, not a subagent's
assert abs(s["agents"] - (0.01 + 0.06 + 0.06)) < 1e-9, s  # sonnet-5 cache reads: 50k + 300k + 300k
assert abs(s["total"] - (0.02 + 0.024 + 0.13)) < 1e-9, s
assert abs(s["last_msg"] - (0.024 + 0.01 + 0.06)) < 1e-9, s  # agent-old ran before the last prompt
a = guard.read_agent(agents_dir / "agent-new.jsonl")
assert a["context"] == 200_000 and abs(a["total"] - 0.06) < 1e-9, a

# Subagent guard: tell the user (not the subagent) once per level.
state, events = {}, []
assert guard.decide_agent({"context": 100_000, "total": 0.1}, state, events) is None and state == {}
out = guard.decide_agent({"context": 160_000, "total": 0.5}, state, events)
assert "160k" in out["systemMessage"], out
assert guard.decide_agent({"context": 170_000, "total": 0.6}, state, events) is None  # same level: quiet
assert events == [{"event": "agent_warn", "level": 160_000, "ctx": 160_000}], events  # 80% of a 200k window
assert "80% of 200k" in out["systemMessage"], out
assert "hookSpecificOutput" not in out, out  # nothing is sent to the subagent

# The hook itself, with a temporary HOME: quiet for the main session's tool calls, speaks inside a big subagent.
import subprocess  # noqa: E402
import sys  # noqa: E402

hook_home = Path(tempfile.mkdtemp())
def run_hook(**extra):
    payload = {"session_id": "s", "transcript_path": str(t), "hook_event_name": "PostToolUse", **extra}
    return subprocess.run([sys.executable, "guard.py"], input=json.dumps(payload), capture_output=True, text=True,
                          env={**os.environ, "HOME": str(hook_home)}).stdout
assert run_hook() == ""
assert "200k" in run_hook(agent_id="new") and run_hook(agent_id="new") == ""  # once
assert run_hook(agent_id="missing") == ""
log = (hook_home / ".claude" / "session-guard" / "events.jsonl").read_text()
assert log.count("agent_warn") == 1 and "prompt" not in log, log
assert guard.price_for("some-new-model") == guard.DEFAULT_PRICE

# Levels: warn once per level, re-arm after compaction.
state = {}
sess = lambda ctx: {"context": ctx, "total": 1.0, "last_msg": 0.1, "prompts": ["x"]}
assert guard.decide({}, sess(100_000), state) is None
assert "systemMessage" in guard.decide({}, sess(130_000), state)  # 60% of 200k
assert guard.decide({}, sess(140_000), state) is None  # same level: quiet
assert "systemMessage" in guard.decide({}, sess(170_000), state)  # 80%
assert guard.decide({}, sess(40_000), state) is None  # after /compact
assert "systemMessage" in guard.decide({}, sess(130_000), state)  # warns again

# Levels are shares of the window: 200k, 1M, SESSION_GUARD_LEVELS and SESSION_GUARD_WINDOW overrides, bad values.
for var in ("SESSION_GUARD_LEVELS", "SESSION_GUARD_WINDOW"):
    os.environ.pop(var, None)
assert guard.levels(200_000) == [120_000, 160_000, 180_000]
assert guard.levels(1_000_000) == [600_000, 800_000, 900_000]
os.environ["SESSION_GUARD_LEVELS"] = "0.9, 0.8,0.95"
assert guard.levels(200_000) == [160_000, 180_000, 190_000]
for bad in ("", "abc", "0.5,x", "0,0.5", "1.5", "-0.2,0.5", "nan", "0.5,,0.9"):
    os.environ["SESSION_GUARD_LEVELS"] = bad
    assert guard.levels(200_000) == [120_000, 160_000, 180_000], bad
del os.environ["SESSION_GUARD_LEVELS"]
# The window: env, then the size the status line saved, then proof from a call over 200k, then 200k.
assert guard.window_for({"context": 150_000}) == 200_000
assert guard.window_for({"context": 150_000, "peak": 150_000}) == 200_000
assert guard.window_for({"context": 150_000, "peak": 310_000}) == 1_000_000  # shrank after /compact
assert guard.window_for({"context": 150_000}, {"window": 1_000_000}) == 1_000_000
assert guard.window_for({"context": 350_000}, {"window": "junk"}) == 1_000_000
os.environ["SESSION_GUARD_WINDOW"] = "500000"
assert guard.window_for({"context": 150_000}, {"window": 1_000_000}) == 500_000
os.environ["SESSION_GUARD_WINDOW"] = "junk"
assert guard.window_for({"context": 150_000}, {"window": 1_000_000}) == 1_000_000
del os.environ["SESSION_GUARD_WINDOW"]
# On a 1M window 310k is quiet and the message says how full it is; 600k warns.
state = {}
assert guard.decide({}, sess(310_000), state) is None
out = guard.decide({}, sess(610_000), state)
assert "610k tokens (61% of 1000k" in out["systemMessage"], out
# A call over 200k flips the default window from 200k to 1M: 130k warned (65% of 200k), 250k does not.
state = {}
assert "systemMessage" in guard.decide({}, sess(130_000), state)
assert guard.decide({}, sess(250_000), state) is None
# A window saved by the status line wins over the default; compact reset works on the 1M levels.
state = {"window": 1_000_000}
assert guard.decide({}, sess(130_000), state) is None
assert "systemMessage" in guard.decide({}, sess(650_000), state) and state["warned"] == 600_000
assert guard.decide({}, {**sess(100_000), "peak": 650_000}, state) is None and state["warned"] == 0
assert "systemMessage" in guard.decide({}, sess(650_000), state)
# read_session reports the biggest main-session call.
t2 = transcript([prompt("x"), call("a", 310_000), call("b", 50_000)])
assert guard.read_session(t2)["peak"] == 310_000 and guard.read_session(t2)["context"] == 50_000

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

# Status line. Colours are forced on (and the theme is the default one, not the real file) so the checks are the same everywhere.
import re  # noqa: E402

import statusline  # noqa: E402
import ui  # noqa: E402

os.environ.pop("NO_COLOR", None)
os.environ["COLORTERM"] = "truecolor"
T = ui.effective({})
rd = lambda session, state, theme=T: statusline.render(session, state, theme)
plain = lambda text: re.sub(r"\033\[[0-9;]*m", "", text)

assert rd(sess(0), {}) == ""  # nothing before the first reply
line = rd(sess(150_000), {"jev": 0.1})
assert "150k ctx" in line and "same task" in line and "/handover soon" in line, line
line = rd(sess(190_000), {"jev": 0.93})
assert "new task? 93%" in line and "/handover now" in line, line
assert "/handover" not in rd(sess(40_000), {})
assert "agents" not in line and "$1.00 (agents $0.25)" in rd({**sess(150_000), "agents": 0.25}, {})
assert "/handover" not in rd(sess(310_000), {"window": 1_000_000})  # 31% of a 1M window: quiet
# Status line saves the window to the session's state file without dropping other keys.
sl_home = Path(tempfile.mkdtemp())
(sl_home / ".claude" / "session-guard").mkdir(parents=True)
(sl_home / ".claude" / "session-guard" / "s.json").write_text(json.dumps({"warned": 120_000, "jev": 0.3}))
subprocess.run([sys.executable, "statusline.py"], capture_output=True, text=True, env={**os.environ, "HOME": str(sl_home)},
               input=json.dumps({"session_id": "s", "transcript_path": str(t2), "context_window": {"context_window_size": 1_000_000}}))
saved = json.loads((sl_home / ".claude" / "session-guard" / "s.json").read_text())
assert saved == {"warned": 120_000, "jev": 0.3, "window": 1_000_000}, saved
# ...and guard.py keeps it when it rewrites the file.
subprocess.run([sys.executable, "guard.py"], capture_output=True, text=True, env={**os.environ, "HOME": str(sl_home)},
               input=json.dumps({"session_id": "s", "transcript_path": str(t2), "hook_event_name": "UserPromptSubmit", "prompt": "hi"}))
assert json.loads((sl_home / ".claude" / "session-guard" / "s.json").read_text())["window"] == 1_000_000

# read_session keeps its old keys and adds prompt_ctx (last 6 prompt ends).
t3 = transcript([prompt("a"), call("a", 100_000), call("b", 110_000), prompt("b"), call("c", 150_000),
                 {**call("side", 999_000), "isSidechain": True}, prompt("c"), call("d", 90_000)])
s3 = guard.read_session(t3)
assert s3["prompt_ctx"] == [110_000, 150_000, 90_000], s3  # subagent call left out
assert s3["context"] == 90_000 and s3["peak"] == 150_000 and len(s3["prompts"]) == 3
many = guard.read_session(transcript([x for i in range(10) for x in (prompt(f"p{i}"), call(f"m{i}", 1000 * (i + 1)))]))
assert many["prompt_ctx"] == [5000, 6000, 7000, 8000, 9000, 10000], many
many = guard.read_session(transcript([prompt("p")] + [call(f"h{i}", 1000 * (i + 1)) for i in range(50)]))
assert many["prompt_ctx"] == [50_000] and "history" not in many, many
assert guard.read_session(transcript([prompt("only")]))["prompt_ctx"] == []  # no reply yet

# Colours: hex -> xterm-256, truecolor escape, NO_COLOR, mode detection.
assert ui.to256("#87af87") == 108 and ui.to256("#000000") == 16 and ui.to256("#ffffff") == 231
assert ui.to256("#808080") == 244 and ui.to256("#d75f5f") == 167  # grey ramp, cube
assert ui.paint("x", "ok", T) == "\033[38;2;135;175;135mx\033[0m"
os.environ["COLORTERM"] = "24bit"
assert ui.paint("x", "ok", T).startswith("\033[38;2;")
for term in (None, "xterm"):  # auto without truecolor -> 256
    os.environ.pop("COLORTERM", None) if term is None else os.environ.update(COLORTERM=term)
    assert ui.paint("x", "ok", T) == "\033[38;5;108mx\033[0m", term
assert ui.paint("x", "ok", {**T, "mode": "truecolor"}).startswith("\033[38;2;")  # forced
os.environ["COLORTERM"] = "truecolor"
assert ui.paint("x", "ok", {**T, "mode": "256"}) == "\033[38;5;108mx\033[0m"
os.environ["NO_COLOR"] = "1"
assert ui.paint("x", "ok", T) == "x" and "\033" not in rd(sess(150_000), {"jev": 0.1})
os.environ["NO_COLOR"] = ""  # set but empty: colours stay
assert ui.paint("x", "ok", T).startswith("\033[")
del os.environ["NO_COLOR"]

# Theme file: defaults, unreadable file, bad parts fall back one by one, preset < explicit colours.
real_dir, guard.STATE_DIR = guard.STATE_DIR, Path(tempfile.mkdtemp())
try:
    assert ui.load() == ui.effective({}) and ui.load()["colors"] == ui.PRESETS["default"]
    for junk in ("not json", "[1, 2]", "", '{"colors": 5}'):
        ui.theme_file().write_text(junk)
        assert ui.load() == ui.effective({}), junk
    ui.theme_file().write_text(json.dumps({"preset": "ocean", "colors": {"warn": "112233", "danger": "nope", "x": "#000000"},
                                           "bar": "wide", "bar_width": 99, "emoji": "yes", "segments": [["cost", "bogus"]], "mode": "256"}))
    th = ui.load()
    assert th["preset"] == "ocean" and th["colors"]["ok"] == ui.PRESETS["ocean"]["ok"], th
    assert th["colors"]["warn"] == "#112233" and th["colors"]["danger"] == ui.PRESETS["ocean"]["danger"], th
    assert (th["bar"], th["bar_width"], th["emoji"], th["mode"]) == ("blocks", 10, True, "256"), th
    assert th["segments"] == [["cost"]], th  # the unknown name is skipped, the rest kept
    # The removed "spark" segment in an old theme file: skipped, the rest of the layout stays; validate still rejects it.
    ui.theme_file().write_text(json.dumps({"segments": [["ctx", "spark", "cost"], ["spark"]]}))
    assert ui.load()["segments"] == [["ctx", "cost"]] and "spark" not in ui.SEGMENTS
    ui.theme_file().write_text(json.dumps({"segments": [["spark"]]}))
    assert ui.load()["segments"] == ui.effective({})["segments"]
    assert "ctx, bar, eta" in ui.validate({"segments": [["spark"]]})[1]
finally:
    guard.STATE_DIR = real_dir
assert all(ui.validate({"colors": c})[1] is None for c in ui.PRESETS.values()) and set(ui.PRESETS) >= {"colorblind", "high-contrast"}
clean, err = ui.validate({"colors": {"ok": "AABBCC"}, "bar_width": 8, "emoji": False})
assert err is None and clean == {"colors": {"ok": "#aabbcc"}, "bar_width": 8, "emoji": False}, (clean, err)
for bad, words in (({"colors": {"ok": "red"}}, "#rrggbb"), ({"colors": {"bad": "#000000"}}, "ok, warn, danger, dim"),
                   ({"segments": [["ctx", "nope"]]}, "ctx, bar, eta"), ({"bar": "wide"}, "blocks, solid, dots"),
                   ({"bar_width": 3}, "4 to 20"), ({"bar_width": True}, "4 to 20"), ({"emoji": "yes"}, "on or off"),
                   ({"mode": "8bit"}, "auto, truecolor, 256"), ({"preset": "x"}, "colorblind"), ({"segments": []}, "ctx"),
                   ({"nope": 1}, "valid keys"), ([], "JSON object")):
    out, err = ui.validate(bad)
    assert out == {} and err and words in err, (bad, err)

# Bar: filled cells take the colour of the zone they sit in; the percent takes the zone of the fill.
paint = lambda text, role: ui.paint(text, role, T)
big = lambda ctx, **kw: rd({**sess(ctx), **kw}, {"window": 1_000_000})
assert plain(big(140_000)).count("▰▰▱▱▱▱▱▱▱▱ 14%") == 1
assert paint("▰▰", "ok") + paint("▱" * 8, "dim") + " " + paint("14%", "ok") in big(140_000)
assert paint("▰" * 6, "ok") + paint("▰", "warn") + paint("▱" * 3, "dim") + " " + paint("65%", "warn") in big(650_000)
assert paint("▰" * 6, "ok") + paint("▰▰", "warn") + paint("▰", "danger") + paint("▱", "dim") + " " + paint("85%", "danger") in big(850_000)
assert paint("💸 850k ctx", "danger") in big(850_000) and paint("💸 140k ctx", "ok") in big(140_000)
wide = ui.effective({"bar": "solid", "bar_width": 4, "segments": [["bar"]]})
assert plain(rd(sess(100_000), {}, wide)) == "██░░ 50%", plain(rd(sess(100_000), {}, wide))  # 100k of 200k
assert plain(rd(sess(100_000), {}, {**wide, "bar": "dots"})) == "●●○○ 50%"

only = lambda *names, **kw: ui.effective({"segments": [list(names)], **kw})

# ETA: tokens to the next level / mean growth per prompt; /compact drops are ignored; hidden when unknown.
eta = lambda ctx, prompt_ctx, **kw: plain(rd({**sess(ctx), "prompt_ctx": prompt_ctx}, kw.get("state", {}), only("eta")))
assert eta(40_000, [10_000, 20_000, 30_000, 40_000]) == "~8 msgs to 120k"
assert eta(30_000, [100_000, 130_000, 20_000, 30_000]) == "~5 msgs to 120k"  # growth 30k, (drop), 10k -> mean 20k
assert eta(130_000, [110_000, 120_000, 130_000]) == "~3 msgs to 160k"  # next level not yet reached
assert eta(40_100, [40_000, 40_100]) == "~99+ msgs to 120k"  # capped
assert eta(110_000, [100_000, 105_000, 110_000]) == "~2 msgs to 120k" and eta(115_000, [105_000, 110_000, 115_000]) == "~1 msg to 120k"
assert eta(40_000, [40_000]) == "" and eta(40_000, []) == ""  # needs 2 prompts
assert eta(40_000, [40_000, 40_000]) == "" and eta(10_000, [90_000, 10_000]) == ""  # no growth
assert eta(190_000, [150_000, 170_000, 190_000]) == ""  # every level reached
assert eta(40_000, [10_000, 20_000, 30_000, 40_000], state={"window": 1_000_000}) == "~56 msgs to 600k"

# Emoji off; segments reorder, hide and split into lines; no stray separators.
noemoji = ui.effective({"emoji": False})
line = rd(sess(150_000), {"jev": 0.1}, noemoji)
assert "💸" not in line and "🧭" not in line and "150k ctx" in line and "same task" in line, line
assert plain(rd(sess(150_000), {}, only("cost", "ctx"))) == "$1.00 · 💸 150k ctx"
assert plain(rd(sess(150_000), {}, ui.effective({"segments": [["ctx", "bar"], ["cost", "last"]]}))).split("\n")[1] == "$1.00 · last msg $0.10"
assert plain(rd(sess(40_000), {}, only("eta", "cost", "hint"))) == "$1.00"  # hidden segments leave no " · "
assert rd(sess(40_000), {}, only("eta", "hint")) == ""
assert plain(rd(sess(40_000), {}, ui.effective({"segments": [["hint"], ["cost"]]}))) == "$1.00"  # empty line skipped
full = plain(rd({**sess(150_000), "prompt_ctx": [100_000, 125_000, 150_000]}, {"jev": 0.1}))
assert full == "💸 150k ctx · ▰▰▰▰▰▰▰▰▱▱ 75% · ~1 msg to 160k · $1.00 · last msg $0.10 · 🧭 same task · /handover soon", full

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
assert events == [{"event": "warn", "level": 160_000, "ctx": 160_000}], events
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
    {**prompt("[Request interrupted by user]"), "timestamp": day_in},  # not a message
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
assert r["subagent_runs"] == 1 and r["share_cost_subagents"] == round(0.01 / 0.12, 3), r

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

# Recall: user prompts + Claude's text only, best match first, newer wins ties; current id = newest transcript.
import recall  # noqa: E402
t = transcript([
    {**prompt("use sqlite for the cache"), "timestamp": "2026-09-01T10:00:00Z"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "Chose sqlite because redis needs a server."},
                                                  {"type": "tool_use", "name": "Bash", "input": {"command": "redis-cli"}}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "redis sqlite cache"}]}},
    prompt("switch the cache to redis after all"),
])
hits = recall.search(t, "why redis cache")
assert [h[1] for h in hits] == ["user", "claude", "user"], hits  # 2 words, 1 word (newer first), tool lines skipped
assert hits[2][0] == "2026-09-01 10:00", hits
assert recall.search(t, "nothing matches") == []
proj = Path(tempfile.mkdtemp())
recall.PROJECTS = proj
(proj / "-tmp-my-app").mkdir()
for name, mtime in (("old", 1), ("new", 2)):
    f = proj / "-tmp-my-app" / f"{name}.jsonl"
    f.write_text("")
    os.utime(f, (mtime, mtime))
assert recall.current_id("/tmp/my.app") == "new"
assert recall.current_id("/tmp/other") is None

# Repo discovery: a plain folder holding repos at any depth (skips dependency folders, hidden folders and repos inside repos).
import subprocess  # noqa: E402
work = Path(tempfile.mkdtemp())
for sub in ("api", "web/deep/er/app", "node_modules/pkg", ".cache/x", "api/vendored"):
    (work / sub).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work / sub)], check=True)
(work / "docs").mkdir()
(work / "api" / "new.txt").write_text("x")
assert [r.relative_to(work).as_posix() for r in recall.find_repos(work)] == ["api", "web/deep/er/app"], recall.find_repos(work)
rep = recall.repo_report(work)
assert "## api  (branch: main)" in rep and "## web/deep/er/app  (branch: main)" in rep and "new.txt" in rep, rep
assert rep.count("\n## ") == 2, rep  # node_modules, .cache and api/vendored got no block
assert recall.find_repos(work / "api") == [work / "api"]  # started inside one repo: just that repo
assert "No git repos found" in recall.repo_report(work / "docs")

# Installer, against a temporary HOME: merges, keeps the old status line, safe twice, uninstall restores.
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
assert [h["command"] for g in s["hooks"]["PostToolUse"] for h in g["hooks"]] == cmds[1:], s["hooks"]  # subagent guard
assert s["model"] == "opus" and s["statusLine"]["padding"] == 0, s
assert s["statusLine"]["command"].endswith("statusline.py sh -c '~/my line.sh'"), s["statusLine"]
assert (home / ".claude" / "commands" / "handover.md").exists()
# An older or edited command file is replaced on install; the old one is kept as a timestamped .bak.
mine = home / ".claude" / "commands" / "handover.md"
mine.write_text("an older version")
assert "updated" in run() and mine.read_bytes() == Path("commands/handover.md").read_bytes()
baks = list((home / ".claude" / "commands").glob("handover.md.*.bak"))
assert len(baks) == 1 and baks[0].read_text() == "an older version", baks
run()
assert len(list((home / ".claude" / "commands").glob("handover.md.*.bak"))) == 1  # unchanged file: no new backup
assert (home / ".claude" / "session-guard" / "recall.py").resolve() == Path("recall.py").resolve()
assert len(list((home / ".claude").glob("settings.json.*.bak"))) == 1  # second run changed nothing
cmd = home / ".local" / "bin" / "session-guard"
assert cmd.is_symlink() and cmd.resolve() == Path("cli.py").resolve(), cmd  # the command; second run kept it
assert (home / ".claude" / "session-guard" / "before.json").exists()  # baseline saved by the install
cli = lambda *a: subprocess.run([sys.executable, "cli.py", *a], env={**os.environ, "HOME": str(home)},
                                capture_output=True, text=True).stdout
assert "warning hook (prompts)   yes" in cli("status") and "session-guard command    yes" in cli("status")
out = run("uninstall")
assert not cmd.is_symlink() and (home / ".claude" / "session-guard" / "before.json").exists(), out  # reports are kept
assert "NO" in cli("status")
assert json.loads((home / ".claude" / "settings.json").read_text()) == original, out
assert not (home / ".claude" / "commands" / "handover.md").exists()
assert not (home / ".claude" / "session-guard" / "recall.py").is_symlink()
home = Path(tempfile.mkdtemp())  # fresh Mac: no ~/.claude yet
out = run()
assert "New-task check (Jev): session-guard jev on / off" in out and "is OFF" not in out, out
assert json.loads((home / ".claude" / "settings.json").read_text())["statusLine"]["command"].endswith("statusline.py")
assert "export PATH" in out, out  # ~/.local/bin is not on PATH in this test
run("uninstall")
assert json.loads((home / ".claude" / "settings.json").read_text()) == {}

# Something else already at ~/.local/bin/session-guard: left alone. --purge deletes our files, never the clone.
home = Path(tempfile.mkdtemp())
(home / ".local" / "bin").mkdir(parents=True)
(home / ".local" / "bin" / "session-guard").write_text("mine")
assert "skipped" in run() and (home / ".local" / "bin" / "session-guard").read_text() == "mine"
run("uninstall")
assert (home / ".local" / "bin" / "session-guard").read_text() == "mine"
(home / ".local" / "bin" / "session-guard").unlink()
run()
out = run("uninstall", "--purge")
assert not (home / ".claude" / "session-guard").exists() and Path("install.py").exists(), out

# cli: .env editing keeps other lines and is owner-only; jev on/off; unknown command fails.
import cli  # noqa: E402
cli.HERE = Path(tempfile.mkdtemp())
(cli.HERE / ".env").write_text("# keep me\nOTHER=1\nSESSION_GUARD_JEV=0\n")
cli.main(["jev", "on", "--key", "abc"])
env_text = (cli.HERE / ".env").read_text()
assert env_text == "# keep me\nOTHER=1\nSESSION_GUARD_JEV=1\nTYPESAFE_API_KEY=abc\n", env_text
assert (cli.HERE / ".env").stat().st_mode & 0o777 == 0o600
cli.main(["jev", "off"])
assert "SESSION_GUARD_JEV=0" in (cli.HERE / ".env").read_text() and "TYPESAFE_API_KEY=abc" in (cli.HERE / ".env").read_text()
# Interactive: it asks for the key and saves what was typed; Enter alone keeps the saved key.
real_stdin, real_getpass = sys.stdin, cli.getpass.getpass
sys.stdin = type("Tty", (), {"isatty": lambda self: True})()
cli.getpass.getpass = lambda prompt: "typed-key"
cli.main(["jev", "on"])
assert "TYPESAFE_API_KEY=typed-key" in (cli.HERE / ".env").read_text() and "SESSION_GUARD_JEV=1" in (cli.HERE / ".env").read_text()
cli.getpass.getpass = lambda prompt: ""
os.environ["TYPESAFE_API_KEY"] = "typed-key"
cli.main(["jev", "on"])
assert "TYPESAFE_API_KEY=typed-key" in (cli.HERE / ".env").read_text()
sys.stdin, cli.getpass.getpass = real_stdin, real_getpass
for k in ("TYPESAFE_API_KEY", "SESSION_GUARD_JEV"):
    os.environ.pop(k, None)  # load_env put them there
try:
    cli.main(["nonsense"])
    raise AssertionError("unknown command should exit")
except SystemExit as e:
    assert e.code == 2

# theme command, against a temporary HOME: the real state folder is never touched.
th_home = Path(tempfile.mkdtemp())
th_file = th_home / ".claude" / "session-guard" / "theme.json"
th_env = {k: v for k, v in os.environ.items() if k not in ("NO_COLOR", "COLORTERM")}
th_cli = lambda *a: subprocess.run([sys.executable, "cli.py", "theme", *a], env={**th_env, "HOME": str(th_home), "COLORTERM": "truecolor"},
                                   capture_output=True, text=True)
saved = lambda: json.loads(th_file.read_text())
r = th_cli()
assert r.returncode == 0 and "preset    default" in r.stdout and "#87af87" in r.stdout and "38;2;135;175;135m" in r.stdout, r.stdout
assert r.stdout.count("ctx") >= 3 and "14%" in r.stdout and "65%" in r.stdout and "85%" in r.stdout and not th_file.exists(), r.stdout
r = th_cli("list")
assert r.returncode == 0 and all(name in r.stdout for name in ui.PRESETS) and "38;2;204;121;167m" in r.stdout, r.stdout  # colorblind danger
assert th_cli("use", "ocean").returncode == 0 and saved() == {"preset": "ocean"}
r = th_cli("set", "ok=FF0000", "bar_width=6", "emoji=off", "dim=#123456")
assert r.returncode == 0 and "#ff0000" in r.stdout, r.stdout
assert saved() == {"preset": "ocean", "colors": {"ok": "#ff0000", "dim": "#123456"}, "bar_width": 6, "emoji": False}, saved()
assert th_cli("set", "segments=ctx,bar,eta|cost,last").returncode == 0 and saved()["segments"] == [["ctx", "bar", "eta"], ["cost", "last"]]
assert th_cli("set", "bar=dots", "mode=256").returncode == 0 and saved()["bar"] == "dots" and saved()["mode"] == "256"
assert th_cli("use", "mono").returncode == 0 and "colors" not in saved() and saved()["preset"] == "mono"  # your colours would hide the preset
before = th_file.read_text()
for args, words in ((("set", "ok=red"), "#rrggbb"), (("set", "bar=wide"), "blocks, solid, dots"), (("set", "bar_width=30"), "4 to 20"),
                    (("set", "bar_width=abc"), "4 to 20"), (("set", "emoji=maybe"), "on or off"), (("set", "segments=ctx,nope"), "ctx, bar, eta"),
                    (("set", "mode=8bit"), "auto, truecolor, 256"), (("set", "color=#000000"), "ok, warn, danger, dim"),
                    (("set", "ok"), "key=value"), (("use", "nope"), "colorblind"), (("apply", "{bad"), "valid JSON"),
                    (("apply", '{"segments": [["x"]]}'), "ctx, bar, eta"), (("apply", '{"zzz": 1}'), "valid keys"), (("use",), "session-guard theme")):
    r = th_cli(*args)
    assert r.returncode != 0 and words in r.stderr and th_file.read_text() == before, (args, r.stderr)
r = th_cli("apply", '{"preset": "colorblind", "bar": "solid", "colors": {"dim": "#abcdef"}, "segments": [["ctx"], ["cost"]]}')
assert r.returncode == 0 and saved() == {"preset": "colorblind", "bar": "solid", "colors": {"dim": "#abcdef"}, "segments": [["ctx"], ["cost"]]}
assert [f.name for f in th_file.parent.iterdir()] == ["theme.json"]  # atomic write leaves no temp file
# The status line itself reads that file: two lines, truecolor; a broken file falls back to the defaults.
sl_run = lambda **env: subprocess.run([sys.executable, "statusline.py"], capture_output=True, text=True, input=json.dumps(
    {"session_id": "q", "transcript_path": str(t2)}), env={**th_env, "HOME": str(th_home), **env}).stdout
out = sl_run(COLORTERM="truecolor")
assert out.count("\n") == 2 and "38;2;" in out and "$" in out.split("\n")[1], repr(out)
assert "38;5;" in sl_run() and "38;2;" not in sl_run()
assert "\033" not in sl_run(NO_COLOR="1") and "50k ctx" in sl_run(NO_COLOR="1")
th_file.write_text("{{{")
assert "50k ctx" in sl_run() and sl_run().count("\n") == 1
assert th_cli("reset").returncode == 0 and not th_file.exists() and th_cli("reset").returncode == 0

# Theme picker: keys, screens and the saved theme (no terminal needed).
import themepicker as tp  # noqa: E402

def press(st, *keys):
    action = None
    for k in keys:
        st, action = tp.handle_key(st, k)
    return st, action
goto = lambda st, name: {**st, "row": tp.ROWS.index(name)}
st = tp.initial_state({})
assert st["row"] == 0 and st["palette"] is None and tp.to_theme(st) == {"preset": "default"}  # unchanged: a minimal file
assert len(tp.SWATCHES) == 24 and all(c in tp.SWATCHES for p in ui.PRESETS.values() for c in p.values())
# Moving: down/up and j/k, wrapping at both ends.
assert press(st, "down", "j")[0]["row"] == 2 and press(st, "up")[0]["row"] == len(tp.ROWS) - 1
assert press(goto(st, "cancel"), "down")[0]["row"] == 0 and press(st, "k", "k", "j")[0]["row"] == len(tp.ROWS) - 1
# Preset: right/left cycle (wrapping) and reset the colours to the preset's.
st2 = {**st, "colors": {**st["colors"], "ok": "#123456"}}
st2, _ = press(st2, "right")
assert st2["preset"] == "ocean" and st2["colors"] == ui.PRESETS["ocean"], st2
assert press(st, "left")[0]["preset"] == "colorblind" and press(st, "enter")[0]["preset"] == "ocean" and press(st, "l", " ", "h")[0]["preset"] == "ocean"
# Value rows: bar style cycles, width clamps at 4 and 20, emoji toggles.
assert press(goto(st, "bar"), "right", "right")[0]["bar"] == "dots" and press(goto(st, "bar"), "left")[0]["bar"] == "dots"
assert press(goto(st, "bar_width"), *["right"] * 30)[0]["bar_width"] == 20 and press(goto(st, "bar_width"), *["left"] * 30)[0]["bar_width"] == 4
assert press(goto(st, "emoji"), " ")[0]["emoji"] is False and press(goto(st, "emoji"), "left", "right")[0]["emoji"] is True
assert press(goto(st, "color:ok"), "right")[0] == goto(st, "color:ok")  # arrows do nothing on a colour row
# Show: toggle off and back on (usual order); the last visible segment cannot be hidden.
off, _ = press(goto(st, "seg:eta"), "enter")
assert off["segments"] == [[n for n in ui.SEGMENTS if n != "eta"]]
assert press(goto(off, "seg:eta"), " ")[0]["segments"] == [list(ui.SEGMENTS)]
few = tp.initial_state({"segments": [["ctx", "cost"], ["hint"]]})
assert press(goto(few, "seg:bar"), " ")[0]["segments"] == [["ctx", "bar", "cost"], ["hint"]]
assert press(goto(few, "seg:ctx"), " ")[0]["segments"] == [["cost"], ["hint"]]
one = tp.initial_state({"segments": [["cost"]]})
assert press(goto(one, "seg:cost"), " ")[0]["segments"] == [["cost"]]  # refused
assert press(goto(one, "seg:ctx"), " ")[0]["segments"] == [["ctx", "cost"]]
# Palette: open, move (the colour follows), Esc puts the old colour back, Enter keeps the new one.
p, _ = press(goto(st, "color:warn"), "enter")
assert p["palette"]["role"] == "warn" and p["palette"]["grid"][p["palette"]["index"]] == ui.PRESETS["default"]["warn"]
moved, _ = press(p, "right")
assert moved["colors"]["warn"] == p["palette"]["grid"][p["palette"]["index"] + 1] != p["colors"]["warn"]
back, _ = press(moved, "esc")
assert back["palette"] is None and back["colors"] == st["colors"]
kept, _ = press(moved, "enter")
assert kept["palette"] is None and kept["colors"]["warn"] == moved["colors"]["warn"]
far, _ = press(p, *["left"] * 40, "up")  # clamps at the first swatch
assert far["palette"]["index"] == 0
odd = tp.initial_state({"colors": {"ok": "#010203"}})
assert press(goto(odd, "color:ok"), "enter")[0]["palette"]["grid"][0] == "#010203"  # a colour not in the list is kept as the first
# Custom hex: valid (with or without #, upper case), invalid keeps the old colour and shows an error, Esc cancels.
typed, _ = press(p, "#", "F", "f", "9", "9", "0", "0")
assert typed["palette"]["typing"] == "#Ff9900"
done, _ = press(typed, "enter")
assert done["palette"] is None and done["colors"]["warn"] == "#ff9900"
done, _ = press(p, "h", "a", "b", "c", "1", "2", "3", "enter")
assert done["colors"]["warn"] == "#abc123"
bad, _ = press(p, "#", "z", "g", "1", "2", "enter")  # z and g are not typed
assert bad["palette"]["typing"] == "#12" and "6 hex digits" in bad["palette"]["error"] and bad["colors"] == p["colors"]
bad, _ = press(p, "#", "backspace", "backspace", "enter")
assert bad["palette"]["typing"] == "" and bad["palette"]["error"] and bad["colors"] == p["colors"]
assert press(bad, "esc")[0]["palette"]["typing"] is None and press(typed, "backspace")[0]["palette"]["typing"] == "#Ff990"
assert press(typed, "1")[0]["palette"]["typing"] == "#Ff9900"  # at most 6 digits
assert press(p, "s")[1] is None and press(p, "q")[1] is None  # keys of the main screen do nothing in the palette
# Save and cancel.
assert press(st, "s")[1] == "save" and press(st, "q")[1] == "cancel" and press(st, "esc")[1] == "cancel" and press(p, "ctrl-c")[1] == "cancel"
assert press(goto(st, "save"), "enter")[1] == "save" and press(goto(st, "cancel"), " ")[1] == "cancel" and press(goto(st, "save"), "right")[1] is None
assert tp.parse_keys(b"\x1b[A\x1bOB\x1b[C\x1b[Dq \r\x7f\x03\x1b[3~x\x1b") == ["up", "down", "right", "left", "q", " ", "enter", "backspace", "ctrl-c", "x", "esc"]
# Screens.
screen = plain(tp.render(st, 100))
assert screen.split("\n")[0] == "session-guard theme" and "› Preset      ‹ default ›" in screen and screen.count("› ") == 1, screen
assert "██ #87af87" in screen and "██ #8a8a8a" in screen and "[x] spark" not in screen and "[x] hint" in screen and "[ Cancel ]" in screen
assert screen.count("ctx") >= 3 and "14%" in screen and "85%" in screen and "q cancel" in screen
assert "38;2;135;175;135m" in tp.render(st, 100) and "› Bar style" in plain(tp.render(goto(st, "bar"), 100))
assert "[ ] eta" in plain(tp.render(off, 100)) and "› [ Save ]" in plain(tp.render(goto(st, "save"), 100))
pscreen = plain(tp.render(p, 100))
assert "warn colour" in pscreen and pscreen.count("██") >= 24 and "[██]" in pscreen and "(was #d7af5f)" in pscreen, pscreen
assert "Type a hex colour: #Ff9900" in plain(tp.render(typed, 100)) and "6 hex digits" in plain(tp.render(bad, 100))
for state_ in (st, p, typed, bad, goto(st, "save")):  # narrow terminal: the preview loses segments from the end, nothing is wider than 60
    assert all(tp.width(line) <= 60 for line in plain(tp.render(state_, 60)).split("\n")), plain(tp.render(state_, 60))
narrow = plain(tp.render(st, 60)).split("\n")
assert "/handover" not in "\n".join(narrow[2:5]) and "ctx" in narrow[2] and st["segments"] == [list(ui.SEGMENTS)]
assert tp.width("💸 1") == 4 and tp.width(ui.paint("██", "ok", T)) == 2
# What gets saved: the preset plus only the colours that differ; the file passes validate and loads to the same look.
st3, _ = press(st, "right")  # ocean
st3, _ = press(goto(st3, "color:warn"), "enter", "right", "enter")
st3, _ = press(goto(st3, "emoji"), "right")
out = tp.to_theme(st3)
assert out == {"preset": "ocean", "colors": {"warn": st3["colors"]["warn"]}, "emoji": False} and out["colors"]["warn"] != ui.PRESETS["ocean"]["warn"], out
assert ui.validate(out) == (out, None)
real_dir, guard.STATE_DIR = guard.STATE_DIR, Path(tempfile.mkdtemp())
try:
    cli.store(out)
    loaded = ui.load()
    assert loaded["preset"] == "ocean" and loaded["colors"] == st3["colors"] and loaded["emoji"] is False and loaded["bar"] == "blocks", loaded
    assert json.loads(ui.theme_file().read_text()) == out and [f.name for f in ui.theme_file().parent.iterdir()] == ["theme.json"]
    cli.store(tp.to_theme(tp.initial_state(ui.load_raw())))  # open and save without changes: same file
    assert json.loads(ui.theme_file().read_text()) == out
finally:
    guard.STATE_DIR = real_dir

# The picker in a real pseudo-terminal, with a temporary HOME (skipped where there is no pty).
import subprocess  # noqa: E402,F811

if os.name == "posix":
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios
    import time

    def drive(keys, home, rows=40, cols=100):
        """Run `cli.py theme` in a pty, send keys one by one. (output, exit code). Fails, not hangs, after 30 seconds."""
        pid, fd = pty.fork()
        if pid == 0:
            os.execve(sys.executable, [sys.executable, "cli.py", "theme"], {**th_env, "HOME": str(home), "COLORTERM": "truecolor", "TERM": "xterm-256color"})
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        out, end, closed = b"", time.time() + 30, False

        def pump(seconds, until=None):
            nonlocal out, closed
            stop = min(end, time.time() + seconds)
            while time.time() < stop and not closed and not (until and until in out):
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        chunk = b""
                    out, closed = out + chunk, not chunk
        pump(20, until=b"Show")  # first screen is up
        for key in keys:
            os.write(fd, key)
            pump(0.2)
        pump(20)  # until the picker exits and the terminal closes
        for _ in range(100):
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        else:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError(f"the picker did not exit: {out[-300:]!r}")
        os.close(fd)
        return out.decode("utf-8", "replace"), os.waitstatus_to_exitcode(status)

    pk_home = Path(tempfile.mkdtemp())
    pk_file = pk_home / ".claude" / "session-guard" / "theme.json"
    out, code = drive([b"\x1b[C", b"s"], pk_home)  # right arrow (preset: ocean), then save
    assert code == 0 and json.loads(pk_file.read_text()) == {"preset": "ocean"}, (code, out[-300:])
    assert out.index("\x1b[?1049h") < out.index("\x1b[?1049l") < out.index("Saved. Your status line updates on the next message.")
    assert "\x1b[?25l" in out and out.rindex("\x1b[?25h") > out.index("\x1b[?25l") and "38;2;" in out and "› Preset" in plain(out), out[-300:]
    assert [f.name for f in pk_file.parent.iterdir()] == ["theme.json"]
    # Second visit starts from the saved theme. Eight downs in one write reach "ctx"; Enter hides it; eight more reach [ Save ].
    out, code = drive([b"\x1b[B" * 8, b"\r", b"\x1b[B" * 8, b"\r"], pk_home)
    assert code == 0 and json.loads(pk_file.read_text()) == {"preset": "ocean", "segments": [list(ui.SEGMENTS[1:])]}, (code, out[-300:])
    # q, a lone Esc and Ctrl-C cancel without writing anything.
    for key in (b"q", b"\x1b", b"\x03"):
        cancel_home = Path(tempfile.mkdtemp())
        out, code = drive([b"\x1b[C", key], cancel_home)
        assert code == 0 and not (cancel_home / ".claude" / "session-guard" / "theme.json").exists(), (key, code, out[-300:])
        assert out.rindex("\x1b[?25h") > out.rindex("\x1b[?25l") and out.rindex("\x1b[?1049l") > out.rindex("\x1b[?1049h") and "Nothing changed." in out, (key, out[-300:])
# Not a terminal (a pipe, a script): no picker, the plain view and the hint, and it does not wait for input.
r = subprocess.run([sys.executable, "cli.py", "theme"], input="", capture_output=True, text=True, timeout=30,
                   env={**th_env, "HOME": str(Path(tempfile.mkdtemp()))})
assert r.returncode == 0 and "Preset" not in r.stdout and "preset    default" in r.stdout, r
assert "The picker needs a terminal. Use: session-guard theme use <preset> / theme set key=value." in r.stdout
assert th_cli("show").returncode == 0 and "preset    default" in th_cli("show").stdout

# Grabs: what made the context grow. Synthetic transcripts; the context is exact, every split is an estimate (by text length).
# A Read tool_use input is 17 + len(path) chars of JSON; text blocks and results are counted by length; thinking is not counted.
import grabs  # noqa: E402

def acall(msg_id, ctx, out=0, *blocks, **extra):
    return {"type": "assistant", "message": {"id": msg_id, "model": "claude-sonnet-5", "content": list(blocks), "usage": {
        "input_tokens": 0, "cache_read_input_tokens": ctx, "cache_creation_input_tokens": 0, "output_tokens": out}}, **extra}
use = lambda tid, name, **inp: {"type": "tool_use", "id": tid, "name": name, "input": inp}
result = lambda tid, text: {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}
item = lambda g, label: next((i for i in g["items"] if i["label"] == label), None)
def whole(g):  # baseline + the items always add up to the context
    assert g["baseline"] + sum(i["tokens"] for i in g["items"]) == g["context"], g
    assert [i["tokens"] for i in g["items"]] == sorted((i["tokens"] for i in g["items"]), reverse=True), g
    return g

# One gap: delta 10k is shared by visible length: 3000 chars of Claude's reply (text 2985 + a 15-char Bash tool input) and a 7000-char
# result give 3,000 / 7,000 tokens. Thinking and usage.output_tokens are ignored. Prompt text, command text and contents never reach a label.
g = whole(grabs.grabs(transcript([
    prompt("SECRET_PROMPT please"), acall("m1", 10_000, 90_000, {"type": "thinking", "thinking": "t" * 9000}, {"type": "text", "text": "r" * 2985},
                                          use("r1", "Bash", command="")),
    result("r1", "SECRET_CONTENT " * 466 + "x" * 10), acall("m2", 20_000)])))
assert g["context"] == 20_000 and g["baseline"] == 10_000, g
assert g["items"] == [{"label": "Bash output", "tokens": 7_000, "count": 1}, {"label": "Claude's replies", "tokens": 3_000, "count": 1}], g
assert g["calls"] == [{"context": 10_000, "delta": 0, "label": ""}, {"context": 20_000, "delta": 10_000, "label": "Bash output"}], g
# Two results in one gap, and a response split over two lines (same id; its lengths add up: 961 + 24 + 15 = 1000): 1000 : 3000 : 1000.
g = whole(grabs.grabs(transcript([
    acall("m1", 10_000, 1_000, {"type": "text", "text": "r" * 961}, use("a", "Read", file_path="/p/a.py")), acall("m1", 10_000, 1_000, use("b", "Bash", command="")),
    result("a", "x" * 3000), result("b", "y" * 1000), acall("m2", 20_000)])))
assert [(i["label"], i["tokens"]) for i in g["items"]] == [("Read a.py", 6000), ("Bash output", 2000), ("Claude's replies", 2000)], g
assert len(g["calls"]) == 2 and g["baseline"] == 10_000
# Nothing visible arrived: the growth is attributed to nothing and stays in baseline.
g = whole(grabs.grabs(transcript([acall("m1", 10_000), result("a", ""), acall("m2", 20_000)])))
assert g["items"] == [] and g["baseline"] == 20_000, g
# The same label across gaps is one item with a count; a non-text block weighs 6000 chars; unknown tool_use_id still counts.
g = whole(grabs.grabs(transcript([
    acall("m1", 10_000, 0, use("a", "Read", file_path="/p/a.py")), result("a", "x" * 100), acall("m2", 11_000, 0, use("b", "Read", file_path="/q/a.py")),
    result("b", "x" * 100), acall("m3", 13_000, 0, use("c", "Read", file_path="/p/a.py")),
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c", "content": [{"type": "image"}]},
                                              {"type": "tool_result", "tool_use_id": "nope", "content": "z" * 6000}]}}, acall("m4", 15_000)])))
assert item(g, "Read a.py")["count"] == 3 and item(g, "Read a.py")["tokens"] > 1000 and item(g, "tool output")["count"] == 1, g
# Gap 3 -> 4 by hand: 2000 tokens over 24 (reply) + 6000 (image) + 6000 (unknown id) chars.
assert item(g, "tool output")["tokens"] == round(2000 * 6000 / 12024), g
# Labels: tool names, file base names and fixed phrases only.
lab = grabs.label_for
assert [lab("Edit", {"file_path": "/a/b/c.txt"}), lab("NotebookEdit", {"notebook_path": "C:\\w\\n.ipynb"}), lab("Write", {}), lab("Agent", {}), lab("Task", {}),
        lab("Bash", {"command": "SECRET_CMD"}), lab("Grep", {"pattern": "SECRET"}), lab("Glob", {}), lab("WebFetch", {"url": "https://secret.example/x"}),
        lab("WebSearch", {"query": "SECRET"}), lab("mcp__db__query", {"sql": "SECRET"}), lab(None, None)] == [
    "Edit c.txt", "NotebookEdit n.ipynb", "Write", "subagent results", "subagent results", "Bash output", "Grep results", "Glob results",
    "web results", "web results", "mcp__db__query output", "tool output"]
# Your messages count after the first call. Nothing secret in the data, in describe, or in a warning.
tp_ = transcript([
    prompt("first"), acall("m1", 100_000, 0, use("r1", "Bash", command="echo SECRET_CMD https://secret.example")), result("r1", "SECRET_OUT " * 500),
    prompt("SECRET_PROMPT " * 1000), acall("m2", 130_000)])
g = whole(grabs.grabs(tp_))
assert {i["label"] for i in g["items"]} == {"Bash output", "your messages", "Claude's replies"}, g
assert item(g, "your messages")["tokens"] > item(g, "Bash output")["tokens"] > 0
everything = json.dumps(g) + grabs.describe(g) + guard.grab_note({"transcript": tp_})
assert "SECRET" not in everything and "echo" not in everything and "https" not in everything, everything
# Subagent lines: skipped by default, used with sidechain=True (a subagent file is all sidechain lines).
side = lambda row: {**row, "isSidechain": True}
mixed = transcript([acall("m1", 10_000, 0, use("a", "Read", file_path="/p/main.py")), result("a", "x" * 100),
                    side(acall("s1", 90_000, 0, use("s", "Grep", pattern="q"))), side(result("s", "y" * 100)), side(acall("s2", 95_000)),
                    acall("m2", 12_000)])
g = grabs.grabs(mixed)
assert g["context"] == 12_000 and {i["label"] for i in g["items"]} == {"Read main.py", "Claude's replies"}, g
agent_file = transcript([side(prompt("task for the subagent")), side(acall("s1", 90_000, 0, use("s", "Grep", pattern="q"))),
                         side(result("s", "y" * 100)), side(acall("s2", 95_000))])
g = whole(grabs.grabs(agent_file, sidechain=True))
assert g["context"] == 95_000 and g["baseline"] == 90_000 and g["items"] == [  # 5000 over 100 chars of result and a 16-char tool input
    {"label": "Grep results", "tokens": 4310, "count": 1}, {"label": "Claude's replies", "tokens": 690, "count": 1}], g
assert grabs.grabs(agent_file)["context"] == 0  # without the flag a subagent file is empty
# /compact (context falls): everything before is forgotten, the new size is all baseline.
g = whole(grabs.grabs(transcript([
    acall("m1", 10_000, 0, use("a", "Read", file_path="/p/old.py")), result("a", "x" * 100), acall("m2", 60_000),
    acall("m3", 20_000, 0, use("b", "Read", file_path="/p/new.py")), result("b", "x" * 100), acall("m4", 30_000),
    acall("m5", 0)])))  # a zero-usage placeholder line is not a call
assert g["baseline"] == 20_000 and g["items"] == [  # 10k over 100 chars of result and a 26-char tool input
    {"label": "Read new.py", "tokens": 7937, "count": 1}, {"label": "Claude's replies", "tokens": 2063, "count": 1}], g
assert [c["delta"] for c in g["calls"]] == [0, 50_000, -40_000, 10_000] and g["calls"][2]["label"] == "compact", g["calls"]
# A small drop (a model switch counts the same text differently) is not a reset: items are kept and scaled by f. Below half is a reset.
drop = lambda last: whole(grabs.grabs(transcript([
    acall("m1", 10_000, 1_000, use("a", "Read", file_path="/p/a.py")), result("a", "x" * 100), acall("m2", 50_000), acall("m3", last), result("zz", "x" * 99)])))
g = drop(45_000)  # f = 0.9
assert g["baseline"] + sum(i["tokens"] for i in g["items"]) == 45_000 and g["baseline"] == 9_000, g
assert g["items"] == [{"label": "Read a.py", "tokens": 29_032, "count": 1}, {"label": "Claude's replies", "tokens": 6_968, "count": 1}], g
assert [c["delta"] for c in g["calls"]] == [0, 40_000, -5_000] and g["calls"][2]["label"] == "", g["calls"]
g = drop(25_000)  # exactly f = 0.5: still not a reset
assert g["baseline"] == 5_000 and g["items"][0] == {"label": "Read a.py", "tokens": 16_129, "count": 1}, g
g = drop(24_999)  # just under: reset
assert g["items"] == [] and g["baseline"] == 24_999 and g["calls"][2]["label"] == "compact", g
# calls keeps the last 60; the shape is ints and short strings.
g = grabs.grabs(transcript([acall(f"c{i}", 1000 * (i + 1)) for i in range(70)]))
assert len(g["calls"]) == 60 and g["calls"][0]["context"] == 11_000 and g["calls"][-1] == {"context": 70_000, "delta": 1000, "label": ""}, g["calls"][-1]
assert all(set(c) == {"context", "delta", "label"} and isinstance(c["context"], int) for c in g["calls"])
# describe: +Nk, ×count only above 1, share of the context, top n, 3% threshold, empty cases.
big = {"context": 100_000, "items": [{"label": "Read report.py", "tokens": 38_000, "count": 1}, {"label": "Bash output", "tokens": 22_000, "count": 4},
                                     {"label": "web results", "tokens": 5_000, "count": 2}, {"label": "Glob results", "tokens": 4_000, "count": 1}]}
assert grabs.describe(big) == "Read report.py +38k (38%), Bash output ×4 +22k (22%), web results ×2 +5k (5%)"
assert grabs.describe(big, 1) == "Read report.py +38k (38%)" and grabs.describe({**big, "context": 150_000}, 4).endswith("web results ×2 +5k (3%)")
assert "Glob" not in grabs.describe({**big, "context": 150_000}, 4)  # 4k of 150k is under 3%
assert grabs.describe({**big, "context": 2_000_000}) == "" and grabs.describe({"context": 0, "items": big["items"]}) == "" and grabs.describe({}) == ""
# describe leads with what the user can change; the conversation (replies and your messages) is a tail, only from 10% of the context.
talk = {"context": 100_000, "items": [{"label": "Claude's replies", "tokens": 50_000, "count": 30}, {"label": "your messages", "tokens": 20_000, "count": 5},
                                      {"label": "Read report.py", "tokens": 12_000, "count": 1}, {"label": "Bash output", "tokens": 2_000, "count": 3}]}
assert grabs.describe(talk) == "Read report.py +12k (12%); the conversation itself (replies and your messages) is 70%"
assert [i["label"] for i in grabs.tool_items(talk)] == ["Read report.py", "Bash output"] and len(talk["items"]) == 4  # the full list is untouched
assert grabs.describe({**talk, "items": talk["items"][:2]}) == "the conversation itself (replies and your messages) is 70%"
assert grabs.describe({**talk, "items": [{**talk["items"][0], "tokens": 6_000}, {**talk["items"][1], "tokens": 3_000}, *talk["items"][2:]]}) == "Read report.py +12k (12%)"
assert grabs.describe({**talk, "items": [{**talk["items"][0], "tokens": 9_999}, *talk["items"][2:]]}) == "Read report.py +12k (12%)"  # 9.999% < 10%
assert grabs.describe({"context": 100_000, "items": talk["items"][:2]}, 1).endswith("is 70%") and grabs.describe({"context": 0, "items": talk["items"]}) == ""
# Odd input never raises: missing file, a folder, junk lines, odd shapes.
empty = {"context": 0, "baseline": 0, "items": [], "calls": []}
assert grabs.grabs(Path("/no/such/file.jsonl")) == empty and grabs.grabs(Path(tempfile.mkdtemp())) == empty
junk = transcript([]); junk.write_text('not json\n5\n[]\nnull\n{"type": "assistant", "message": "x"}\n{"type": "user", "message": {"content": 7}}\n'
                                       + json.dumps(acall("ok", 5000)) + '\n{"type": "assistant", "message": {"usage": {"output_tokens": "x"}}}\n')
assert grabs.grabs(junk)["context"] == 5000 and guard.grab_note({}) == "" and guard.grab_note({"transcript": Path("/no/such")}) == ""

# The warning says what grew the context, only when a level is crossed, and is the old message if grabs fails.
grow = transcript([prompt("go"), acall("m1", 100_000, 0, use("r", "Read", file_path="/x/report.py")), result("r", "x" * 4000), acall("m2", 130_000)])
sg = {**sess(130_000), "transcript": grow}
old = "💸 Session context is 130k tokens (65% of 200k; ~$1.00 so far, last message ~$0.10). Every message re-sends all of it. When you finish this step: /handover, then /clear and /continue."
msg = guard.decide({}, sg, {})["systemMessage"]
assert msg == old.replace("Every message", "Biggest grabs (estimates): Read report.py +30k (23%). Every message"), msg
assert guard.decide({}, {**sg, "context": 100_000}, {}) is None and guard.decide({}, sess(130_000), {})["systemMessage"] == old  # no transcript: unchanged
real_grabs = grabs.grabs
def boom(*a, **k):
    raise RuntimeError("grabs broke")
grabs.grabs = boom
try:
    assert guard.decide({}, sg, {})["systemMessage"] == old
finally:
    grabs.grabs = real_grabs
calls_seen = []
grabs.grabs = lambda *a, **k: calls_seen.append(1) or real_grabs(*a, **k)
try:
    guard.decide({}, {**sg, "context": 100_000}, {})
    assert not calls_seen  # computed only when a warning fires
finally:
    grabs.grabs = real_grabs
# A subagent's warning: short, from its own transcript.
am = guard.decide_agent({"context": 160_000, "total": 0.5, "transcript": agent_file}, {})["systemMessage"]
assert "Biggest grabs (estimates): Grep results +4k (5%). Every further step" in am, am
agent_big = transcript([side(acall("s1", 100_000, 0, use("s", "Bash", command="x"))), side(result("s", "y" * 100)), side(acall("s2", 160_000))])
am = guard.decide_agent({"context": 160_000, "total": 0.5, "transcript": agent_big}, {})["systemMessage"]
assert "Biggest grabs (estimates): Bash output +52k (32%). Every further step" in am, am
# End to end through the real hooks (temporary HOME): the warning and the status line, with no secret in either.
e2e_home = Path(tempfile.mkdtemp())
e2e = lambda script, **payload: subprocess.run([sys.executable, script], input=json.dumps({"session_id": "g", "transcript_path": str(grow), **payload}),
                                               capture_output=True, text=True, env={**os.environ, "HOME": str(e2e_home), "NO_COLOR": "1"}).stdout
out = e2e("guard.py", hook_event_name="UserPromptSubmit", prompt="hi")
assert "Biggest grabs (estimates): Read report.py +30k (23%)." in json.loads(out)["systemMessage"], out
assert "top: Read report.py +30k" in e2e("statusline.py")
assert "report.py" not in (e2e_home / ".claude" / "session-guard" / "events.jsonl").read_text()  # numbers only in the log
# Status line "grab" segment: dim, only once a level is reached, only if the segment is shown, never breaks the line.
past = lambda ctx, **kw: rd({**sess(ctx), "transcript": grow, **kw}, {})
assert "top: Read report.py +30k" in plain(past(130_000)) and paint("top: Read report.py +30k", "dim") in past(130_000)
assert "top:" not in past(100_000) and "top:" not in plain(rd({**sess(130_000), "transcript": Path("/no/such")}, {})) and "ctx" in rd({**sess(130_000), "transcript": 5}, {})
assert plain(past(130_000)).index("top: ") < plain(past(130_000)).index("/handover soon") and "grab" in ui.SEGMENTS and ui.SEGMENTS.index("grab") == ui.SEGMENTS.index("hint") - 1
assert "grab" in tp.HELP and "top: Read report.py" in plain(tp.render(tp.initial_state({}), 200)) and "[x] grab" in plain(tp.render(tp.initial_state({}), 100))
talky = transcript([prompt("a"), acall("m1", 100_000), prompt("SECRET " * 5000), acall("m2", 130_000)])  # growth is only your message
assert "top:" not in plain(rd({**sess(130_000), "transcript": talky}, {})) and "your messages" in grabs.grabs(talky)["items"][0]["label"]
assert guard.decide({}, {**sess(130_000), "transcript": talky}, {})["systemMessage"].count("the conversation itself (replies and your messages) is 23%") == 1
grabs.grabs = boom
try:
    assert plain(past(130_000)).count("top:") == 0
    assert "top: shown" in plain(past(130_000, grab="top: shown"))
    assert plain(rd({**sess(130_000), "transcript": grow}, {}, only("ctx"))) == "💸 130k ctx"  # not even tried when the segment is hidden
finally:
    grabs.grabs = real_grabs

print("all good")
