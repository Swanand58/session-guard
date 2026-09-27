---
description: Save a short handover note so a fresh session can continue this task
allowed-tools: Bash(git branch:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Bash(mkdir:*), Write
---
Current git state:
- Branch: !`git branch --show-current`
- Status: !`git status --short`
- Diff: !`git diff --stat`
- Recent commits: !`git log --oneline -5`

Write a handover note for the current task so a NEW session can continue it
without this conversation. Save it to `.handover/HANDOVER.md` in the project
root (create the folder; overwrite any old file).

Keep it under 40 lines. Be specific: file paths, function names, exact
commands. No filler. Use this format:

# Handover
**Task:** <one line>
**Branch:** <branch>
**Status:** <done / in progress / blocked>

## Done
- <finished work, with file paths>

## Decisions
- <important choices and WHY>

## Tried and failed
- <approaches that did not work, so the next session doesn't repeat them>

## Current state
- Changed files: <from git>
- Tests: <last known result and the command to run them>

## Next steps
1. <the very next concrete action>

## Watch out
- <gotchas, tricky code, things not to touch>

Then reply only: "Handover saved. Run /clear, then /continue."
