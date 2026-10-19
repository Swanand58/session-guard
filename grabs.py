"""What made the context grow: a rough split of a session's context by where it came from.

    grabs(transcript, sidechain=False) -> {
        "context":  int,   # context size of the last call (exact)
        "baseline": int,   # tokens not attributed to anything below: system prompt, tools, memory files, summary
        "items":    [{"label": str, "tokens": int, "count": int}, ...],   # biggest first
        "calls":    [{"context": int, "delta": int, "label": str}, ...],  # last 60 calls; delta < 0 is a drop
    }
    baseline + sum of item tokens == context. An unreadable file gives zeros and empty lists.

The context total is exact. Every split is an ESTIMATE: the growth between two calls is shared by the text
length of what arrived (Claude's reply without its thinking, which is not re-sent; tool results; your
messages). Labels are tool names, file base names
and fixed phrases only, never commands, prompts, file contents or URLs. Nothing is stored.

Stdlib only.
"""

from __future__ import annotations  # Python 3.9 (macOS default) support

import json
from collections import deque
from pathlib import Path

import guard

REPLIES = "Claude's replies"
BLOCK_CHARS = 6000  # length assumed for an image or other non-text block
RESET_BELOW = 0.5  # context falling below half is a /compact or clear; a small drop is a model switch (other token counts)
CONVERSATION = (REPLIES, "your messages")  # not something the user can change, so describe() sets them apart
FILE_TOOLS = ("Read", "Edit", "Write", "NotebookEdit")
FIXED = {"Agent": "subagent results", "Task": "subagent results", "Bash": "Bash output",
         "WebFetch": "web results", "WebSearch": "web results"}


def label_for(name, tool_input) -> str:
    """Privacy-safe label of a tool's output."""
    name = str(name or "tool")[:60]
    if name in FILE_TOOLS and isinstance(tool_input, dict):
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        return f"{name} {str(path).replace(chr(92), '/').rsplit('/', 1)[-1][:60]}" if path else name
    if name in FIXED:
        return FIXED[name]
    return f"{name} results" if name in ("Grep", "Glob") else f"{name} output"


def _chars(content) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(b.get("text") or "") if isinstance(b, dict) and b.get("type") == "text" else BLOCK_CHARS for b in content)
    return 0


def _reply_chars(content) -> int:
    """Visible length of an assistant message: text and tool inputs. Thinking is not re-sent, so it is not counted."""
    if isinstance(content, str):
        return len(content)
    n = 0
    for b in content if isinstance(content, list) else []:
        kind = b.get("type") if isinstance(b, dict) else None
        n += (len(b.get("text") or "") if kind == "text" else len(json.dumps(b.get("input"))) if kind == "tool_use"
              else 0 if kind in ("thinking", "redacted_thinking") else BLOCK_CHARS)
    return n


def _add(items: dict, label: str, tokens: float) -> None:
    item = items.setdefault(label, [0.0, 0])
    item[0] += tokens
    item[1] += 1


def _grabs(path: Path, sidechain: bool) -> dict:
    seen, names, pending, items = set(), {}, [], {}  # pending: (label, chars) that arrived since the last call
    calls = deque(maxlen=60)
    prev = None  # context of the previous call
    reply = last_reply = 0  # visible chars of the latest response, and of the one before (the one that led to this gap)
    for e in guard.entries(path):
        try:
            if e.get("isSidechain") and not sidechain:
                continue
            content = (e.get("message") or {}).get("content")
            blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
            if e.get("type") == "user":
                pending += [(names.get(b.get("tool_use_id"), "tool output"), _chars(b.get("content")))
                            for b in blocks if b.get("type") == "tool_result"]
                text = guard.prompt_text({**e, "isSidechain": False})  # a subagent's own prompt counts too
                if text:
                    pending.append(("your messages", len(text)))
                continue
            for b in blocks:  # on every line, before de-duplication: each line of a split response has its own blocks
                if b.get("type") == "tool_use":
                    names[b.get("id")] = label_for(b.get("name"), b.get("input"))
            if e.get("type") != "assistant":
                continue
            call = guard.api_call(e, seen)
            chars = _reply_chars(content)
            if not call or call[0] <= 0:  # no usage, a repeated line, or a zero-usage placeholder: more of the same response
                reply += chars
                continue
            last_reply, reply = reply, chars
            context = call[0]
            delta, label = (context - prev, "") if prev else (0, "")
            if prev and delta <= 0 and context < RESET_BELOW * prev:  # /compact or clear: start again from this call
                items.clear()
                label = "compact"
            elif prev and delta <= 0:  # small drop: the same text counted differently, so keep the items and scale them
                for item in items.values():
                    item[0] *= context / prev
            elif prev:
                new = pending + [(REPLIES, last_reply)]  # all of it is shared by text length
                total = sum(chars for _, chars in new)
                parts = [(name, delta * chars / total) for name, chars in new if chars] if total else []
                for name, tokens in parts:
                    _add(items, name, tokens)
                label = max(parts, key=lambda p: p[1])[0] if parts else ""
            pending = []
            prev = context
            calls.append({"context": context, "delta": delta, "label": label})
        except Exception:
            continue  # odd line: skip it
    ranked = sorted(({"label": k, "tokens": round(v[0]), "count": v[1]} for k, v in items.items()), key=lambda i: -i["tokens"])
    ranked = [i for i in ranked if i["tokens"] > 0]
    context = prev or 0
    return {"context": context, "baseline": max(0, context - sum(i["tokens"] for i in ranked)), "items": ranked, "calls": list(calls)}


def grabs(transcript: Path, sidechain: bool = False) -> dict:
    """Where the context came from (see the top of this file). sidechain=True for a subagent's own transcript file."""
    try:
        return _grabs(Path(transcript), sidechain)
    except Exception:  # unreadable file
        return {"context": 0, "baseline": 0, "items": [], "calls": []}


def tool_items(g: dict) -> list:
    """g["items"] without the conversation itself (replies and your messages): what the user can change. g["items"] has all."""
    return [i for i in g.get("items", []) if i["label"] not in CONVERSATION]


def describe(g: dict, n: int = 3) -> str:
    """'Read report.py +38k (6%), Bash output ×4 +22k (4%); the conversation itself (replies and your messages) is 67%'.

    The top n tool-related items worth 3% or more of the context, then the conversation's share if it is 10% or more. '' if none.
    """
    context = g.get("context") or 0
    if not context:
        return ""
    main = ", ".join(f"{i['label']}{' ×%d' % i['count'] if i['count'] > 1 else ''} +{i['tokens'] / 1000:.0f}k ({i['tokens'] / context:.0%})"
                     for i in tool_items(g)[:n] if i["tokens"] >= 0.03 * context)
    talk = sum(i["tokens"] for i in g.get("items", []) if i["label"] in CONVERSATION) / context
    tail = f"the conversation itself (replies and your messages) is {talk:.0%}" if talk >= 0.1 else ""
    return "; ".join(x for x in (main, tail) if x)
