#!/usr/bin/env python3
"""session-guard: one command for everything.

    session-guard install                  set up the hooks, status line, /handover and /continue
    session-guard uninstall [--purge]      remove them (--purge also deletes its files, reports and logs)
    session-guard update                   get the latest version
    session-guard status                   what is installed and how it is set up
    session-guard report before|after|compare [--days N]
                                           your costs before vs after session-guard
    session-guard recall id                id of the current session
    session-guard recall <id> "words"      search an earlier session
    session-guard repos                    every git repo under this folder
    session-guard jev on                   turn the new-task check on; it asks for your TypeSafe key
    session-guard jev off                  turn it off
    session-guard test                     run the self-checks
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import getpass
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # cli.py is symlinked into ~/.local/bin

import guard  # noqa: E402
import install  # noqa: E402

INSTALL_URL = "https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh"


def set_env(path: Path, **values: str) -> None:
    """Set KEY=value lines in a .env file, keeping the other lines. It can hold an API key, so owner-only."""
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in values.items():
        for i, line in enumerate(lines):
            if line.partition("=")[0].strip() == key:
                lines[i] = f"{key}={value}"
                break
        else:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o600)


def jev(args: list) -> None:
    env = HERE / ".env"
    if args[:1] == ["off"]:
        set_env(env, SESSION_GUARD_JEV="0")
        print("Jev new-task check is off.")
    elif args[:1] == ["on"]:
        guard.load_env(env)
        saved = os.environ.get("TYPESAFE_API_KEY")
        key = args[2] if args[1:2] == ["--key"] and len(args) > 2 else None
        if not key and sys.stdin.isatty():
            keep = " (Enter keeps the saved one)" if saved else ""
            key = getpass.getpass(f"Paste your TypeSafe API key{keep}. Typing is hidden: ").strip() or saved
        key = key or saved  # not a terminal (a script): use the saved key, if any
        if not key:
            sys.exit("No key given. Nothing changed. (Non-interactive: session-guard jev on --key YOUR_KEY)")
        set_env(env, TYPESAFE_API_KEY=key, SESSION_GUARD_JEV="1")
        print("Jev new-task check is on. It sends your last 5 prompts (truncated) to TypeSafe's API"
              " when a session is big. Check your company's policy first.")
    else:
        sys.exit(__doc__)


def status() -> None:
    guard.load_env(HERE / ".env")
    settings = install.load()
    hooks = settings.get("hooks") or {}
    has = lambda event: any(install.ours(h.get("command", ""), "guard.py")
                            for g in hooks.get(event, []) for h in g.get("hooks", []))
    yes = lambda ok: "yes" if ok else "NO"
    cmds = all((install.CLAUDE / "commands" / f.name).exists() for f in (HERE / "commands").glob("*.md"))
    import report
    before = report.data_files()[0]
    jev_on = os.environ.get("SESSION_GUARD_JEV") == "1" and bool(os.environ.get("TYPESAFE_API_KEY"))
    k = lambda ls: " ".join(f"{lvl // 1000}k" for lvl in ls)
    print(f"session-guard in {HERE}\n")
    print(f"  warning hook (prompts)   {yes(has('UserPromptSubmit'))}")
    print(f"  warning hook (subagents) {yes(has('PostToolUse'))}")
    print(f"  status line              {yes(install.ours((settings.get('statusLine') or {}).get('command', ''), 'statusline.py'))}")
    print(f"  /handover, /continue     {yes(cmds)}")
    print(f"  session-guard command    {yes(install.our_link(install.BIN))}")
    print(f"  'before' baseline        {'saved' if before.exists() else 'NOT saved (session-guard report before)'}")
    print(f"  warns at, 200k window    {k(guard.levels(200_000))}")
    print(f"  warns at, 1M window      {k(guard.levels(1_000_000))}")
    print(f"  Jev new-task check       {'on' if jev_on else 'off (session-guard jev on)'}")
    if not all([has("UserPromptSubmit"), has("PostToolUse"), cmds]):
        print("\nSomething is missing. Run: session-guard install")


def update() -> None:
    sys.exit(subprocess.call(f"curl -fsSL {INSTALL_URL} | bash", shell=True))


def main(argv: list | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    cmd, rest = (args[0], args[1:]) if args else ("help", [])
    if cmd == "install":
        install.install()
    elif cmd == "uninstall":
        install.uninstall(purge="--purge" in rest)
    elif cmd == "update":
        update()
    elif cmd == "status":
        status()
    elif cmd == "report":
        import report
        sys.argv = ["session-guard report", *rest]
        report.main()
    elif cmd == "recall":
        import recall
        sys.argv = ["session-guard recall", *rest]
        recall.main()
    elif cmd == "repos":
        import recall
        sys.argv = ["session-guard repos", "git"]
        recall.main()
    elif cmd == "jev":
        jev(rest)
    elif cmd == "test":
        sys.exit(subprocess.call([sys.executable, str(HERE / "test_guard.py")], cwd=HERE))
    else:
        print(__doc__)
        sys.exit(0 if cmd in ("help", "-h", "--help") else 2)


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("session-guard needs Python 3.9 or newer.")
    main()
