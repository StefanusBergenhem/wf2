#!/usr/bin/env python3
"""reconcile.py — requirement-tag harvester (wf2 reconciliation-by-grep).

wf2's drift check. Answers "which of the design's open requirements are actually
built?" by grepping the test tree for requirement tags. Completion is DERIVED from
the tests, never stored — so there is no `done` flag to drift out of sync with the
code. A requirement is *covered* when a test carries its tag; a *slice* is complete
when all its requirements are covered; the *backlog* is empty (the design can be
released) when every slice is complete.

Tag format (the contract the build/review writer must satisfy):

    [REQ:<id>]

A plain comment token — any language, any comment style (// , # , /* */ , <!-- -->).
No hash: a reworded requirement does not invalidate its tag (completion is set
membership, not content equality). <id> is a repo-unique requirement id (REQ-<n>,
monotonic over the whole repo, never reused — a design-local id would collide with a
retired design's lingering tag). After a design retires, its tag stays in the test as a
historical breadcrumb — such tags are reported as orphans, never as errors.

Coverage is not correctness: a tag proves a proving test EXISTS and is committed.
Passing is the merge gate's job; whether the test is worth anything is the review
quality gate's job. Pair this with review/testing-anti-patterns.

Usage:
    reconcile.py --slices <slices.json> --tests <test-root> [--json]
    slices.json: {"slices": [{"id": "<slice>", "requirements": ["REQ-1", ...]}]}

Exit: 0 on success (report produced), 2 on input error. The JSON `all_complete`
field is the "backlog empty -> design releasable" signal.
"""
import argparse
import json
import os
import re
import sys

TAG_RE = re.compile(r"\[REQ:\s*([\w.:-]+)\s*\]")


def harvest(tests_root):
    """Return {req_id: [relpath, ...]} for every [REQ:<id>] tag under tests_root."""
    covered = {}
    for root, _dirs, files in os.walk(tests_root):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for match in TAG_RE.finditer(text):
                covered.setdefault(match.group(1), []).append(
                    os.path.relpath(path, tests_root)
                )
    return covered


def reconcile(slices, covered_ids):
    out_slices = []
    for sl in slices:
        reqs = list(sl.get("requirements", []))
        missing = [r for r in reqs if r not in covered_ids]
        out_slices.append({
            "id": sl.get("id"),
            "complete": not missing,
            "covered": [r for r in reqs if r in covered_ids],
            "missing": missing,
        })
    expected = {r for sl in slices for r in sl.get("requirements", [])}
    orphans = sorted(covered_ids - expected)
    all_complete = all(s["complete"] for s in out_slices) if out_slices else True
    return {"all_complete": all_complete, "slices": out_slices, "orphans": orphans}


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 requirement-tag harvester")
    ap.add_argument("--slices", required=True, help="design slices JSON")
    ap.add_argument("--tests", required=True, help="test-tree root to scan for tags")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    try:
        with open(args.slices) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        print(f"reconcile: cannot read --slices {args.slices}: {err}", file=sys.stderr)
        return 2

    slices = data.get("slices", [])
    seen = {}
    for sl in slices:
        for req in sl.get("requirements", []):
            if req in seen and seen[req] != sl.get("id"):
                print(
                    f"reconcile: requirement id {req!r} appears in two slices "
                    f"({seen[req]!r} and {sl.get('id')!r}); ids must be unique in the design",
                    file=sys.stderr,
                )
                return 2
            seen[req] = sl.get("id")

    if not os.path.isdir(args.tests):
        print(f"reconcile: --tests {args.tests} is not a directory", file=sys.stderr)
        return 2

    report = reconcile(slices, set(harvest(args.tests)))

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    for s in report["slices"]:
        total = len(s["covered"]) + len(s["missing"])
        status = "COMPLETE" if s["complete"] else "PENDING"
        line = f"slice {s['id']}: {status}  ({len(s['covered'])}/{total} covered)"
        if s["missing"]:
            line += f"  missing: {', '.join(s['missing'])}"
        print(line)
    done = sum(1 for s in report["slices"] if s["complete"])
    tail = "  -> backlog empty; design can be released" if report["all_complete"] and report["slices"] else ""
    print(f"backlog: {done}/{len(report['slices'])} slices complete{tail}")
    if report["orphans"]:
        print(f"historical/orphan tags (not in current design): {', '.join(report['orphans'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
