"""wf hygiene — mechanical repo-hygiene linter over the working tree.

``hygiene check`` enforces the context-cost rules code review should never spend
judgement on: file length (what a Read costs), Go function length (what an edit
window costs), and comment discipline (what every future reader pays). Thresholds
come from the config's ``hygiene:`` block.

Two modes:

- **Full sweep** (no ``--diff-base``): report every finding across the tracked +
  untracked source tree. Always exits 0 — this is the debt report a planning role
  reads, not a gate.
- **Ratchet** (``--diff-base <rev>``): check only files the diff touches, and fail
  (exit 1) only on what the diff made *worse* — a new file born over the length
  cap, or an introduced/added long function, long comment block, or spec-narrative
  block. An *existing* file that legitimate growth pushes past the length cap is
  reported but never fails the gate: splitting a file is planning work, not the
  build agent's call. Regression detection compares per-rule finding counts
  against the base revision's version of the file — coarse (an added violation
  masked by a removed one slips), but stateless and derived purely from git.

A ratchet that finds no changes at all reports ``verdict: empty`` and exits 1: a
gate that checked nothing is a no-op, not a pass.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import common

_SOURCE_EXT = {".go", ".ts", ".tsx", ".js", ".jsx", ".py"}
_AGENT_DOCS = {"AGENTS.md", "CLAUDE.md"}
_GENERATED_RE = re.compile(r"@generated|code generated|do not edit", re.I)
_SPEC_ID_RE = re.compile(r"\b(?:REQ-\d+|ADR-\d+|CAP-\d+|SYS-TC-\d+|L-\d{2,3})\b")
_TAG_RE = re.compile(r"\[(?:REQ|SYS-TC):")
# comment-ratio is only judged on files long enough for a ratio to mean anything;
# short files legitimately run comment-heavy (a ports/interface file's doc lines).
_RATIO_MIN_LINES = 50
# rules whose count increase in a touched pre-existing file fails the ratchet —
# each is fixable inside the diff that introduced it. file-length is deliberately
# absent (splitting is planning work); warn-severity rules never gate.
_RATCHET_RULES = {"func-length", "comment-block", "spec-narrative", "agents-md-length"}


def _cfg(config):
    h = common.config_doc(config).get("hygiene")
    if not isinstance(h, dict):
        common.die("no hygiene block in config — add one (see the config template)")
    missing = [k for k in ("file_warn", "file_error", "func_error", "comment_block_max",
                           "comment_ratio_warn", "agents_md_max") if k not in h]
    if missing:
        common.die(f"hygiene config missing: {', '.join(missing)}")
    return h


def _git(root, *args, check=True):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        common.die(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r


def _candidate(relpath):
    if relpath.startswith(".wf/"):  # wf machinery is not project code (mirrors `wf impact`)
        return False
    p = Path(relpath)
    return p.name in _AGENT_DOCS or p.suffix in _SOURCE_EXT


def _comment_line_numbers(lines, ext):
    """1-based indices of full-line comments. Line-based heuristic: only lines that
    ARE a comment count — a trailing same-line comment is code."""
    marker = "#" if ext == ".py" else "//"
    out, in_block = [], False
    for i, raw in enumerate(lines, 1):
        s = raw.strip()
        if in_block:
            out.append(i)
            if "*/" in s:
                in_block = False
        elif ext != ".py" and s.startswith("/*"):
            out.append(i)
            in_block = "*/" not in s
        elif s.startswith(marker):
            out.append(i)
    return out


def _blocks(indices):
    """Group consecutive line numbers into (start, end) runs."""
    out = []
    for i in indices:
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def _go_funcs(lines):
    """(start, end) per top-level Go func, relying on gofmt's closing ``}`` at column 0."""
    out, start = [], None
    for i, raw in enumerate(lines, 1):
        if start is None and raw.startswith("func "):
            start = i
        elif start is not None and raw.rstrip() == "}":
            out.append((start, i))
            start = None
    return out


def _check_file(relpath, text, cfg):
    lines = text.splitlines()
    n = len(lines)
    name, ext = Path(relpath).name, Path(relpath).suffix
    find = lambda rule, sev, line, msg: {
        "rule": rule, "severity": sev, "file": relpath, "line": line, "msg": msg}

    if name in _AGENT_DOCS:
        if n > cfg["agents_md_max"]:
            return [find("agents-md-length", "error", 1,
                         f"{n} lines > agents_md_max {cfg['agents_md_max']} — trim; "
                         f"every line here is loaded into every agent context")]
        return []
    if _GENERATED_RE.search("\n".join(lines[:5])):
        return []

    out = []
    if n > cfg["file_error"]:
        out.append(find("file-length", "error", 1,
                        f"{n} lines > file_error {cfg['file_error']} — split it"))
    elif n > cfg["file_warn"]:
        out.append(find("file-length", "warn", 1,
                        f"{n} lines > file_warn {cfg['file_warn']}"))

    if ext == ".go":
        for s, e in _go_funcs(lines):
            if e - s + 1 > cfg["func_error"]:
                out.append(find("func-length", "error", s,
                                f"func of {e - s + 1} lines > func_error {cfg['func_error']}"))

    comment_idx = _comment_line_numbers(lines, ext)
    for s, e in _blocks(comment_idx):
        blen = e - s + 1
        block = lines[s - 1:e]
        if blen > cfg["comment_block_max"]:
            out.append(find("comment-block", "error", s,
                            f"{blen}-line comment block > comment_block_max "
                            f"{cfg['comment_block_max']} — a comment states a constraint, "
                            f"not a narrative"))
        if blen >= 3 and any(_SPEC_ID_RE.search(l) and not _TAG_RE.search(l) for l in block):
            out.append(find("spec-narrative", "error", s,
                            "comment block narrates spec ids (REQ/ADR/CAP/L) — replace "
                            "with a tag or a one-line pointer"))
    if n >= _RATIO_MIN_LINES and len(comment_idx) / n > cfg["comment_ratio_warn"]:
        out.append(find("comment-ratio", "warn", 1,
                        f"{len(comment_idx)}/{n} comment lines > comment_ratio_warn "
                        f"{cfg['comment_ratio_warn']}"))
    return out


def _untracked(root):
    r = _git(root, "ls-files", "--others", "--exclude-standard")
    return [l for l in r.stdout.splitlines() if l]


def _check(rest):
    p = common.base_parser("hygiene check")
    p.add_argument("--diff-base", help="git rev to ratchet against: check only touched "
                                       "files, fail only on regressions")
    args = p.parse_args(rest)
    cfg = _cfg(args.config)
    # The tree to lint is the one the caller stands in (a task worktree, typically);
    # the config supplies thresholds only. Anchoring on the config's root instead
    # would read an unchanged host checkout from a worktree and pass on an empty diff.
    root = common.worktree_root()

    touched = []
    if args.diff_base:
        changed = [l for l in _git(root, "diff", "--name-only", args.diff_base, "--")
                   .stdout.splitlines() if l]
        touched = list(dict.fromkeys(changed + _untracked(root)))
        if not touched:
            common.emit({
                "mode": "ratchet", "files_checked": 0, "findings": [], "regressions": [],
                "verdict": "empty",
                "note": f"no changes against {args.diff_base} in {root} — the ratchet "
                        f"checked nothing. Run it from the tree you changed.",
            }, args.format)
            return 1
        files = [f for f in touched if _candidate(f)]
    else:
        tracked = [l for l in _git(root, "ls-files").stdout.splitlines() if l]
        files = [f for f in dict.fromkeys(tracked + _untracked(root)) if _candidate(f)]

    findings, regressions = [], []
    checked = 0
    for rel in files:
        path = root / rel
        if not path.is_file():
            continue  # deleted in the diff
        checked += 1
        cur = _check_file(rel, path.read_text(errors="replace"), cfg)
        findings.extend(cur)
        if not args.diff_base:
            continue
        base = _git(root, "show", f"{args.diff_base}:{rel}", check=False)
        if base.returncode != 0:  # new file: every error-severity finding regresses
            regressions.extend(f for f in cur if f["severity"] == "error")
            continue
        base_counts = Counter(f["rule"] for f in _check_file(rel, base.stdout, cfg))
        cur_by_rule = {}
        for f in cur:
            cur_by_rule.setdefault(f["rule"], []).append(f)
        for rule, fs in cur_by_rule.items():
            if rule in _RATCHET_RULES and len(fs) > base_counts.get(rule, 0):
                regressions.extend(fs[base_counts.get(rule, 0):])

    if args.diff_base:
        verdict = "fail" if regressions else "pass"
    else:
        verdict = "report"
    common.emit({
        "mode": "ratchet" if args.diff_base else "full",
        "files_checked": checked,
        "findings": findings,
        "regressions": regressions,
        "verdict": verdict,
    }, args.format)
    return 1 if verdict == "fail" else 0


COMMANDS = {
    ("hygiene", "check"): _check,
}
