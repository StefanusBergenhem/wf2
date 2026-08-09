"""Drill-cache freshness — derived from git, never judged.

A drill digest is a read of the tree at one commit. The tree now moves under it at
every stage, so the cache is swept at every stage close: a digest whose targets have
changed since the commit it was taken at no longer describes the code, and a digest
that cannot say what it read cannot be checked at all. Both are deleted.

Adequacy digests share the directory and are never touched here — they are verdicts,
not reads of the tree, and the park count is derived by counting them.
"""
from __future__ import annotations

import re

import progress

# The header ``wf-drill`` writes. Both the rendered form (`**Taken at:** <sha>`) and a
# bare `taken_at: <sha>` are accepted, so a digest is not thrown away over a marker.
_TAKEN_AT_RE = re.compile(r"^\s*(?:\*\*Taken at:?\*\*|taken_at:)\s*[`\"']?([0-9a-fA-F]{7,40})",
                          re.MULTILINE)
_TARGETS_RE = re.compile(r"^\s*(?:\*\*Targets:?\*\*|targets:)\s*(.+)$", re.MULTILINE)
# The verdicts wf-adequacy leaves in the same directory. `adequacy.consecutive_inadequate`
# counts them to decide a park, so a sweep that took them would silently reset the count.
_ADEQUACY_PREFIX = "adequacy-"


def targets_of(text: str) -> list:
    """The repo-relative paths a digest says it read, or [] when it names none."""
    found = _TARGETS_RE.search(text)
    if not found:
        return []
    raw = found.group(1).replace("[", " ").replace("]", " ")
    return [t.strip().strip("`\"',") for t in re.split(r"[,\s]+", raw) if t.strip(" `\"',")]


def taken_at(text: str) -> str:
    found = _TAKEN_AT_RE.search(text)
    return found.group(1) if found else ""


def _stale(git, text: str) -> bool:
    """True when the digest can no longer be trusted: it names no commit, names no
    targets, git cannot reach the commit, or one of its targets has changed since."""
    sha, targets = taken_at(text), targets_of(text)
    if not sha or not targets:
        return True
    changed = git.changed_since(sha)
    if changed is None:
        return True
    return any(_touches(path, targets) for path in changed)


def _touches(path: str, targets) -> bool:
    path = path.strip("/")
    for target in targets:
        target = target.strip("/")
        if not target:
            continue
        if path == target or path.startswith(target + "/"):
            return True
    return False


def prune(rt) -> list:
    """Delete every stale digest under ``paths.drill_cache``. Returns their names."""
    cache = rt.cfg.path_opt("drill_cache")
    if not cache or not cache.is_dir() or rt.dry_run:
        return []
    pruned = []
    for path in sorted(cache.glob("*.md")):
        if path.name.startswith(_ADEQUACY_PREFIX):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if not _stale(rt.git, text):
            continue
        path.unlink()
        pruned.append(path.name)
    if pruned:
        rt.report.line(f"drill cache · pruned {len(pruned)} stale digest(s): "
                       f"{', '.join(pruned)}", symbol=progress.OK, indent=1)
    return pruned
