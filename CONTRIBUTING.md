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

Releases are automatic. To make one:

1. In a pull request, change the version in the `VERSION` file (one line, like
   `0.2.0`) and add a `## [0.2.0]` section to `CHANGELOG.md` with what changed.
   A test fails the pull request if the changelog has no section for `VERSION`.
2. Merge it to `main`.

On every push to `main`, the release workflow reads `VERSION`. If there is no
tag for that version yet, it runs the tests, takes the release notes from that
section of `CHANGELOG.md`, and creates the tag (`v0.2.0`) and the GitHub release.
If the tag already exists, it does nothing, so ordinary merges release nothing.
A version with a hyphen (for example `0.2.0-rc1`) is marked as a pre-release.
The installer picks up the latest release by itself. To run the workflow by
hand, use "Run workflow" on the release workflow in the Actions tab.
