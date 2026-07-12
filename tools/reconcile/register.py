#!/usr/bin/env python3
"""register.py — the derived requirement register (read-only markdown view).

Answers "what does the system require, in total, right now?" for a human reader —
an engineer or an auditor — by harvesting the proving-test tags the same way
reconcile.py does. The register is DERIVED on demand from the tags; it is never
maintained, so it cannot drift. Regenerate it whenever you need it; never hand-edit
or treat a stale copy as truth.

Two lanes, same as the harvester: [REQ:<id>] component requirements and
[SYS-TC:<id>] system test cases, each row carrying the statement the tag line
holds and every test file that proves it. When two tags for one id carry
different non-empty statements, the row is flagged divergent (the same warning
reconcile emits).

Usage:
    register.py --tests <test-root> [--out <path>]

Exit: 0 on success, 2 on input error. Stdlib only.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402

_NUM_RE = re.compile(r"(\d+)$")


def _id_key(rid):
    """Numeric sort within a lane (REQ-2 < REQ-10); string fallback for odd ids."""
    m = _NUM_RE.search(rid)
    return (int(m.group(1)) if m else float("inf"), rid)


def _rows(harvested, prefix):
    """(id, statement, divergent?, files) for every harvested id in one lane."""
    rows = []
    for rid in sorted((r for r in harvested if r.startswith(prefix)), key=_id_key):
        distinct = []
        for text in harvested[rid]["statements"]:
            if text and text not in distinct:
                distinct.append(text)
        files = sorted(set(harvested[rid]["files"]))
        rows.append((rid, distinct[0] if distinct else "", len(distinct) > 1, files))
    return rows


def _table(rows, statement_header):
    lines = [f"| id | {statement_header} | proven by |", "|---|---|---|"]
    for rid, statement, divergent, files in rows:
        if divergent:
            statement += " ⚠ divergent statements across tags"
        proven = ", ".join(f"`{f}`" for f in files)
        lines.append(f"| {rid} | {statement} | {proven} |")
    return "\n".join(lines)


def render(harvested, tests_roots):
    shown = ", ".join(f"`{r}`" for r in tests_roots)
    parts = [
        "# Requirement register",
        "",
        f"> Derived from the `[REQ:]` / `[SYS-TC:]` proving-test tags under "
        f"{shown}. Regenerate on demand (`register.py --tests …`); "
        "never hand-edit — a stale copy is not truth.",
        "",
        "## Component requirements",
        "",
        _table(_rows(harvested, "REQ-"), "statement"),
        "",
        "## System test cases",
        "",
        _table(_rows(harvested, "SYS-TC-"), "description"),
        "",
    ]
    return "\n".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 derived requirement register")
    ap.add_argument("--tests", action="append", required=True, metavar="PATH",
                    help="test-tree root to scan for tags (repeatable; rows union "
                         "across roots — pass each root of a split test tree)")
    ap.add_argument("--test-glob", action="append", dest="test_glob", metavar="GLOB",
                    help="extra test-file name glob, added to the built-in defaults "
                         "(repeatable) — e.g. '*Test.java'")
    ap.add_argument("--out", help="write the register here instead of stdout")
    args = ap.parse_args(argv)

    for troot in args.tests:
        if not os.path.isdir(troot):
            print(f"register: --tests {troot} is not a directory", file=sys.stderr)
            return 2

    globs = DEFAULT_TEST_GLOBS + tuple(args.test_glob or ())
    report = render(harvest(args.tests, globs), args.tests)

    if args.out:
        parent = os.path.dirname(args.out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(args.out)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
