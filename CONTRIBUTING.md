# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Run the tests

```bash
session-guard test        # or, from a clone:
python3 test_guard.py
```

It prints `all good` at the end. The tests need only Python 3.9 or newer (the
picker tests use a pseudo-terminal, and a few checks use `git`, `bash` and
`node` when they are installed).

## Rules

- **Standard library only.** No packages to install.
- **Python 3.9 must keep working** (it is the macOS default). Keep
  `from __future__ import annotations` at the top of each module.
- **Plain `assert` tests** in `test_guard.py`. No test framework.
- **Small pull requests**: one change at a time, with a test for it.
- **No prompt text in logs or pages.** The event log, the state files and the
  session page hold numbers, tool names and file base names only. Never prompts,
  commands, file contents, paths or URLs.

## Try a change safely

Use a temporary `HOME`, so your real `~/.claude/settings.json` and
`~/.claude/session-guard` are never touched:

```bash
export HOME="$(mktemp -d)"
python3 install.py
python3 cli.py status
python3 install.py uninstall --purge
```

The tests already do this for you.

## Releases

1. Change `VERSION` in `cli.py` and add an entry for it to `CHANGELOG.md`.
2. Merge to `main`.
3. Tag it and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.

The release workflow checks that the tag matches `VERSION`, runs the tests and
creates the GitHub release with generated notes. A tag with a hyphen (for
example `v0.2.0-rc1`) is marked as a pre-release; set `VERSION` to the same
text (`0.2.0-rc1`) for it. The installer picks up the latest release by itself.
