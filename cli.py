#!/usr/bin/env python3
"""session-guard: one command for everything.

    session-guard install                  set up the hooks, status line, /handover and /continue
    session-guard uninstall [--purge]      remove them (--purge also deletes its files, reports and logs)
    session-guard update                   get the latest version
    session-guard status                   what is installed and how it is set up (first line: the version)
    session-guard version                  print the version (also: --version)
    session-guard report before|after|compare [--days N]
                                           your costs before vs after session-guard
    session-guard recall id                id of the current session
    session-guard recall <id> "words"      search an earlier session
    session-guard repos                    every git repo under this folder
    session-guard jev on                   turn the new-task check on; it asks for your TypeSafe key
    session-guard jev off                  turn it off
    session-guard theme                    pick the look with the arrow keys (needs a terminal)
    session-guard theme show               print the theme and a preview
    session-guard theme list               every preset, previewed
    session-guard theme use <preset>       default | ocean | mono | high-contrast | colorblind
    session-guard theme set <key>=<value>  ok|warn|danger|dim=#rrggbb, bar, bar_width, emoji=on|off,
                                           mode, segments=ctx,bar|cost (| starts a new line)
    session-guard theme reset              back to the defaults
    session-guard theme apply '<json>'     set the whole theme from JSON
    session-guard view [--session ID] [--no-open]
                                           the session page: context chart, biggest grabs, cost, theme studio
    session-guard test                     run the self-checks
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # cli.py is symlinked into ~/.local/bin

import guard  # noqa: E402
import install  # noqa: E402
import themepicker  # noqa: E402
import ui  # noqa: E402

KEYS = (*ui.ROLES, "bar", "bar_width", "emoji", "mode", "segments")  # what `theme set` takes


def _version() -> str:
    """The text of the VERSION file next to this script (the release workflow reads the same file)."""
    try:
        return (HERE / "VERSION").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


VERSION = _version()
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


def preview(theme: dict, only: int | None = None) -> None:
    for line in themepicker.preview_lines(theme, only):
        print("  " + line)


def save_theme(raw: dict) -> None:
    path = ui.theme_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n")
    os.replace(tmp, path)  # atomic: the status line never reads half a file


def store(raw: dict) -> None:
    """Validate and save a theme. A bad theme exits with the reason and leaves the file alone."""
    clean, error = ui.validate(raw)
    if error:
        sys.exit(f"Not saved. {error}")
    save_theme(clean)


def change(raw: dict) -> None:
    store(raw)
    show()


def pick() -> None:
    """The picker, if there is a terminal for it; otherwise the plain view."""
    if not (themepicker.AVAILABLE and sys.stdin.isatty() and sys.stdout.isatty()):
        show()
        print("\nThe picker needs a terminal. Use: session-guard theme use <preset> / theme set key=value.")
        return
    new = themepicker.run(ui.load_raw())
    if new is None:
        print("Nothing changed.")
    else:
        store(new)
        print("Saved. Your status line updates on the next message.")


def show() -> None:
    raw = ui.load_raw()
    theme = ui.effective(raw)
    print(f"Theme file: {ui.theme_file()}{'' if ui.theme_file().exists() else ' (none yet, using defaults)'}\n")
    print(f"  preset    {theme['preset']}")
    for role in ui.ROLES:
        print(f"  {role:<9} {ui.paint('██', role, theme)} {theme['colors'][role]}")
    print(f"  bar       {theme['bar']}, {theme['bar_width']} wide")
    print(f"  emoji     {'on' if theme['emoji'] else 'off'}")
    print(f"  mode      {theme['mode']}")
    print(f"  segments  {' | '.join(', '.join(line) for line in theme['segments'])}")
    print("\nPreview (14%, 65% and 85% of a 1M window):")
    preview(theme)


def theme(args: list) -> None:
    if not args:
        return pick()
    cmd, rest = args[0], args[1:]
    raw = ui.load_raw()
    if cmd == "show":
        show()
    elif cmd == "list":
        for name in ui.PRESETS:
            print(name)
            preview(ui.effective({"preset": name}), only=2)
    elif cmd == "use" and len(rest) == 1:
        change({**{k: v for k, v in raw.items() if k != "colors"}, "preset": rest[0]})  # your own colours would hide the preset
    elif cmd == "set" and rest:
        new = dict(raw)
        for pair in rest:
            key, sep, value = pair.partition("=")
            key = key.strip()
            if not sep or key not in KEYS:
                sys.exit(f"Not saved. Use key=value, with key one of: {', '.join(KEYS)}")
            if key in ui.ROLES:
                new["colors"] = {**new.get("colors", {}), key: value}
            elif key == "bar_width":
                new[key] = int(value) if value.strip().lstrip("-").isdigit() else value
            elif key == "emoji":
                new[key] = {"on": True, "off": False}.get(value.strip().lower(), value)
            elif key == "segments":
                new[key] = [[n.strip() for n in line.split(",") if n.strip()] for line in value.split("|")]
            else:
                new[key] = value.strip()
        change(new)
    elif cmd == "reset" and not rest:
        ui.theme_file().unlink(missing_ok=True)
        show()
    elif cmd == "apply" and len(rest) == 1:
        try:
            data = json.loads(rest[0])
        except ValueError as e:
            sys.exit(f"Not saved. That is not valid JSON: {e}")
        change(data)
    else:
        sys.exit(__doc__)


def view(args: list) -> None:
    """Write the session page and open it in the browser."""
    import re
    import webbrowser

    import recall
    import view as page
    opts = [a for a in args if a != "--no-open"]
    if opts[:1] == ["--session"] and len(opts) == 2:
        sid = opts[1]
    elif opts[:1] and opts[0].startswith("--session=") and len(opts) == 1:
        sid = opts[0][len("--session="):]
    elif not opts:
        sid = recall.current_id(os.getcwd())
    else:
        sys.exit(__doc__)
    found = sorted(recall.PROJECTS.glob(f"*/{sid}.jsonl"), key=lambda f: f.stat().st_mtime) if sid and re.fullmatch(r"[\w.-]+", sid) else []
    if not found:
        sys.exit(f"No session found ({sid or 'no transcript for this folder'}) in {recall.PROJECTS}. Use: session-guard view --session ID")
    path = page.write(found[-1])
    print(path)
    if "--no-open" not in args and not webbrowser.open(path.as_uri()):
        print("Could not open a browser. Open the file above yourself.")


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
    print(f"session-guard {VERSION} in {HERE}\n")
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
    elif cmd in ("version", "--version"):
        print(f"session-guard {VERSION}")
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
    elif cmd == "theme":
        theme(rest)
    elif cmd == "view":
        view(rest)
    elif cmd == "test":
        sys.exit(subprocess.call([sys.executable, str(HERE / "test_guard.py")], cwd=HERE))
    else:
        print(__doc__)
        sys.exit(0 if cmd in ("help", "-h", "--help") else 2)


if __name__ == "__main__":
    if sys.version_info < (3, 9):
        sys.exit("session-guard needs Python 3.9 or newer.")
    main()
