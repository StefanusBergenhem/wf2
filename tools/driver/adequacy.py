"""Convergence — reading close-time adequacy verdicts and parking a capability.

Nobody declares a capability done; the close-time gate detects it. This module
reads the digests ``wf-adequacy`` leaves on disk (``adequacy-<cap>-<question>-<utc>.md``)
and answers two mechanical questions: what did the newest full-promise review say,
and how many inadequate verdicts has this capability collected in a row.

The consecutive count is **derived from the digests**, not stored: three in a row
parks the capability for a PO session, which is a text-surgical edit of the
capabilities file — comments, ordering and unicode survive, and a no-op edit is not
written at all (L-106).
"""
from __future__ import annotations

import re

PARK_THRESHOLD = 3
FULL_PROMISE = "full-promise"

_HEAD_RE = re.compile(r"^#\s*Adequacy:\s*(CAP-\d+)\s*[—\-–:]+\s*(\w+)", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^(\s*)-\s")
_ENTRY_ID_RE = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?([\w.-]+)")
_STATUS_RE = re.compile(r"^(\s*status:\s*)([^\s#]+)(.*)$")


def digests(cfg, capability: str, question: str = FULL_PROMISE) -> list:
    """Every digest for this capability and question, oldest first — the stamped
    filename orders them."""
    cache = cfg.path_opt("drill_cache")
    if not cache or not cache.is_dir():
        return []
    return sorted(cache.glob(f"adequacy-{capability}-{question}-*.md"))


def verdict_of(path):
    """The verdict on the digest's own heading, lower-cased; None when unreadable."""
    if path is None or not path.is_file():
        return None
    head = _HEAD_RE.search(path.read_text(errors="replace"))
    return head.group(2).lower() if head else None


def newest_digest(cfg, capability: str, question: str = FULL_PROMISE):
    hits = digests(cfg, capability, question)
    return hits[-1] if hits else None


def consecutive_inadequate(cfg, capability: str) -> int:
    """The trailing run of inadequate full-promise verdicts. Only the full-promise
    question counts: a proposed-set review judges a scenario set at authoring time, not
    the promise as shipped, so it can never say the promise is unprovable."""
    count = 0
    for path in reversed(digests(cfg, capability)):
        if verdict_of(path) == "inadequate":
            count += 1
        else:
            break
    return count


def should_park(cfg, capability: str) -> bool:
    return consecutive_inadequate(cfg, capability) >= PARK_THRESHOLD


def park_capability(cfg, capability: str) -> bool:
    """Mark the capability ``status: parked`` in the capabilities file. Returns True
    when the file changed."""
    path = cfg.path_opt("capabilities")
    if not path or not path.exists():
        return False
    text = path.read_text()
    new_text = _set_status(text, capability, "parked")
    if new_text is None or new_text == text:
        return False
    path.write_text(new_text)
    return True


def _set_status(text: str, entry_id: str, status: str):
    """Rewrite one list entry's ``status:`` as TEXT. Round-tripping the file through
    a YAML dumper would strip its header comments and normalise its unicode, so the
    edit is line-level. Returns None when no such entry exists."""
    lines = text.splitlines(keepends=True)
    for span_start, span_end, block_indent in _blocks(lines):
        ident = next((m.group(1) for m in
                      (_ENTRY_ID_RE.match(b) for b in lines[span_start:span_end]) if m),
                     None)
        if ident != entry_id:
            continue
        field_indent = " " * (block_indent + 2)
        for i in range(span_start, span_end):
            m = _STATUS_RE.match(lines[i])
            if m:
                if m.group(2) == status:
                    return text
                lines[i] = f"{m.group(1)}{status}{m.group(3)}\n"
                return "".join(lines)
        lines.insert(span_start + 1, f"{field_indent}status: {status}\n")
        return "".join(lines)
    return None


def _blocks(lines):
    """[(start, end, indent)] for every top-level list entry in the document."""
    out, i = [], 0
    while i < len(lines):
        m = _LIST_ITEM_RE.match(lines[i])
        if not m:
            i += 1
            continue
        indent = len(m.group(1))
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                break
            j += 1
        out.append((i, j, indent))
        i = j
    return out
