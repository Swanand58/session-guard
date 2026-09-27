#!/usr/bin/env python3
"""Session stats for the last 30 days, before and after session-guard.

    python3 report.py before    # run once, BEFORE installing: last 30 days -> before.json
    python3 report.py after     # 30 days later: days since 'before' -> after.json + comparison
    python3 report.py compare   # show the comparison again

Reads Claude Code transcripts (~/.claude/projects) and GitHub Copilot CLI
sessions (~/.copilot/session-state). Uses only token counts, model names,
timestamps and slash-command names; never prompt text. Claude Code costs use
Copilot AI-credit prices from guard.py; Copilot CLI uses the credits it billed.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

import guard

PROJECTS = Path.home() / ".claude" / "projects"
COPILOT = Path.home() / ".copilot" / "session-state"
HERE = Path(__file__).resolve().parent

# (key, label, lower_is_better) in display order.
METRICS = [
    ("sessions", "Sessions", None),
    ("sessions_claude_code", "  in Claude Code", None),
    ("sessions_copilot_cli", "  in Copilot CLI", None),
    ("messages", "Messages you sent", None),
    ("messages_per_session", "Messages per session (avg)", None),
    ("tokens_per_session", "Tokens per session (avg)", True),
    ("tokens_per_session_median", "Tokens per session (median)", True),
    ("cost", "Total cost ($)", None),
    ("cost_per_session", "Cost per session (avg, $)", True),
    ("cost_per_message", "Cost per message ($)", True),
    # Copilot CLI only logs per-session totals, so these four cover Claude Code sessions only.
    ("peak_context_avg_k", "Peak context per session (avg, k) *", True),
    ("sessions_over_150k", "Sessions that passed 150k *", True),
    ("sessions_over_300k", "Sessions that passed 300k *", True),
    ("share_cost_over_300k", "Share of cost at >300k context *", True),
    ("biggest_session_cost", "Most expensive session ($)", True),
    ("biggest_session_messages", "Longest session (messages)", True),
    ("handovers", "/handover used", None),
    ("clears", "/clear used", None),
    ("compacts", "/compact used", None),
    ("guard_warnings", "session-guard warnings", None),
    ("jev_blocks", "Jev blocks", None),
    ("jev_overrides", "Jev blocks you overrode", None),
]


def session_id(f: Path, projects: Path) -> str:
    """<project>/<session>.jsonl, or <project>/<session>/subagents/*.jsonl -> the parent session."""
    parts = f.relative_to(projects).parts
    return f"{parts[0]}/{Path(parts[1]).stem}"


def analyse(
    since: date, until: date, projects: Path = PROJECTS, events_file: Path | None = None, copilot: Path = COPILOT
) -> dict:
    tz = datetime.now().astimezone().tzinfo
    start = datetime.combine(since, time.min, tz)
    end = datetime.combine(until + timedelta(days=1), time.min, tz)

    def in_period(ts) -> bool:
        try:
            # Before Python 3.11, fromisoformat() can't read a trailing 'Z'.
            t = datetime.fromtimestamp(ts, tz) if isinstance(ts, (int, float)) else datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return start <= t < end
        except (TypeError, ValueError, AttributeError):  # missing or malformed timestamp
            return False

    sessions = defaultdict(lambda: {"messages": 0, "tokens": 0, "cost": 0.0, "peak": 0, "tool": "claude_code"})
    commands = Counter()
    by_model = Counter()
    cost_over_300k = 0.0
    copilot_no_usage = 0

    for f in projects.glob("*/**/*.jsonl"):  # inside a project folder, including subagents/
        s = sessions[session_id(f, projects)]
        seen = set()
        for line in f.open(errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not in_period(e.get("timestamp")):
                continue
            msg = e.get("message") if isinstance(e.get("message"), dict) else {}
            content = msg.get("content")
            if e.get("type") == "user" and isinstance(content, str):
                for cmd in ("handover", "clear", "compact"):
                    commands[cmd] += f"<command-name>/{cmd}</command-name>" in content
            if guard.prompt_text(e):
                s["messages"] += 1
                continue
            usage = msg.get("usage")
            key = msg.get("id") or e.get("requestId") or e.get("uuid")
            if not usage or key in seen:
                continue  # one API response is split over several lines that repeat its usage
            seen.add(key)
            tin, tread, twrite, tout = (usage.get(k) or 0 for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
            ctx = tin + tread + twrite
            p = guard.price_for(msg.get("model"), ctx)
            cost = (tin * p[0] + tread * p[1] + twrite * p[2] + tout * p[3]) / 1e6
            s["tokens"] += ctx + tout
            s["cost"] += cost
            s["peak"] = max(s["peak"], ctx)
            by_model[msg.get("model") or "?"] += cost
            if ctx > 300_000:
                cost_over_300k += cost

    # Copilot CLI: <id>/events.jsonl (older: <id>.jsonl). Exact per-model totals are written
    # in session.shutdown events (one per run; a resumed session has several).
    for f in list(copilot.glob("*/events.jsonl")) + list(copilot.glob("*.jsonl")):
        s = sessions[f"copilot/{f.parent.name if f.name == 'events.jsonl' else f.stem}"]
        s.update(tool="copilot_cli", peak=None)
        had_usage = False
        for line in f.open(errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not in_period(e.get("timestamp")):
                continue
            if e.get("type") == "user.message":
                s["messages"] += 1
            if e.get("type") != "session.shutdown":
                continue
            for model, m in ((e.get("data") or {}).get("modelMetrics") or {}).items():
                u = m.get("usage") or {}
                tin, tread, twrite, tout = (u.get(k) or 0 for k in (
                    "inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens"))
                if m.get("totalNanoAiu"):
                    cost = m["totalNanoAiu"] / 1e9 * 0.01  # billed: 1e9 nano credits = 1 credit = $0.01
                else:
                    p = guard.price_for(model)
                    cost = (max(0, tin - tread) * p[0] + tread * p[1] + twrite * p[2] + tout * p[3]) / 1e6
                s["tokens"] += tin + tout  # Copilot's inputTokens already include cache reads
                s["cost"] += cost
                by_model[model] += cost
                had_usage = True
        copilot_no_usage += s["messages"] > 0 and not had_usage

    events = Counter()
    events_file = events_file or guard.STATE_DIR / "events.jsonl"
    if events_file.exists():
        for line in events_file.open():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if in_period(ev.get("ts")):
                events[ev.get("event")] += 1

    real = [s for s in sessions.values() if s["tokens"]]
    measured = [s for s in real if s["peak"] is not None]  # sessions with per-call context sizes
    measured_cost = sum(s["cost"] for s in measured)
    n = len(real) or 1
    msgs = sum(s["messages"] for s in real)
    cost = sum(s["cost"] for s in real)
    biggest = max(real, key=lambda s: s["cost"], default={"cost": 0, "messages": 0})
    return {
        "period": f"{since} to {until}",
        "since": str(since),
        "until": str(until),
        "sessions": len(real),
        "sessions_claude_code": sum(s["tool"] == "claude_code" for s in real),
        "sessions_copilot_cli": sum(s["tool"] == "copilot_cli" for s in real),
        "copilot_sessions_without_usage": copilot_no_usage,
        "messages": msgs,
        "messages_per_session": round(msgs / n, 1),
        "tokens_per_session": round(sum(s["tokens"] for s in real) / n),
        "tokens_per_session_median": round(statistics.median([s["tokens"] for s in real] or [0])),
        "cost": round(cost, 2),
        "cost_per_session": round(cost / n, 2),
        "cost_per_message": round(cost / msgs, 3) if msgs else 0,
        "peak_context_avg_k": round(sum(s["peak"] for s in measured) / (len(measured) or 1) / 1000),
        "sessions_over_150k": sum(s["peak"] > 150_000 for s in measured),
        "sessions_over_300k": sum(s["peak"] > 300_000 for s in measured),
        "share_cost_over_300k": round(cost_over_300k / measured_cost, 3) if measured_cost else 0,
        "biggest_session_cost": round(biggest["cost"], 2),
        "biggest_session_messages": max((s["messages"] for s in real), default=0),
        "handovers": commands["handover"],
        "clears": commands["clear"],
        "compacts": commands["compact"],
        "guard_warnings": events["warn"],
        "jev_blocks": events["block"],
        "jev_overrides": events["override"],
        "cost_by_model": {m: round(c, 2) for m, c in by_model.most_common() if c},
    }


def fmt(key: str, v) -> str:
    return f"{v:.1%}" if key.startswith("share_") else f"{v:,}"


def show(r: dict) -> None:
    print(f"\n  {r['period']}\n")
    for key, label, _ in METRICS:
        print(f"  {label:36} {fmt(key, r[key]):>14}")
    print("\n  * Claude Code sessions only (Copilot CLI logs just per-session totals).")
    if r.get("copilot_sessions_without_usage"):
        print(f"  {r['copilot_sessions_without_usage']} Copilot CLI session(s) ended without a usage summary"
              " (crashed or still open) and are not counted.")
    print("\n  Cost by model:")
    for m, c in list(r["cost_by_model"].items())[:8]:
        print(f"    {m:34} {c:>14,.2f}")


def compare(a: dict, b: dict) -> None:
    print(f"\n  BEFORE: {a['period']}\n  AFTER:  {b['period']}")
    print(f"\n  {'':36} {'BEFORE':>12} {'AFTER':>12} {'CHANGE':>8}")
    for key, label, lower_better in METRICS:
        x, y = a.get(key, 0), b.get(key, 0)  # older saved files may lack newer metrics
        change = f"{(y - x) / x:+.0%}" if x else "n/a"
        mark = " ✓" if lower_better is not None and x and y != x and (y < x) == lower_better else (
            " ✗" if lower_better is not None and x and y != x else "")
        print(f"  {label:36} {fmt(key, x):>12} {fmt(key, y):>12} {change:>8}{mark}")
    print("\n  * Claude Code sessions only.  ✓ better  ✗ worse.  Per-session and per-message numbers are the fair ones to")
    print("  compare; totals depend on how much work you did in each period.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["before", "after", "compare"])
    ap.add_argument("--days", type=int, default=30, help="how many days 'before' looks back (default 30)")
    args = ap.parse_args()
    before_file, after_file = HERE / "before.json", HERE / "after.json"
    today = date.today()

    if args.step == "before":
        r = analyse(today - timedelta(days=args.days - 1), today)
        before_file.write_text(json.dumps(r, indent=2))
        show(r)
        print(f"\n  saved {before_file.name}. Now install session-guard; run 'report.py after' in ~30 days.")
    elif args.step == "after":
        if not before_file.exists():
            raise SystemExit("Run 'report.py before' first.")
        before = json.loads(before_file.read_text())
        since = date.fromisoformat(before["until"]) + timedelta(days=1)  # never overlaps 'before'
        if since > today:
            raise SystemExit("'before' was saved today. Use session-guard for a while first.")
        r = analyse(since, today)
        after_file.write_text(json.dumps(r, indent=2))
        show(r)
        compare(before, r)
    else:
        if not (before_file.exists() and after_file.exists()):
            raise SystemExit("Need both before.json and after.json.")
        compare(json.loads(before_file.read_text()), json.loads(after_file.read_text()))


if __name__ == "__main__":
    main()
