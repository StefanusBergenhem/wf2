"""wf impact — mechanical consumer/fan-out computation for contract cutting.

Given the symbols a task changes (a function, field, table, column) and/or the
files it edits, emit the consumer file set (grep over the working tree, split
source vs. tests) and the companion fan-out: the built-in ``*.up.sql`` →
``*.down.sql`` sibling, plus any ``impact.companions`` rules the project config
declares (``when`` glob on a changed/consumer file → ``add`` globs expanded
against the tree, e.g. a migrations change fanning out to a codegen output
folder). ``candidates`` is the union of the three sets: the mechanical floor for
what a change must reach — the tree answers it, so nobody has to recall it — and
what makes two tasks' overlapping reach visible when ordering them.
"""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import common

_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".wf"}
_MAX_FALLBACK_BYTES = 2_000_000  # fallback scan: skip anything bigger (binaries, bundles)


def _skipped(rel_path: str) -> bool:
    return any(seg in _SKIP_DIRS for seg in rel_path.split("/"))


def _git_grep(root: Path, symbol: str):
    """Consumer paths via ``git grep`` (tracked + untracked), or None when git
    is unusable here — rc 0/1 are hits/no-hits; anything else means no repo."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "grep", "-I", "-l", "--untracked", "-F", "-e", symbol],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode not in (0, 1):
        return None
    return [line.strip() for line in proc.stdout.splitlines()
            if line.strip() and not _skipped(line.strip())]


def _walk_grep(root: Path, symbol: str):
    """Filesystem fallback when the project is not a git repo."""
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(seg in _SKIP_DIRS for seg in rel.parts):
            continue
        try:
            if path.stat().st_size > _MAX_FALLBACK_BYTES:
                continue
            if symbol in path.read_text(encoding="utf-8", errors="ignore"):
                hits.append(rel.as_posix())
        except OSError:
            continue
    return hits


def _consumers(root: Path, symbol: str):
    hits = _git_grep(root, symbol)
    if hits is None:
        hits = _walk_grep(root, symbol)
    return sorted(hits)


def _companions(root: Path, rules, inputs):
    """Companion files the changed set mechanically drags along. ``inputs`` is
    every changed/consumer path; a rule fires when any input matches its
    ``when`` glob. The up.sql→down.sql sibling is built in (named even when the
    sibling does not exist yet — the task must create it)."""
    found = []
    for path in sorted(inputs):
        if path.endswith(".up.sql"):
            sibling = path[: -len(".up.sql")] + ".down.sql"
            found.append({"rule": "*.up.sql sibling", "files": [sibling]})
    for rule in rules or []:
        when = str(rule.get("when") or "")
        adds = rule.get("add") or []
        if not when or not any(fnmatch.fnmatch(p, when) for p in inputs):
            continue
        files = []
        for pattern in adds if isinstance(adds, list) else [adds]:
            files += [f.relative_to(root).as_posix()
                      for f in sorted(root.glob(str(pattern))) if f.is_file()]
        if files:
            found.append({"rule": when, "files": files})
    return found


def _impact(rest):
    p = common.base_parser("impact")
    p.add_argument("--symbol", action="append", default=[],
                   help="a changed symbol (function, field, table, column); repeatable")
    p.add_argument("--file", action="append", default=[],
                   help="a file the change edits (companion-rule input); repeatable")
    args = p.parse_args(rest)
    if not args.symbol and not args.file:
        common.die("impact needs at least one --symbol or --file")

    root = common.project_root(args.config)
    rules = (common.config_doc(args.config).get("impact") or {}).get("companions")

    symbols = {}
    consumer_files = []
    for sym in args.symbol:
        hits = _consumers(root, sym)
        symbols[sym] = {
            "files": [h for h in hits if not common.is_test_path(h)],
            "tests": [h for h in hits if common.is_test_path(h)],
        }
        consumer_files += hits

    inputs = sorted(set(args.file) | set(consumer_files))
    companions = _companions(root, rules, inputs)

    candidates = set(inputs)
    for comp in companions:
        candidates.update(comp["files"])

    common.emit({
        "symbols": symbols,
        "companions": companions,
        "candidates": sorted(candidates),
    }, args.format)
    return 0


COMMANDS = {
    ("impact", "files"): _impact,
}
