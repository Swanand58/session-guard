# Security

## What session-guard does on your machine

session-guard installs a hook that runs before each prompt and after tool calls
in Claude Code, and a status line that runs on every update. Both are local
Python scripts (standard library only) that run with your user's permissions.

It touches:

- your Claude Code transcripts (`~/.claude/projects`), which it reads;
- `~/.claude/settings.json`, which the installer edits (a backup is saved first);
- `~/.claude/commands`, where it adds `/handover` and `/continue`;
- `~/.claude/session-guard`, where it keeps small state files, a log of numbers,
  reports and the session pages (the pages are readable only by you);
- `~/.session-guard` (its own files) and a link in `~/.local/bin`.

It sends nothing over the network and has no telemetry. The one optional network
feature is the Jev new-task check. It is off by default. When you turn it on, it
sends your last 5 prompts and the new one, each cut short, to `api.typesafe.ai`.
The installer and `session-guard update` download session-guard from GitHub.

## Reporting a vulnerability

Please report it privately. Use the **Report a vulnerability** button on the
[Security tab](https://github.com/Swanand58/session-guard/security) of this
repository (GitHub private vulnerability reporting). Please do not open a public
issue for a security problem.

Include what you found, how to reproduce it and which version
(`session-guard version`). You will get a reply as soon as the maintainer can
manage; this is a small project run in spare time.
