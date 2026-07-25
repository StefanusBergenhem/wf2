#!/usr/bin/env python3
"""retired.py — the superseded SYS-TC sweep (wf2 retirement check).

Verifies that a list of superseded system-test ids no longer appears as a
`[SYS-TC:]` proving-test tag anywhere in the test tree. When the SA supersedes a
shipped scenario, the sprint that builds the successor must update or delete the
old proving test and its tag — this is the mechanical check that it happened
(complete-sprint runs the same sweep at close from the slice's Supersedes list).

Reuses reconcile.py's harvester: exact-id matching (SYS-TC-2 never matches
SYS-TC-20), test files only. Only SYS-TC ids are sweepable — component
requirements are not tagged in code, so a REQ id here is an input error, not a
vacuous pass.

Usage:
    retired.py --ids <SYS-TC-id> [<id> ...] --tests <test-root>

Exit: 0 when every id is gone; 1 when any id survives (each survivor listed
with the files still carrying its tag); 2 on input error. Stdlib only.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 superseded SYS-TC sweep")
    ap.add_argument("--ids", nargs="+", required=True,
                    help="superseded system-test ids that must be gone (SYS-TC-<n>)")
    ap.add_argument("--tests", action="append", required=True, metavar="PATH",
                    help="test-tree root to sweep (repeatable; sweeps the union of "
                         "roots — pass each root of a split test tree)")
    ap.add_argument("--test-glob", action="append", dest="test_glob", metavar="GLOB",
                    help="extra test-file name glob, added to the built-in defaults "
                         "(repeatable) — e.g. '*Test.java'")
    args = ap.parse_args(argv)

    bad_ids = [i for i in args.ids if not i.startswith("SYS-TC-")]
    if bad_ids:
        print(f"retired: only SYS-TC ids are sweepable (component requirements are not "
              f"tagged in code): {', '.join(bad_ids)}", file=sys.stderr)
        return 2

    for troot in args.tests:
        if not os.path.isdir(troot):
            print(f"retired: --tests {troot} is not a directory", file=sys.stderr)
            return 2

    roots = ", ".join(args.tests)
    globs = DEFAULT_TEST_GLOBS + tuple(args.test_glob or ())
    harvested = harvest(args.tests, globs)
    survivors = [rid for rid in args.ids if rid in harvested]

    if survivors:
        for rid in survivors:
            files = ", ".join(sorted(set(harvested[rid]["files"])))
            print(f"surviving: {rid}  ({files})")
        print(f"retired: {len(survivors)}/{len(args.ids)} superseded ids still "
              f"tagged under {roots}")
        return 1

    print(f"retired: all {len(args.ids)} superseded ids gone from {roots}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
