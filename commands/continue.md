---
description: Continue a task from the handover note
allowed-tools: Bash(git branch:*), Bash(git status:*), Bash(cat:*)
---
Handover note:
!`cat .handover/HANDOVER.md 2>/dev/null || echo "NO HANDOVER FILE FOUND"`

Current git state:
- Branch: !`git branch --show-current`
- Status: !`git status --short`

If there is no handover file, say so and stop.

If the branch differs from the note, or the changed files look very
different, warn me before doing anything.

Do NOT explore the codebase. Only open files the note mentions, and only
when you need them.

Reply with:
1. A 3-line summary: the task, where we are, the next step.
2. Any mismatch you found.
3. "Shall I start on: <next step>?"

Then wait for my answer.
