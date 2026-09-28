#!/usr/bin/env python3
"""Add session-guard to Claude Code, or take it out again.

    python3 install.py              # hook, status line, /handover and /continue
    python3 install.py uninstall    # undo it

Safe to run twice. Backs up ~/.claude/settings.json before changing it.
An existing status line keeps working: ours wraps it (see statusline.py).
Jev stays off; this only prints how to turn it on.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLAUDE = Path.home() / ".claude"
SETTINGS = CLAUDE / "settings.json"
SAVED_STATUSLINE = CLAUDE / "session-guard" / "original-statusline.json"  # restored by uninstall
HOOK = f"python3 {shlex.quote(str(HERE / 'guard.py'))}"
STATUSLINE = f"python3 {shlex.quote(str(HERE / 'statusline.py'))}"


def ours(command: str, script: str) -> bool:
    """True for our command, here or from an older manual install."""
    return str(HERE / script) in command or f"session-guard/{script}" in command


def load() -> dict:
    if not SETTINGS.exists() or not SETTINGS.read_text().strip():
        return {}
    try:
        return json.loads(SETTINGS.read_text())
    except ValueError:
        sys.exit(f"{SETTINGS} is not valid JSON. Fix it and run this again. Nothing was changed.")


def save(settings: dict, before: str) -> None:
    if json.dumps(settings) == before:
        return
    CLAUDE.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.{time.strftime('%Y%m%d-%H%M%S')}.bak")
        if not backup.exists():  # same second: keep the older one
            shutil.copy2(SETTINGS, backup)
            print(f"  backup: {backup}")
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    os.replace(tmp, SETTINGS)  # never leave a half-written settings.json


def install() -> None:
    settings = load()
    before = json.dumps(settings)
    groups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    if not any(ours(h.get("command", ""), "guard.py") for g in groups for h in g.get("hooks", [])):
        groups.append({"hooks": [{"type": "command", "command": HOOK, "timeout": 10}]})
    old = settings.get("statusLine") or {}
    if not ours(old.get("command", ""), "statusline.py"):
        SAVED_STATUSLINE.parent.mkdir(parents=True, exist_ok=True)
        SAVED_STATUSLINE.write_text(json.dumps(old))
        wrap = f" sh -c {shlex.quote(old['command'])}" if old.get("command") else ""
        settings["statusLine"] = {**old, "type": "command", "command": STATUSLINE + wrap}
    save(settings, before)

    (CLAUDE / "commands").mkdir(parents=True, exist_ok=True)
    for src in sorted((HERE / "commands").glob("*.md")):
        dst = CLAUDE / "commands" / src.name
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            print(f"  skipped {dst}: you already have a different one")
        else:
            shutil.copyfile(src, dst)

    print(f"""session-guard is installed. Restart Claude Code to load it.

Jev new-task check is OFF. To turn it on, add these two lines to {HERE / '.env'}:
    TYPESAFE_API_KEY=your-key
    SESSION_GUARD_JEV=1

Uninstall: python3 {shlex.quote(str(HERE / 'install.py'))} uninstall""")


def uninstall() -> None:
    settings = load()
    before = json.dumps(settings)
    hooks = settings.get("hooks") or {}
    groups = hooks.get("UserPromptSubmit") or []
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if not ours(h.get("command", ""), "guard.py")]
    groups[:] = [g for g in groups if g.get("hooks")]
    if "UserPromptSubmit" in hooks and not groups:
        del hooks["UserPromptSubmit"]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    if ours((settings.get("statusLine") or {}).get("command", ""), "statusline.py"):
        original = json.loads(SAVED_STATUSLINE.read_text()) if SAVED_STATUSLINE.exists() else {}
        if original:
            settings["statusLine"] = original
        else:
            del settings["statusLine"]
    SAVED_STATUSLINE.unlink(missing_ok=True)
    save(settings, before)

    for src in (HERE / "commands").glob("*.md"):
        dst = CLAUDE / "commands" / src.name
        if dst.exists() and dst.read_bytes() == src.read_bytes():  # never delete one you edited
            dst.unlink()
    print(f"session-guard is removed. Restart Claude Code. Delete {HERE} too if you no longer need it.")


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("session-guard needs Python 3.9 or newer.")
    uninstall() if sys.argv[1:] == ["uninstall"] else install()
