#!/usr/bin/env python3
"""register.py — the derived system-test register (read-only markdown view).

Answers "what does the system provably promise end-to-end, right now?" by
harvesting the `[SYS-TC:<id>] <description>` proving-test tags (reconcile.py's
harvester). The register is DERIVED on demand from the tags; it is never
maintained, so it cannot drift. Regenerate it whenever you need it; never
hand-edit or treat a stale copy as truth.

Each row is one shipped scenario: its id, the description its tag line carries
(the user-voice behaviour the test proves), and every test file that proves it.
When two tags for one id carry different non-empty descriptions, the row is
flagged divergent.

Component requirements have no lane here: an acceptance criterion is the requirement,
planning-time working state in the transient stage → task contract chain, drained from
the merge record, and never tagged in code.

Usage:
    register.py --tests <test-root> [--tests <root> ...] [--out <path>]

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
    """Numeric sort (SYS-TC-2 < SYS-TC-10); string fallback for odd ids."""
    m = _NUM_RE.search(rid)
    return (int(m.group(1)) if m else float("inf"), rid)


def _rows(harvested):
    """(id, description, divergent?, files) for every harvested scenario."""
    rows = []
    for rid in sorted(harvested, key=_id_key):
        distinct = []
        for text in harvested[rid]["statements"]:
            if text and text not in distinct:
                distinct.append(text)
        files = sorted(set(harvested[rid]["files"]))
        rows.append((rid, distinct[0] if distinct else "", len(distinct) > 1, files))
    return rows


def render(harvested, tests_roots):
    shown = ", ".join(f"`{r}`" for r in tests_roots)
    lines = [
        "# System test register",
        "",
        f"> Derived from the `[SYS-TC:]` proving-test tags under {shown}. "
        "Regenerate on demand (`register.py --tests …`); never hand-edit — a stale "
        "copy is not truth.",
        "",
        "| id | description | proven by |",
        "|---|---|---|",
    ]
    for rid, description, divergent, files in _rows(harvested):
        if divergent:
            description += " ⚠ divergent descriptions across tags"
        proven = ", ".join(f"`{f}`" for f in files)
        lines.append(f"| {rid} | {description} | {proven} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 derived system-test register")
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
