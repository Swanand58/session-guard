#!/usr/bin/env python3
"""Before/after cost report from Claude Code transcripts.

    python3 report.py --since 2026-09-01 --until 2026-09-30 --json before.json
    python3 report.py --since 2026-10-01 --until 2026-10-31 --json after.json
    python3 report.py --compare before.json after.json

Dates are inclusive, in UTC. Costs use Copilot AI-credit prices from guard.py.
Reads only token counts, models, timestamps and slash-command names;
never prints or saves prompt text.
"""

import argparse
import json
import statistics
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import guard

PROJECTS = Path.home() / ".claude" / "projects"

# (key, label, lower_is_better) in display order.
METRICS = [
    ("total_cost", "Total cost ($)", True),
    ("cost_per_prompt", "Cost per message you sent ($)", True),
    ("cost_per_active_day", "Cost per active day ($)", True),
    ("prompts", "Messages you sent", None),
    ("sessions", "Sessions", None),
    ("active_days", "Active days", None),
    ("median_context_k", "Median context per call (k tokens)", True),
    ("p90_context_k", "90th percentile context (k tokens)", True),
    ("share_cost_over_150k", "Share of cost at >150k context", True),
    ("share_cost_over_300k", "Share of cost at >300k context", True),
    ("sessions_over_300k", "Sessions that passed 300k", True),
    ("longest_session_prompts", "Longest session (messages)", True),
    ("handovers", "/handover used", None),
    ("clears", "/clear used", None),
    ("compacts", "/compact used", None),
    ("guard_warnings", "session-guard warnings", None),
    ("jev_checks", "Jev checks", None),
    ("jev_blocks", "Jev blocks", None),
    ("jev_overrides", "Blocks you overrode", None),
]


def in_period(ts: str | float, start: datetime, end: datetime) -> bool:
    try:
        t = datetime.fromtimestamp(ts, timezone.utc) if isinstance(ts, (int, float)) else datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return False
    return start <= t < end


def analyse(since: date, until: date, projects: Path = PROJECTS, events_file: Path | None = None) -> dict:
    start = datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    end = datetime(until.year, until.month, until.day, tzinfo=timezone.utc) + timedelta(days=1)
    calls = []  # (context, cost, model)
    prompts = 0
    days = set()
    commands = Counter()
    per_session = {}  # file -> {"prompts": n, "peak": ctx}

    for f in projects.rglob("*.jsonl"):  # includes subagent transcripts: they cost money too
        seen = set()
        sess = {"prompts": 0, "peak": 0}
        for line in f.open(errors="ignore"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not in_period(e.get("timestamp"), start, end):
                continue
            msg = e.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if e.get("type") == "user" and isinstance(content, str):
                for cmd in ("handover", "continue", "clear", "compact"):
                    if f"<command-name>/{cmd}</command-name>" in content:
                        commands[cmd] += 1
            if guard.prompt_text(e):
                prompts += 1
                sess["prompts"] += 1
                continue
            usage = msg.get("usage") if isinstance(msg, dict) else None
            key = (msg.get("id") or e.get("requestId") or e.get("uuid")) if usage else None
            if not usage or key in seen:
                continue
            seen.add(key)
            tin, tread, twrite, tout = (usage.get(k) or 0 for k in (
                "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
            ctx = tin + tread + twrite
            p = guard.price_for(msg.get("model"), ctx)
            calls.append((ctx, (tin * p[0] + tread * p[1] + twrite * p[2] + tout * p[3]) / 1e6, msg.get("model")))
            days.add(e["timestamp"][:10])
            sess["peak"] = max(sess["peak"], ctx)
        if sess["prompts"] or sess["peak"]:
            per_session[f] = sess

    events = Counter()
    events_file = events_file or guard.STATE_DIR / "events.jsonl"
    if events_file.exists():
        for line in events_file.open():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if in_period(ev.get("ts"), start, end):
                events[ev.get("event")] += 1

    total = sum(c for _, c, _ in calls)
    ctxs = sorted(x for x, _, _ in calls) or [0]
    by_model = Counter()
    for _, c, m in calls:
        by_model[m or "?"] += c
    return {
        "period": f"{since} to {until}",
        "total_cost": round(total, 2),
        "cost_per_prompt": round(total / prompts, 3) if prompts else 0,
        "cost_per_active_day": round(total / len(days), 2) if days else 0,
        "prompts": prompts,
        "sessions": len(per_session),
        "active_days": len(days),
        "median_context_k": round(statistics.median(ctxs) / 1000),
        "p90_context_k": round(ctxs[int(0.9 * (len(ctxs) - 1))] / 1000),
        "share_cost_over_150k": round(sum(c for x, c, _ in calls if x > 150_000) / total, 3) if total else 0,
        "share_cost_over_300k": round(sum(c for x, c, _ in calls if x > 300_000) / total, 3) if total else 0,
        "sessions_over_300k": sum(s["peak"] > 300_000 for s in per_session.values()),
        "longest_session_prompts": max((s["prompts"] for s in per_session.values()), default=0),
        "handovers": commands["handover"],
        "clears": commands["clear"],
        "compacts": commands["compact"],
        "guard_warnings": events["warn"],
        "jev_checks": events["jev"],
        "jev_blocks": events["block"],
        "jev_overrides": events["override"],
        "cost_by_model": {m: round(c, 2) for m, c in by_model.most_common()},
    }


def fmt(key: str, v) -> str:
    return f"{v:.1%}" if key.startswith("share_") else f"{v:,}"


def show(r: dict) -> None:
    print(f"\nsession-guard report: {r['period']}\n")
    for key, label, _ in METRICS:
        print(f"  {label:38} {fmt(key, r[key]):>12}")
    print("\n  Cost by model:")
    for m, c in [x for x in r["cost_by_model"].items() if x[1]][:8]:
        print(f"    {m:36} {c:>12,.2f}")


def compare(a: dict, b: dict) -> None:
    print(f"\n  BEFORE: {a['period']}\n  AFTER:  {b['period']}")
    print(f"\n  {'':38} {'BEFORE':>12} {'AFTER':>12} {'CHANGE':>9}")
    for key, label, lower_better in METRICS:
        x, y = a[key], b[key]
        change = f"{(y - x) / x:+.0%}" if x else "n/a"
        mark = ""
        if lower_better is not None and x and y != x:
            mark = " ✓" if (y < x) == lower_better else " ✗"
        print(f"  {label:38} {fmt(key, x):>12} {fmt(key, y):>12} {change:>9}{mark}")
    print("\n  ✓ better, ✗ worse. Compare 'Cost per message' and 'Cost per active day',")
    print("  not total cost: the amount of work differs between periods.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=date.fromisoformat)
    ap.add_argument("--until", type=date.fromisoformat, default=date.today())
    ap.add_argument("--json", type=Path, help="also save the numbers to this file")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = ap.parse_args()
    if args.compare:
        compare(*(json.loads(p.read_text()) for p in args.compare))
        return
    if not args.since:
        ap.error("--since is required (or use --compare)")
    r = analyse(args.since, args.until)
    show(r)
    if args.json:
        args.json.write_text(json.dumps(r, indent=2))
        print(f"\n  saved {args.json}")


if __name__ == "__main__":
    main()
