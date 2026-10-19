# session-guard

Stops Claude Code sessions from quietly getting expensive.

Every message re-sends the whole session, so a long session costs more per
message. session-guard:

- **warns you** when a session's context passes 60%, 80% and 90% of the
  model's context window, with the dollar cost so far and the cost of your
  last message, and **what grew the context** (the biggest grabs)
- **(optional, Jev)** spots when you start a *new task* in a big session and
  stops that prompt once, so you can hand over to a fresh session instead
- **counts subagents**: their cost is in every number, and a subagent that
  grows past the same levels shows you a warning (it is not stopped)
- gives you **/handover** (save a short note) and **/continue** (resume from it)

Costs use GitHub Copilot AI-credit rates (edit `PRICES` in `guard.py`).

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash
```

That downloads session-guard to `~/.session-guard` and adds it to
`~/.claude/settings.json` (a backup is saved next to it first). It adds the
warning hook, a live cost meter in the status line, the `/handover` and
`/continue` commands, and the `session-guard` command (a link in
`~/.local/bin`). It also saves your last 30 days of costs as the "before"
numbers for the [benchmark](#benchmark-before-vs-after). Restart Claude Code
afterwards. Check it worked with `session-guard status`.

If your shell says `session-guard: command not found`, add `~/.local/bin` to
your PATH (the installer prints the line to add).

```
💸 150k ctx · ▰▰▰▰▰▰▰▰▱▱ 75% · ▃▄▅▆▇ · ~4 msgs to 160k · $5.39 · last msg $0.44 · 🧭 same task · /handover soon
```

The bar shows how full the context window is. The small chart shows how
context grew over the last few calls. `~4 msgs to 160k` is a guess: how many
more messages until the next warning level, at your recent pace. See
[Customize the status line](#customize-the-status-line) for colours and layout.

- Already have a status line? It keeps showing, above ours.
- Run the same command again, or `session-guard update`, to update. It never
  adds anything twice. If your `/handover` or `/continue` file differs from the
  new one, it is replaced and your old one is saved next to it as `*.bak`.
- From a clone instead: `python3 install.py`.

### Uninstall

```bash
session-guard uninstall            # undo everything it added; keeps your reports and logs
session-guard uninstall --purge    # also delete its files, reports and logs
```

Your old status line comes back. A settings backup is saved first. Lost the
command? This does the same from scratch:

```bash
curl -fsSL https://raw.githubusercontent.com/Swanand58/session-guard/main/install.sh | bash -s -- uninstall --purge
```

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

## Commands

| Command | What it does |
|---|---|
| `session-guard install` | Set up the hooks, status line, `/handover`, `/continue` and the command. Safe to run twice. |
| `session-guard uninstall [--purge]` | Remove them. `--purge` also deletes its files, reports and logs. |
| `session-guard update` | Get the latest version. |
| `session-guard status` | What is installed, when it warns, and whether Jev is on. |
| `session-guard report before\|after\|compare [--days N]` | Your costs before vs after. See [Benchmark](#benchmark-before-vs-after). |
| `session-guard recall id` | The id of the current session. |
| `session-guard recall <id> "words"` | Search an earlier session (up to 5 short matches). |
| `session-guard repos` | Every git repo under this folder, with branch and changes. |
| `session-guard jev on` / `off` | Turn the new-task check on (it asks for your key) or off. |
| `session-guard theme` | Open the picker: choose the status line colours and layout with the arrow keys. See [Customize the status line](#customize-the-status-line). |
| `session-guard theme show\|list\|use\|set\|reset\|apply` | Print or change the theme with commands, for scripts. |
| `session-guard view [--session ID] [--no-open]` | Open the page for this session (or another): context chart, biggest grabs, cost and a theme studio. See [The session page](#the-session-page). |
| `session-guard test` | Run the self-checks. |

Inside Claude Code: `/handover` saves a short note, `/continue` resumes from it.

## Customize the status line

Run this in a terminal and pick the look with the arrow keys. The preview at
the top (14%, 65% and 85% of a 1M window) changes as you move.

```bash
session-guard theme
```

| Key | What it does |
|---|---|
| `↑` `↓` (or `k` `j`) | Move between rows |
| `←` `→` (or `h` `l`) | Change a value: preset, bar style, bar width, emoji, clickable `ctx` |
| `Enter` or `Space` | On a colour: open the palette. On a segment: show or hide it. On Save or Cancel: do it. |
| `s` | Save |
| `q` or `Esc` | Cancel. Nothing is saved. |

In the palette, move with the arrow keys and press `Enter` to pick. `Esc` goes
back to the old colour. `#` lets you type your own hex colour. The last visible
segment cannot be hidden. The picker keeps the order of the segments. To change
the order, use `theme set segments=...` (below). Under "Show" the picker
lists every segment (`ctx`, `bar`, `eta`, `cost`, `last`, `jev`, `grab`, `hint`).

Without a terminal (in a script, or piped), `session-guard theme` prints the
current theme and a hint instead. To print it any time:

```bash
session-guard theme show
session-guard theme list          # every preset, previewed
```

**Presets** are ready-made colour sets: `default`, `ocean`, `mono`,
`high-contrast` and `colorblind` (blue, orange and magenta, no red/green pair).

```bash
session-guard theme use ocean
session-guard theme set warn=#ff9900          # one colour (the # is optional)
session-guard theme set bar=solid bar_width=14
session-guard theme set emoji=off
session-guard theme set link=off              # ctx is no longer a link (see The session page)
session-guard theme reset                     # back to the defaults
```

There are four colours: `ok`, `warn`, `danger` and `dim` (the separators and
empty bar cells). Other settings:

| Setting | Values |
|---|---|
| `bar` | `blocks` (▰▱), `solid` (█░) or `dots` (●○) |
| `bar_width` | 4 to 20 cells, default 10 |
| `emoji` | `on` or `off` |
| `link` | `on` (default) or `off`. When on, `ctx` opens [the session page](#the-session-page) in terminals that support links. It does not change the text. |
| `mode` | `auto`, `truecolor` or `256`. `auto` uses 24-bit colour when `COLORTERM` is `truecolor` or `24bit`, else the nearest of 256 colours. |
| `segments` | which parts to show, in which order (below) |

`theme use` replaces your own colours with the preset. A bad value prints what
is valid, exits with an error and changes nothing.

**NO_COLOR:** if the `NO_COLOR` environment variable is set to anything, the
status line has no colours at all.

**Segments** are the parts of the line, shown in the order you give:

| Name | Shows |
|---|---|
| `ctx` | context size, e.g. `💸 141k ctx` |
| `bar` | how full the window is, e.g. `▰▰▱▱▱▱▱▱▱▱ 14%`. Each cell is coloured by the zone it sits in: before the first warning level, between level 1 and 2, beyond. |
| `eta` | `~12 msgs to 160k`: tokens left to the next level, divided by how much context grew per message lately. Hidden until there are 2 messages, or if context is not growing. |
| `cost` | cost so far, with the subagents' part if it is $0.005 or more |
| `last` | cost of your last message |
| `jev` | the new-task check (only when Jev is on) |
| `grab` | `top: Read report.py +38k`: the biggest thing in the context. Only after the first warning level. See [What grew the context](#what-grew-the-context). |
| `hint` | `/handover soon` or `/handover now` |

A segment with nothing to show is skipped. To split the status line over two
lines, put `|` between the lines:

```bash
session-guard theme set 'segments=ctx,bar,eta|cost,last,jev,hint'
```

The theme is saved in `~/.claude/session-guard/theme.json`. The picker writes
the preset and only what differs from it. You can also set
all of it at once with JSON (this is the format any tool that builds a theme
should use):

```bash
session-guard theme apply '{"preset": "ocean", "bar": "dots", "segments": [["ctx", "bar"], ["cost", "last"]]}'
```

Every key is optional. If the file is missing or broken, the defaults are used. Segment names it does not know (for example `spark`, which was removed) are skipped.

## The session page

```bash
session-guard view                 # this folder's newest session, opened in your browser
session-guard view --session ID    # another session (`session-guard recall id` prints the current one)
session-guard view --no-open       # only write the file and print its path
```

It is one HTML file, `~/.claude/session-guard/view/<session id>.html`. The
design is minimal: one column, flat, no cards, mono type, heavy enough to read
at a glance. It opens **dark**. A small `dark · light · auto` switch at the top
right changes that, and the page remembers your choice (`auto` follows your
system). All text meets WCAG AAA contrast (7:1 or better). It shows:

- **Context now**, how full the window is, the warning levels (60%, 80%, 90%
  by default) and the cost: so far, subagents, last message.
- **Context growth:** a chart of the context at each call (the latest 200),
  with a line at each warning level and a mark where you ran `/compact`.
  Hover, touch or use the arrow keys to see what grew it at that call.
  The same numbers are in a table below the chart.
- **Biggest grabs:** the things that grew the context, biggest first, and the
  baseline (system prompt, tools, memory) shown apart. Rows under 1% of the
  context are hidden behind "show all". A tick box adds the conversation
  itself (replies and your messages). Sizes are estimates; the total is exact.
- **Theme studio**, folded away until you open "Theme" (or add `#theme` to the
  address): colour pickers, presets, bar style and width, emoji, the
  clickable `ctx`, and which segments to show, in which order, on how many
  lines. A live preview of the status line uses your real numbers at the
  current fill and at 65% and 85%. **Copy command** gives you a
  `session-guard theme apply '...'` line. A plain file page cannot write your
  settings, so you paste that line in a terminal.

**Clickable `ctx`.** With `link` on (the default), the `ctx` part of the status
line is a link to this page. In a terminal that supports links (Ghostty, iTerm2,
Kitty, WezTerm) hold Cmd (Ctrl on Linux) and click it. Terminal.app does not
support them; use `session-guard view` there. The status line writes the page
only when it is missing or older than the session, which added about 0.05
seconds to an update on a 21 MB transcript. If anything goes wrong, you get the
same status line without the link.

**Private and offline.** The page is a local file. It loads nothing from the
internet and sends nothing. It holds numbers, tool names and file base names
(`report.py`, not the path). It never holds your prompts, commands, file
contents or URLs. The file is readable only by you, and `session-guard
uninstall --purge` deletes it with the rest of the session-guard folder. The page
is a snapshot: reload the tab after your next message, or run the command again.
The page is rebuilt when the session, your theme or the page design is newer than
the file, so an update brings the new design on its next status line refresh.

## What grew the context

A warning also says what made the context grow, so you can change what you do:

```
💸 Session context is 630k tokens (63% of 1000k; ~$9.40 so far, last message ~$0.14). Biggest grabs (estimates): subagent results ×3 +41k (7%), Read report.py +38k (6%), Bash output ×4 +22k (4%); the conversation itself (replies and your messages) is 61%. Every message re-sends all of it. When you finish this step: /handover, then /clear and /continue.
```

- **It is an estimate.** The context size is exact. Every split is not: the
  growth between two calls is shared by the text length of what arrived
  (Claude's reply without its thinking, tool results, your messages). Read
  "+38k" as "about 38k".
- **What you can change comes first.** The list shows tool results (files read,
  command output, web results, subagent results). Claude's replies and your
  messages are not listed one by one; they appear as a closing "the
  conversation itself is N%" when together they are 10% or more.
- **Private.** It uses only tool names, file base names (`report.py`, not the
  path) and sizes. Never file contents, commands, prompts or URLs. It is shown
  on screen only and is not stored: `events.jsonl` still holds numbers only.
- Items under 3% of the context are left out. Nothing to show? The warning is
  the same as before. A drop in context (`/compact`) starts the count again.
- Subagent warnings list their top two, from the subagent's own transcript.
- The `grab` status line segment shows the single biggest tool-related item. It reads the
  transcript once more per update, which added about 0.03 to 0.07 seconds on a
  7 to 21 MB transcript.

## Configure

Warnings exist because every message re-sends the whole session. The fuller
the session, the more each message costs. The *context window* is how much
the model can hold at once.

By default you get a warning at 60%, 80% and 90% of the window. On a 200k
window that is 120k, 160k and 180k tokens. On a 1M window it is 600k, 800k
and 900k. The window size comes from Claude Code's status line. If that is
missing, session-guard assumes 200k, and switches to 1M once a call goes over
200k.
Keep the status line the installer adds: without it, 1M-window users get early warnings until a call passes 200k (or set `SESSION_GUARD_WINDOW`).

Want fewer or later warnings? Set the levels as shares of the window (three
numbers between 0 and 1, comma-separated):

```
SESSION_GUARD_LEVELS=0.8,0.9,0.95
```

If the window size is detected wrongly, set it in tokens:

```
SESSION_GUARD_WINDOW=1000000
```

Put these lines in `~/.session-guard/.env` (in a clone: `.env` next to
`guard.py`) or export them as environment variables. A bad value is ignored
and the defaults are used.

## Jev new-task check (optional, off by default)

```bash
session-guard jev on     # asks for your TypeSafe API key (typing is hidden), then turns it on
session-guard jev off    # turns it off; keeps the key
```

The key is saved in `~/.session-guard/.env` (in a clone: `.env` next to
`guard.py`, which is git-ignored), readable only by you. You can also set
`TYPESAFE_API_KEY` and `SESSION_GUARD_JEV=1` as environment variables instead.

On macOS with Python from python.org, HTTPS calls fail with
`CERTIFICATE_VERIFY_FAILED` until you run
`/Applications/Python 3.12/Install Certificates.command` once. The Jev check
then silently does nothing, so run that, or point the hook at Homebrew's `python3`.

Only runs when context is above 40% of the window, or 150k tokens, whichever is
lower (80k on a 200k window). It sends your last 5 prompts and
the new prompt (truncated) to TypeSafe's API. **Check your
company's policy before enabling this on work code.** If Jev is slow (>4s) or
down, the prompt goes through normally.

## Daily use

1. Work normally. A 💸 warning appears when the session gets big.
2. At a good stopping point: `/handover`, then `/clear`, then `/continue`.
3. The note records the old session's id. If the new session needs a detail the
   note left out, it searches the old transcript (up to 5 short matches):
   `session-guard recall <session-id> "a few words"`

### Folders with several repos

You can start Claude in a plain folder that holds your repos (not a repo
itself). `/handover` finds every git repo under that folder, at any depth, and
writes down which repo is where, with its branch. The note stays in the folder
you started in. `/continue` checks each repo's branch against the note. It
skips `node_modules`-style folders, hidden folders, and anything inside a repo,
and stops at 40 repos.

## Subagents

A subagent starts with a fresh context and has its own transcript, so:

- **Cost:** the warnings and the status line add subagent cost to the session
  total. The status line shows their part: `$4.61 (agents $0.67)`. The context
  number stays the main session's own, because that is what gets re-sent.
- **Size:** if a subagent's own context passes the same levels (60%, 80% and
  90% of the window), you get one warning per level with its context size and
  cost. The subagent is not told to stop; it carries on. This uses a
  `PostToolUse` hook, which also runs (and exits straight away) on the main
  session's tool calls.
- **Report:** `session-guard report` shows subagent runs and their share of the cost.

Already installed? Run the install command again to add the subagent hook.

## Test

```bash
session-guard test
```

## Benchmark: before vs after

It reads both **Claude Code** (`~/.claude/projects`) and **GitHub Copilot CLI**
(`~/.copilot/session-state`) sessions. For Copilot CLI it uses the AI credits
GitHub actually billed, when the session log has them. Copilot CLI only logs
totals per session, so the context-size rows (marked `*`) cover Claude Code only.

**1. Install session-guard.** It saves your last 30 days as the "before"
numbers for you. (Redo it any time with `session-guard report before`.)

**2. Work normally for ~30 days.**

**3. Then:**

```bash
session-guard report after
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

(Example numbers.) `session-guard report compare` shows it again later. Results
are saved in `~/.claude/session-guard/before.json` and `after.json`.

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
  with a full `C:\...` path. Use Windows Terminal for colours and emoji. The
  one-line installer needs bash (use WSL), or install by hand as above and run
  commands as `python cli.py status`, `python cli.py report after`, and so on.
- **Nothing shows at all?** Run `/hooks` in Claude Code. If your company
  manages Claude Code settings, it may block personal hooks or status lines.
- **Status line is empty?** Your Claude Code setup may not record token
  counts in its transcripts (some Copilot API proxies don't). Check one
  transcript line for `"usage"`.
- **Jev never shows (🧭)?** A company firewall may block `api.typesafe.ai`.
  session-guard then carries on without it.
- Only Claude Code shows warnings and the status line. For Copilot CLI,
  `session-guard report` reads its sessions, but it has no live warnings.

## License

MIT. See [LICENSE](LICENSE).
