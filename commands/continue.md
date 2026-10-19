---
description: Continue a task from the handover note
allowed-tools: Bash(git branch:*), Bash(git status:*), Bash(cat:*), Bash(python3 ~/.claude/session-guard/recall.py:*)
---
Handover note:
!`cat .handover/HANDOVER.md 2>/dev/null || echo "NO HANDOVER FILE FOUND"`

Git repos in this folder now (path relative to the working folder):
!`python3 ~/.claude/session-guard/recall.py git`

If there is no handover file, say so and stop.

Compare each repo in the note's "Repos" section with the list above. If a
repo's branch differs, a repo is missing or new, or the changed files look
very different, warn me before doing anything.

Do NOT explore the codebase. Only open files the note mentions, and only
when you need them.

If you later need a detail the note left out (why a choice was made, an
exact error, a command), search the previous session instead of guessing:
`python3 ~/.claude/session-guard/recall.py <Previous session id> "<a few words>"`
It returns up to 5 short, timestamped matches. The old session also holds
ideas that were dropped later: when it disagrees with the note, trust the note.

Reply with:
1. A 3-line summary: the task, where we are, the next step.
2. Any mismatch you found.
3. "Shall I start on: <next step>?"

Then wait for my answer.
