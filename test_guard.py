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

# Status line.
import statusline  # noqa: E402

assert statusline.render(sess(0), {}) == ""  # nothing before the first reply
line = statusline.render(sess(150_000), {"jev": 0.1})
assert "150k ctx" in line and "same task" in line and "/handover soon" in line, line
line = statusline.render(sess(190_000), {"jev": 0.93})
assert "new task? 93%" in line and "/handover now" in line, line
assert "/handover" not in statusline.render(sess(40_000), {})
assert "agents" not in line and "$1.00 (agents $0.25)" in statusline.render({**sess(150_000), "agents": 0.25}, {})
assert "/handover" not in statusline.render(sess(310_000), {"window": 1_000_000})  # 31% of a 1M window: quiet
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
assert "session-guard jev on" in out, out
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

print("all good")
