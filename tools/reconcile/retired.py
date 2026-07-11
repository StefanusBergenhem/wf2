#!/usr/bin/env python3
"""retired.py — the superseded-id sweep (wf2 retirement check).

Verifies that a list of superseded requirement ids no longer appears as a
proving-test tag anywhere in the test tree. When the SA supersedes a shipped
requirement, the sprint that builds the successor must update or delete the
old proving test and its tag — this is the mechanical check that it happened.

Reuses reconcile.py's harvester, so it sweeps both lanes ([REQ:<id>] and
[SYS-TC:<id>]) with the same exact-id matching (REQ-2 never matches REQ-20).

Usage:
    retired.py --ids <id> [<id> ...] --tests <test-root>

Exit: 0 when every id is gone; 1 when any id survives (each survivor listed
with the files still carrying its tag); 2 on input error. Stdlib only.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from reconcile import harvest  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 superseded-id sweep")
    ap.add_argument("--ids", nargs="+", required=True,
                    help="superseded ids that must be gone (REQ-<n> / SYS-TC-<n>)")
    ap.add_argument("--tests", required=True, help="test-tree root to sweep")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.tests):
        print(f"retired: --tests {args.tests} is not a directory", file=sys.stderr)
        return 2

    harvested = harvest(args.tests)
    survivors = [rid for rid in args.ids if rid in harvested]

    if survivors:
        for rid in survivors:
            files = ", ".join(sorted(set(harvested[rid]["files"])))
            print(f"surviving: {rid}  ({files})")
        print(f"retired: {len(survivors)}/{len(args.ids)} superseded ids still "
              f"tagged under {args.tests}")
        return 1

    print(f"retired: all {len(args.ids)} superseded ids gone from {args.tests}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
