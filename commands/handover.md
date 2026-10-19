---
description: Save a short handover note so a fresh session can continue this task
allowed-tools: Bash(git branch:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(mkdir:*), Bash(python3 ~/.claude/session-guard/recall.py:*), Write
---
Git repos in this folder (one block per repo, path relative to the working folder):
!`python3 ~/.claude/session-guard/recall.py git`

- This session: !`python3 ~/.claude/session-guard/recall.py id`

Write a handover note for the current task so a NEW session can continue it
without this conversation. Save it to `.handover/HANDOVER.md` in the project
root, which is the folder this session was started in (create the folder;
overwrite any old file). That folder may hold several git repos. Say exactly
which repo is where, and start every file path with its repo path.

Keep it under 40 lines. The next session can search this session's full
transcript for details, so keep only what it needs to start. Be specific:
file paths, function names, exact commands. No filler. Use this format:

# Handover
**Task:** <one line>
**Status:** <done / in progress / blocked>
**Previous session:** <"This session" id from above>

## Repos
Working folder: <absolute path from above>
- `<repo path relative to the working folder>`: <what it is, in a few words>. Branch `<branch>`. <touched in this task / not touched>
(one line per repo found; if the folder is itself the only repo, list it as `.`)

## Done
- <finished work, with file paths that start with the repo path, e.g. `api/src/auth.py`>

## Decisions
- <important choices and WHY>

## Tried and failed
- <approaches that did not work, so the next session doesn't repeat them>

## Current state
- Changed files per repo: <from git>
- Tests: <last known result and the command to run them>

## Next steps
1. <the very next concrete action>

## Watch out
- <gotchas, tricky code, things not to touch>

Then reply only: "Handover saved. Run /clear, then /continue."
