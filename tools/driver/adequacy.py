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
# How many countable full-promise rounds a capability gets before the remainder is
# re-homed as work rather than re-reviewed. Each round is a full adversarial source
# review at a stage close; dems spent eight on one capability and drained nothing.
ROUND_CAP = 3
FULL_PROMISE = "full-promise"

_HEAD_RE = re.compile(r"^#\s*Adequacy:\s*(CAP-\d+)\s*[—\-–:]+\s*(\w+)", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^(\s*)-\s")
_ENTRY_ID_RE = re.compile(r"^\s*(?:-\s+)?id:\s*[\"']?([\w.-]+)")
_STATUS_RE = re.compile(r"^(\s*status:\s*)([^\s#]+)(.*)$")
_RESIDUALS_RE = re.compile(r"^\*\*Residuals:\*\*\s*(\S+)", re.MULTILINE)
_BREAKS_RE = re.compile(r"^\*\*Breaks:\*\*\s*(\S+)", re.MULTILINE)


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


def _count(text, pattern):
    hit = pattern.search(text)
    if not hit:
        return None
    try:
        return int(hit.group(1))
    except ValueError:
        return None


def residuals_of(path):
    """How many falsifying paths the digest found, off its ``**Residuals:**`` header —
    both classes together. None when the digest carries no countable header, which is NOT
    zero: it means this review said nothing a program can compare against the last one."""
    if path is None or not path.is_file():
        return None
    return _count(path.read_text(errors="replace"), _RESIDUALS_RE)


def blocking_of(path):
    """How many **breaks** residuals the digest found — paths where a user gets a wrong
    answer today. This is the only count a convergence rule may read.

    The total cannot serve: it includes the unproven class, which is bounded by code size
    and grows every time a residual is closed, so a trend over it never falls. A digest
    predating the split carries no ``**Breaks:**`` line; its residuals were unclassified
    and every one of them blocked, so the total is the honest reading for those."""
    if path is None or not path.is_file():
        return None
    text = path.read_text(errors="replace")
    breaks = _count(text, _BREAKS_RE)
    return breaks if breaks is not None else _count(text, _RESIDUALS_RE)


def residual_trend(cfg, capability: str) -> list:
    """The blocking count of every full-promise digest, oldest first — the shape of the
    capability's convergence. An uncountable digest holds its place as None rather than
    dropping out, so a gap in the record cannot read as progress."""
    return [blocking_of(p) for p in digests(cfg, capability)]


def is_converging(cfg, capability: str) -> bool:
    """True when the blocking set is smaller than it was. Round count alone cannot tell
    a capability being narrowed down from one that is not designable: eleven residuals
    falling to three is the first, five holding at five is the second. Fewer than two
    countable digests cannot show a trend, so it does not claim one — this answers
    'is there evidence it is NOT converging', and absence of evidence is not that."""
    counts = [c for c in residual_trend(cfg, capability) if c is not None]
    if len(counts) < 2:
        return True
    return counts[-1] < counts[0]


def should_drain_with_residue(cfg, capability: str) -> bool:
    """True when another adequacy round cannot be justified, so the capability leaves
    through its residue instead.

    Two ways to get here. The set **stopped shrinking** — the review is no longer
    narrowing anything down, and each round's fix adds code that becomes the next round's
    residual, so the loop has no fixed point. Or the set is shrinking but has taken
    ``ROUND_CAP`` rounds, each a full adversarial source review at every stage close;
    past that the remainder is cheaper as ranked work than as a gate.

    Only a countable trend triggers it. Draining is irreversible, and a run of digests
    nobody can compare is exactly the case where a human should look — that is what
    ``should_park`` is for."""
    if verdict_of(newest_digest(cfg, capability)) == "adequate":
        return False
    counts = [c for c in residual_trend(cfg, capability) if c is not None]
    if len(counts) < 2:
        return False
    return len(counts) >= ROUND_CAP or not is_converging(cfg, capability)


def should_park(cfg, capability: str) -> bool:
    """The human escalation, now the fallback rather than the first exit. It is reached
    only when ``should_drain_with_residue`` did not fire — i.e. the residual trend is
    unreadable — because a promise a human re-words comes back the same way otherwise:
    dems re-worded CAP-015 twice and the count returned higher each time."""
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
