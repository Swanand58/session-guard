#!/usr/bin/env python3
"""Look things up in an earlier Claude Code session, so a handover note can stay short.

    recall.py id                      # id of the current session (newest transcript in this folder)
    recall.py git                     # branch, changes and recent commits of every git repo under this folder
    recall.py <session-id> "words"    # up to 5 short matches from that session, with timestamps

/handover records the id; after /clear the new session searches the old one only when it
needs a detail the note left out. Stdlib only.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # recall.py is symlinked into ~/.claude
from guard import prompt_text

PROJECTS = Path.home() / ".claude" / "projects"
TOP, PER_MATCH, TOTAL = 5, 400, 2000  # keep lookups cheap: the point is a small new session
SKIP = {"node_modules", "venv", "__pycache__", "dist", "build", "target"}  # dependency folders: no repos of yours in there
MAX_REPOS = 40


def current_id(cwd: str) -> str | None:
    # ponytail: newest transcript wins; two sessions running in the same folder could pick the other one.
    folder = PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", cwd)
    files = sorted(folder.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    return files[-1].stem if files else None


def find_repos(root: Path) -> list:
    """Every git repo at or under root, at any depth. Does not look inside a repo for more (submodules, vendored copies)."""
    repos = []
    for dirpath, dirs, files in os.walk(root):
        if ".git" in dirs or ".git" in files:  # .git is a file in worktrees and submodules
            repos.append(Path(dirpath))
            dirs[:] = []
        else:
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
        # ponytail: walks every folder; started in a huge tree (e.g. home) this is slow. Add a time cap if that bites.
        if len(repos) >= MAX_REPOS:
            break
    return sorted(repos)


def git(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


def repo_report(root: Path) -> str:
    """Per repo: where it is (relative to root), its branch, what changed, recent commits. For /handover and /continue."""
    repos = find_repos(root)
    if not repos:
        return f"No git repos found under {root}."
    out = [f"Working folder: {root}", f"Git repos found: {len(repos)}" + (" (stopped looking at the limit)" if len(repos) >= MAX_REPOS else "")]
    for r in repos:
        rel = os.path.relpath(r, root)
        out.append(f"\n## {rel}  (branch: {git(r, 'branch', '--show-current') or 'detached HEAD'})")
        out.append("Status:\n" + (git(r, "status", "--short") or "clean"))
        out.append("Diff:\n" + (git(r, "diff", "--stat") or "none"))
        out.append("Recent commits:\n" + (git(r, "log", "--oneline", "-5") or "none"))
    return "\n".join(out)


def turns(transcript: Path):
    """(timestamp, who, text) for user prompts and Claude's replies; tool calls and results skipped."""
    for line in transcript.open(errors="ignore"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("isSidechain"):
            continue
        text = prompt_text(e)
        who = "user"
        if not text and e.get("type") == "assistant":
            content = (e.get("message") or {}).get("content")
            if isinstance(content, list):
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
            who = "claude"
        if text:
            yield (e.get("timestamp") or "")[:16].replace("T", " "), who, text


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9_.]+", text.lower()) if len(w) > 2}


def search(transcript: Path, query: str) -> list:
    """Best matches first: most distinct query words present; later turns win ties (newer = more current)."""
    q = words(query)
    scored = [(len(q & words(t[2])), i, t) for i, t in enumerate(turns(transcript))]
    return [t for n, i, t in sorted(scored, key=lambda x: (-x[0], -x[1])) if n][:TOP]


def main() -> None:
    if sys.argv[1:] == ["id"]:
        print(current_id(os.getcwd()) or "unknown")
        return
    if sys.argv[1:] == ["git"]:
        print(repo_report(Path(os.getcwd())))
        return
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    found = list(PROJECTS.glob(f"*/{sys.argv[1]}.jsonl"))
    if not found:
        sys.exit(f"No session {sys.argv[1]} in {PROJECTS}.")
    out, used = [], 0
    for when, who, text in search(found[0], sys.argv[2]):
        text = " ".join(text.split())
        text = text if len(text) <= PER_MATCH else text[:PER_MATCH] + " …"
        if used + len(text) > TOTAL:
            break
        used += len(text)
        out.append(f"[{when or '?'}] {who}: {text}")
    print("\n\n".join(out) or "No matches. Try other words.")


if __name__ == "__main__":
    main()
