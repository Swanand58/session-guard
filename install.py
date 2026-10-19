#!/usr/bin/env python3
"""Add session-guard to Claude Code, or take it out again.

    python3 install.py                      # hook, status line, /handover, /continue, recall.py, the command
    python3 install.py uninstall            # undo it; your reports and logs are kept
    python3 install.py uninstall --purge    # undo it and delete session-guard's files, reports and logs

After installing, use the `session-guard` command (see README).

Safe to run twice. Backs up ~/.claude/settings.json before changing it.
An existing status line keeps working: ours wraps it (see statusline.py).
Jev is left as it is; this only prints how to switch it.
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
DATA = CLAUDE / "session-guard"  # state, logs and reports
RECALL = DATA / "recall.py"  # fixed path the commands call; links back here
BIN = Path.home() / ".local" / "bin" / "session-guard"  # the command; links back to cli.py
HOOK = f"python3 {shlex.quote(str(HERE / 'guard.py'))}"
EVENTS = ("UserPromptSubmit", "PostToolUse")  # PostToolUse: guard.py watches subagents
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


def our_link(path: Path) -> bool:
    """True for a symlink to some session-guard's cli.py (this install or an older location)."""
    target = path.resolve()
    return path.is_symlink() and target.name == "cli.py" and (target.parent / "guard.py").exists()


def link_cli() -> str:
    """Put `session-guard` on the user's PATH (a symlink in ~/.local/bin). Returns a note to print, or ''."""
    cli = HERE / "cli.py"
    cli.chmod(0o755)
    if BIN.exists() or BIN.is_symlink():
        if not our_link(BIN):
            return f"  skipped {BIN}: something else is already there"
        BIN.unlink()
    BIN.parent.mkdir(parents=True, exist_ok=True)
    BIN.symlink_to(cli)
    if str(BIN.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        return f'  {BIN.parent} is not on your PATH yet. Add this to your shell profile:\n    export PATH="$HOME/.local/bin:$PATH"'
    return ""


def baseline() -> None:
    """Save the last 30 days as the 'before' numbers for `session-guard report`. Once; never blocks the install."""
    try:
        import report
        if not report.data_files()[0].exists():
            report.save_before()
            print("  saved your last 30 days as the 'before' baseline (see: session-guard report)")
    except Exception as e:
        print(f"  could not save the 'before' baseline ({e}); run: session-guard report before")


def install() -> None:
    settings = load()
    before = json.dumps(settings)
    for event in EVENTS:
        groups = settings.setdefault("hooks", {}).setdefault(event, [])
        if not any(ours(h.get("command", ""), "guard.py") for g in groups for h in g.get("hooks", [])):
            groups.append({"hooks": [{"type": "command", "command": HOOK, "timeout": 10}]})
    old = settings.get("statusLine") or {}
    if not ours(old.get("command", ""), "statusline.py"):
        SAVED_STATUSLINE.parent.mkdir(parents=True, exist_ok=True)
        SAVED_STATUSLINE.write_text(json.dumps(old))
        wrap = f" sh -c {shlex.quote(old['command'])}" if old.get("command") else ""
        settings["statusLine"] = {**old, "type": "command", "command": STATUSLINE + wrap}
    save(settings, before)

    RECALL.parent.mkdir(parents=True, exist_ok=True)
    RECALL.unlink(missing_ok=True)
    RECALL.symlink_to(HERE / "recall.py")
    path_note = link_cli()

    (CLAUDE / "commands").mkdir(parents=True, exist_ok=True)
    for src in sorted((HERE / "commands").glob("*.md")):
        dst = CLAUDE / "commands" / src.name
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            # An older version of ours or your own edit: replace it, but keep the old one (Claude Code ignores .bak).
            backup = dst.with_name(f"{dst.name}.{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copy2(dst, backup)
            print(f"  updated {dst}; the old one is saved as {backup.name}")
        shutil.copyfile(src, dst)

    baseline()
    print(f"""session-guard is installed. Restart Claude Code to load it.
{path_note}
Check it any time: session-guard status

New-task check (Jev): session-guard jev on / off

Uninstall: session-guard uninstall   (add --purge to delete its files, reports and logs too)""")


def uninstall(purge: bool = False) -> None:
    settings = load()
    before = json.dumps(settings)
    hooks = settings.get("hooks") or {}
    for event in EVENTS:
        groups = hooks.get(event) or []
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks", []) if not ours(h.get("command", ""), "guard.py")]
        groups[:] = [g for g in groups if g.get("hooks")]
        if event in hooks and not groups:
            del hooks[event]
    if "hooks" in settings and not hooks:
        del settings["hooks"]
    if ours((settings.get("statusLine") or {}).get("command", ""), "statusline.py"):
        original = json.loads(SAVED_STATUSLINE.read_text()) if SAVED_STATUSLINE.exists() else {}
        if original:
            settings["statusLine"] = original
        else:
            del settings["statusLine"]
    SAVED_STATUSLINE.unlink(missing_ok=True)
    if RECALL.is_symlink() and RECALL.resolve() == (HERE / "recall.py").resolve():
        RECALL.unlink()
    save(settings, before)
    if our_link(BIN):
        BIN.unlink()

    for src in (HERE / "commands").glob("*.md"):
        dst = CLAUDE / "commands" / src.name
        if dst.exists() and dst.read_bytes() == src.read_bytes():  # never delete one you edited
            dst.unlink()
    if not purge:
        print(f"session-guard is removed. Restart Claude Code.\nYour reports and logs are kept in {DATA}. "
              f"To delete them and {HERE} too: python3 {shlex.quote(str(HERE / 'install.py'))} uninstall --purge")
        return
    shutil.rmtree(DATA, ignore_errors=True)
    # Only delete the folder the one-line installer made, never a clone you are developing in.
    if HERE == Path.home() / ".session-guard":
        shutil.rmtree(HERE, ignore_errors=True)
        print("session-guard is removed, with its files, reports and logs. Restart Claude Code.")
    else:
        print(f"session-guard is removed, with its reports and logs. Restart Claude Code. {HERE} was left alone; delete it yourself.")


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("session-guard needs Python 3.9 or newer.")
    args = sys.argv[1:]
    if args[:1] == ["uninstall"]:
        uninstall(purge="--purge" in args[1:])
    else:
        install()
