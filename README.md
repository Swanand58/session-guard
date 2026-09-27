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

**1. Before installing session-guard**, save stats for your last 30 days:

```bash
python3 report.py before
```

**2. Install session-guard and work normally for ~30 days.**

**3. Then:**

```bash
python3 report.py after
```

It covers every day since step 1, so the two periods never overlap, and
prints them side by side:

```
                                           BEFORE        AFTER   CHANGE
Tokens per session (median)               333,670      210,400     -37% ✓
Cost per session (avg, $)                    9.89         4.10     -59% ✓
Cost per message ($)                        0.857        0.410     -52% ✓
...
```

(Example numbers.) `python3 report.py compare` shows it again later. Results
are saved in `before.json` / `after.json` (git-ignored).

Compare the **per-session and per-message** numbers: totals depend on how
much work you did. For tokens per session, look at the **median**: one huge
session can make the average very large.

The report reads only token counts, model names, timestamps and
slash-command names, never your messages. Jev blocks and overrides come from
`~/.claude/session-guard/events.jsonl` (numbers only). An override is
roughly a time Jev was wrong.
