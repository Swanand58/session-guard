# session-guard

Stops Claude Code sessions from quietly getting expensive.

Every message re-sends the whole session, so a long session costs more per
message. session-guard:

- **warns you** when a session's context passes 150k, 300k and 500k tokens,
  with the dollar cost so far and the cost of your last message
- **(optional, Jev)** spots when you start a *new task* in a big session and
  stops that prompt once, so you can hand over to a fresh session instead
- gives you **/handover** (save a short note) and **/continue** (resume from it)

Costs use GitHub Copilot AI-credit rates (edit `PRICES` in `guard.py`).

## Install

1. Copy the commands:

   ```bash
   mkdir -p ~/.claude/commands && cp commands/*.md ~/.claude/commands/
   ```

2. Add the hook to `~/.claude/settings.json` (merge with existing `hooks`):

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         { "hooks": [{ "type": "command", "command": "python3 /FULL/PATH/TO/session-guard/guard.py", "timeout": 10 }] }
       ]
     }
   }
   ```

3. (Optional) Live cost meter in the status line at the bottom of Claude Code:

   ```
   💸 189k ctx ▰▱▱ · $5.39 · last msg $0.44 · 🧭 same task · /handover soon
   ```

   ```json
   { "statusLine": { "type": "command", "command": "python3 /FULL/PATH/TO/session-guard/statusline.py" } }
   ```

   Already have a status line? Put its command after ours and both show,
   yours on top: `python3 .../statusline.py bash /path/to/yours.sh`

4. Keep handover notes out of commits, per repo, without touching `.gitignore`:

   ```bash
   echo ".handover/" >> .git/info/exclude
   ```

## Jev new-task check (optional, off by default)

```bash
export TYPESAFE_API_KEY=...
export SESSION_GUARD_JEV=1
```

Or put those two lines in a `.env` file next to `guard.py` (git-ignored).

On macOS with Python from python.org, HTTPS calls fail with
`CERTIFICATE_VERIFY_FAILED` until you run
`/Applications/Python 3.12/Install Certificates.command` once. The Jev check
then silently does nothing, so run that, or point the hook at Homebrew's `python3`.

Only runs when context is above 80k tokens. It sends your first prompt, last
3 prompts and the new prompt (truncated) to TypeSafe's API. **Check your
company's policy before enabling this on work code.** If Jev is slow (>4s) or
down, the prompt goes through normally.

## Daily use

1. Work normally. A 💸 warning appears when the session gets big.
2. At a good stopping point: `/handover`, then `/clear`, then `/continue`.

## Test

```bash
python3 test_guard.py
```

## Benchmark: before vs after

`report.py` reads your Claude Code transcripts and reports cost, context
size and how session-guard was used for a date range. It reads only token
counts, model names, timestamps and slash-command names, never prompt text.

**1. Keep your history.** Claude Code deletes transcripts after 30 days by
default. Add this to `~/.claude/settings.json` **before** you start:

```json
{ "cleanupPeriodDays": 365 }
```

**2. Save a baseline before installing** (the last 30 days):

```bash
python3 report.py --since 2026-09-01 --until 2026-09-30 --json before.json
```

**3. Use session-guard for a few weeks, then:**

```bash
python3 report.py --since 2026-10-01 --until 2026-10-31 --json after.json
python3 report.py --compare before.json after.json
```

Compare **cost per message** and **cost per active day**, not total cost,
since the amount of work differs between periods.

### A fairer test: on/off weeks

A plain before/after mixes session-guard's effect with everything else that
changed (different tasks, models, deadlines). Alternating weeks is fairer.
In `.env`:

```bash
SESSION_GUARD_OFF=1   # this week: no warnings, no Jev, no status line
```

Remove it the next week, and repeat for 4+ weeks. Then run the report for
the ON weeks and the OFF weeks and compare them.

### What the numbers mean

| Metric | Why it matters |
|---|---|
| Cost per message | Main outcome: is each message cheaper? |
| Share of cost at >300k context | Money spent in very long chats (the waste session-guard targets) |
| Median / 90th percentile context | Are chats staying smaller? |
| `/handover` used | Are people actually following the advice? |
| Jev blocks vs. blocks you overrode | Overrides ≈ times Jev was wrong. Lower is better. |

`~/.claude/session-guard/events.jsonl` logs each warning, Jev check, block
and override (numbers only).
