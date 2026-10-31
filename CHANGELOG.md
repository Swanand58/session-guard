# Changelog

All notable changes to this project are written here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `cache` status line segment: prompt-cache hit rate of the main session's last
  3 calls and the cause of the last real miss, from [cachekit](https://github.com/Swanand58/cache-kit) when it is
  installed and the session goes through its proxy. Hidden otherwise.

## [0.1.0]

First tagged release.

### Added
- Warnings when a session's context passes 60%, 80% and 90% of the model's
  context window, with the cost so far and the cost of your last message.
  The levels and the window size can be changed in `.env`.
- Each warning names what grew the context (files read, command output, web
  results, subagent results). These are estimates; the context size is exact.
- Subagents: their cost is added to the session total, and a subagent that
  grows past the same levels shows you a warning.
- A status line with a bar, a guess of how many messages are left
  before the next level, the cost and a `/handover` hint. An existing status line
  keeps working.
- Status line themes: five presets, your own colours, bar style, emoji and
  layout, with a picker in the terminal (`session-guard theme`).
- The session page (`session-guard view`): a local HTML file with the context
  growth chart, the biggest grabs, the cost and a theme studio.
- `/handover` and `/continue` commands to save a short note and resume from it
  in a fresh session, including folders that hold several git repos.
- `session-guard recall` to search an earlier session, and `session-guard repos`.
- An optional new-task check (Jev, off by default).
- A before/after cost report (`session-guard report`).
- One-line installer, `session-guard update` and `session-guard uninstall`
  (with `--purge`). The installer uses the latest release; set
  `SESSION_GUARD_REF` to install a given tag or branch.
- `session-guard version` (and `--version`); `session-guard status` shows the
  version in its first line.
