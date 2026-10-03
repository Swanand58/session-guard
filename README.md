# session-guard

Stops Claude Code sessions from quietly getting expensive.

Every message re-sends the whole session, so a long session costs more per
message. session-guard:

- **warns you** when a session's context passes 150k, 300k and 500k tokens,
  with the dollar cost so far and the cost of your last message
- **(optional, Jev)** spots when you start a *new task* in a big session and
  stops that prompt once, so you can hand over to a fresh session instead
- **counts subagents**: their cost is in every number, and a subagent that
  grows past the same levels is told to wrap up and report back
- gives you **/handover** (save a short note) and **/continue** (resume from it)

Costs use GitHub Copilot AI-credit rates (edit `PRICES` in `guard.py`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash
```

That downloads session-guard to `~/.session-guard` and adds it to
`~/.claude/settings.json` (a backup is saved next to it first). It adds the
warning hook, a live cost meter in the status line, and the `/handover` and
`/continue` commands. Restart Claude Code afterwards.

```
💸 189k ctx ▰▱▱ · $5.39 · last msg $0.44 · 🧭 same task · /handover soon
```

- Already have a status line? It keeps showing, above ours.
- Run the same command again to update. It never adds anything twice.
- From a clone instead: `python3 install.py`.
- Uninstall: `python3 ~/.session-guard/install.py uninstall`. This puts your
  old status line back.

Keep handover notes out of commits, per repo, without touching `.gitignore`:

```bash
echo ".handover/" >> .git/info/exclude
```

<details><summary>Manual install (e.g. Windows)</summary>

1. Copy `commands/*.md` to `~/.claude/commands/`.
2. Merge into `~/.claude/settings.json`:

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         { "hooks": [{ "type": "command", "command": "python3 /FULL/PATH/TO/session-guard/guard.py", "timeout": 10 }] }
       ],
       "PostToolUse": [
         { "hooks": [{ "type": "command", "command": "python3 /FULL/PATH/TO/session-guard/guard.py", "timeout": 10 }] }
       ]
     },
     "statusLine": { "type": "command", "command": "python3 /FULL/PATH/TO/session-guard/statusline.py" }
   }
   ```

   To keep an existing status line, put its command after ours:
   `python3 .../statusline.py bash /path/to/yours.sh`

</details>

## Jev new-task check (optional, off by default)

Add these two lines to `~/.session-guard/.env` (in a clone: `.env` next to
`guard.py`, which is git-ignored):

```
TYPESAFE_API_KEY=your-key
SESSION_GUARD_JEV=1
```

Or set them as environment variables (`export TYPESAFE_API_KEY=...`) instead.

On macOS with Python from python.org, HTTPS calls fail with
`CERTIFICATE_VERIFY_FAILED` until you run
`/Applications/Python 3.12/Install Certificates.command` once. The Jev check
then silently does nothing, so run that, or point the hook at Homebrew's `python3`.

Only runs when context is above 80k tokens. It sends your last 5 prompts and
the new prompt (truncated) to TypeSafe's API. **Check your
company's policy before enabling this on work code.** If Jev is slow (>4s) or
down, the prompt goes through normally.

## Daily use

1. Work normally. A 💸 warning appears when the session gets big.
2. At a good stopping point: `/handover`, then `/clear`, then `/continue`.
3. The note records the old session's id. If the new session needs a detail the
   note left out, it searches the old transcript (up to 5 short matches):
   `python3 ~/.claude/session-guard/recall.py <session-id> "a few words"`

## Subagents

A subagent starts with a fresh context and has its own transcript, so:

- **Cost:** the warnings and the status line add subagent cost to the session
  total. The status line shows their part: `$4.61 (agents $0.67)`. The context
  number stays the main session's own, because that is what gets re-sent.
- **Size:** if a subagent's own context passes 150k, 300k or 500k tokens, it
  gets one note per level telling it to finish its step and report back. A
  subagent cannot hand over, so that is the cheapest way out. This uses a
  `PostToolUse` hook, which also runs (and exits straight away) on the main
  session's tool calls.
- **Report:** `report.py` shows subagent runs and their share of the cost.

Already installed? Run the install command again to add the subagent hook.

## Test

```bash
python3 test_guard.py
```

## Benchmark: before vs after

It reads both **Claude Code** (`~/.claude/projects`) and **GitHub Copilot CLI**
(`~/.copilot/session-state`) sessions. For Copilot CLI it uses the AI credits
GitHub actually billed, when the session log has them. Copilot CLI only logs
totals per session, so the context-size rows (marked `*`) cover Claude Code only.

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

## Troubleshooting

- **Python 3.9 or newer.** No packages to install.
- **Windows:** use `python` (or `py`) instead of `python3` in the settings,
  with a full `C:\...` path. Use Windows Terminal for colours and emoji.
- **Nothing shows at all?** Run `/hooks` in Claude Code. If your company
  manages Claude Code settings, it may block personal hooks or status lines.
- **Status line is empty?** Your Claude Code setup may not record token
  counts in its transcripts (some Copilot API proxies don't). Check one
  transcript line for `"usage"`.
- **Jev never shows (🧭)?** A company firewall may block `api.typesafe.ai`.
  session-guard then carries on without it.
- Only Claude Code shows warnings and the status line. For Copilot CLI,
  `report.py` reads its sessions, but it has no live warnings.

## License

MIT. See [LICENSE](LICENSE).
