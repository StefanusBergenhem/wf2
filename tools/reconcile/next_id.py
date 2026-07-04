#!/usr/bin/env python3
"""next_id.py — repo-unique requirement-id allocator (wf2 tag-system writer side).

Prints the next free REQ-<n> id(s): it scans every path given for `REQ-<n>`
mentions and emits max+1 .. max+count. The writer-side mirror of reconcile.py
(the reader) — reconcile asks "is this id built?"; next_id asks "what id is free?".

Allocation scans BROADLY on purpose — every `REQ-<n>` mention in the given paths, not
only `[REQ:...]` tags. Over-counting is safe (a skipped number costs nothing);
under-counting reuses an id and collides with a retired design's lingering tag, which
is the one failure to avoid. So it errs high: any mention burns the number.

Nothing is stored. The next id is DERIVED from what exists (the governor) — there is no
high-water-mark counter to maintain. The only id it cannot keep burned is one minted,
never built (so never tagged), and removed from the backlog; reusing such an id is
harmless because nothing references it.

Usage:
    next_id.py --scan <path> [--scan <path> ...] [--count N] [--prefix REQ|SYS-TC]

Each --scan is a file or a directory (walked recursively). Pass the live id homes — the
test tree, the design backlog, the ADRs. Missing paths are skipped, not errors: minting
the first id of a greenfield repo scans those (absent) paths and returns <prefix>-1.

--prefix names the id namespace: REQ (default) for component requirements, SYS-TC for
system test cases. Each is monotonic in its own space, so the two lanes never collide;
scan the same homes for either.

Exit: 0 on success, 2 on input error (no --scan given, or --count < 1).
"""
import argparse
import os
import re
import sys

# Broad on purpose: any <prefix>-<n> mention, not only [<prefix>:...] tags. \b rejects a
# letter-prefixed lookalike (PREQ-9, FREQ-5) without burning real ids. The prefix names
# the id namespace — REQ for component requirements, SYS-TC for system test cases — each
# monotonic in its own space so the two lanes never collide.
def id_re(prefix):
    return re.compile(rf"\b{re.escape(prefix)}-(\d+)")


def _files(path):
    if os.path.isdir(path):
        for root, _dirs, names in os.walk(path):
            for name in names:
                yield os.path.join(root, name)
    elif os.path.isfile(path):
        yield path
    # missing path: contributes nothing (greenfield)


def max_id(scan_paths, pattern):
    """Highest n among <prefix>-<n> mentions across the given files/dirs (0 if none)."""
    highest = 0
    for p in scan_paths:
        for path in _files(p):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for match in pattern.finditer(text):
                highest = max(highest, int(match.group(1)))
    return highest


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 repo-unique id allocator")
    ap.add_argument("--scan", action="append", default=[], metavar="PATH",
                    help="file or dir to scan for <prefix>-<n> (repeatable)")
    ap.add_argument("--count", type=int, default=1, help="how many ids to allocate")
    ap.add_argument("--prefix", default="REQ",
                    help="id namespace to allocate in (REQ | SYS-TC; default REQ)")
    args = ap.parse_args(argv)

    if not args.scan:
        print("next_id: at least one --scan PATH is required (pass the test tree, "
              "the design backlog, the ADRs — even if absent)", file=sys.stderr)
        return 2
    if args.count < 1:
        print(f"next_id: --count must be >= 1 (got {args.count})", file=sys.stderr)
        return 2

    start = max_id(args.scan, id_re(args.prefix)) + 1
    for n in range(start, start + args.count):
        print(f"{args.prefix}-{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
