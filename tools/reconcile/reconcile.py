#!/usr/bin/env python3
"""reconcile.py — requirement-tag harvester (wf2 reconciliation-by-grep).

wf2's drift check. Answers "which of the design's open requirements are actually
built?" by grepping the test tree for requirement tags. Completion is DERIVED from
the tests, never stored — so there is no `done` flag to drift out of sync with the
code. A requirement is *covered* when a test carries its tag; a *slice* is complete
when all its requirements are covered; the *backlog* is empty (the design can be
released) when every slice is complete.

Tag format (the contract the build/review writer must satisfy):

    [REQ:<id>] <full EARS statement>      a component requirement, proven by its test
    [SYS-TC:<id>] <scenario description>  a system test case, proven by its e2e test

A plain comment token — any language, any comment style (// , # , /* */ , <!-- -->).
The text after the tag on the same line is the requirement's statement, carried so it
survives the spec layer draining; the harvester captures it (empty is allowed for
backward compatibility) and warns — never errors — when two occurrences of one id
carry different non-empty texts.
No hash: a reworded requirement does not invalidate its tag (completion is set
membership, not content equality). <id> is a repo-unique id (REQ-<n> or SYS-TC-<n>,
monotonic over the whole repo, never reused — a design-local id would collide with a
retired design's lingering tag). After a design retires, its tag stays in the test as a
historical breadcrumb — such tags are reported as orphans, never as errors.

Coverage is not correctness: a tag proves a proving test EXISTS and is committed.
Passing is the merge gate's job; whether the test is worth anything is the review
quality gate's job. Pair this with review/testing-anti-patterns.

Usage:
    reconcile.py --slices <slices.json> --tests <test-root> [--json]
    slices.json: {"slices": [{"id": "<slice>",
                              "requirements": ["REQ-1", ...],
                              "system_tests": ["SYS-TC-1", ...]}]}
    A slice is complete when every id in BOTH lists is covered by a tag.

Exit: 0 on success (report produced), 2 on input error. The JSON `all_complete`
field is the "backlog empty -> design releasable" signal.
"""
import argparse
import fnmatch
import json
import os
import re
import sys

# Two proving-tag lanes share one harvester: [REQ:<id>] for component requirements and
# [SYS-TC:<id>] for capability-level system tests. Both are plain comment tokens; the
# captured id is namespaced (REQ-<n> vs SYS-TC-<n>) so the lanes never collide.
TAG_RE = re.compile(r"\[(?:REQ|SYS-TC):\s*([\w.:-]+)\s*\]")

# The honest scope of "the test tree": a file counts as a proving test only when its
# NAME matches one of these globs. A [REQ:<id>] / [SYS-TC:<id>] token in a non-test file
# — an archived contract under an archive dir, a skill doc, a tool README — is NOT
# coverage; counting it would silently drain real backlog work (reconcile) or raise a
# false survivor (retired). Extension-agnostic on the test/spec infix so one set spans
# Go, TS/JS, Python, Ruby, and more:
#   *_test.*   foo_test.go, foo_test.py       *.test.*   foo.test.ts, foo.test.js
#   *.spec.*   foo.spec.ts, e2e.spec.js       *_spec.*   foo_spec.rb
#   test_*.*   test_foo.py
# Extend per project with --test-glob (added to, never replacing, these defaults).
DEFAULT_TEST_GLOBS = ("*_test.*", "*.test.*", "*.spec.*", "*_spec.*", "test_*.*")


def _is_test_file(name, test_globs):
    return any(fnmatch.fnmatch(name, g) for g in test_globs)


# Comment closers a statement may drag along when the tag lives in a block comment
# (/* ... */ or <!-- ... -->); stripped so the harvested text is the statement alone.
_COMMENT_CLOSERS = ("*/", "-->")


def _statement(line, tag_end):
    """The trailing text after the tag on its line — the statement the tag carries."""
    text = line[tag_end:].strip()
    for closer in _COMMENT_CLOSERS:
        if text.endswith(closer):
            text = text[: -len(closer)].strip()
    return text


def harvest(tests_roots, test_globs=DEFAULT_TEST_GLOBS):
    """Return {id: {"files": [relpath, ...], "statements": [text, ...]}} for every
    [REQ:<id>] / [SYS-TC:<id>] tag in the proving TEST FILES under tests_roots.

    tests_roots is a single path or an iterable of paths — coverage is the union across
    all of them (so a repo with split test trees reconciles in one call). A file counts
    only when its name matches test_globs; a tag in a non-test file is not coverage.
    Relpaths are relative to each file's own root."""
    if isinstance(tests_roots, (str, os.PathLike)):
        tests_roots = [tests_roots]
    covered = {}
    for tests_root in tests_roots:
        for root, _dirs, files in os.walk(tests_root):
            for name in files:
                if not _is_test_file(name, test_globs):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except OSError:
                    continue
                for line in text.splitlines():
                    for match in TAG_RE.finditer(line):
                        entry = covered.setdefault(
                            match.group(1), {"files": [], "statements": []}
                        )
                        entry["files"].append(os.path.relpath(path, tests_root))
                        entry["statements"].append(_statement(line, match.end()))
    return covered


def _slice_ids(sl):
    """Every proving id a slice must see built: component requirements + system tests."""
    return list(sl.get("requirements", [])) + list(sl.get("system_tests", []))


def reconcile(slices, harvested):
    covered_ids = set(harvested)
    # Per harvested id: its statement (first non-empty text seen — empty tags are
    # tolerated), plus a warning when occurrences disagree on non-empty text.
    statements = {}
    warnings = []
    for rid in sorted(harvested):
        distinct = []
        for text in harvested[rid]["statements"]:
            if text and text not in distinct:
                distinct.append(text)
        statements[rid] = distinct[0] if distinct else ""
        if len(distinct) > 1:
            warnings.append({"id": rid, "statements": distinct})
    out_slices = []
    for sl in slices:
        ids = _slice_ids(sl)
        missing = [r for r in ids if r not in covered_ids]
        out_slices.append({
            "id": sl.get("id"),
            "complete": not missing,
            "covered": [r for r in ids if r in covered_ids],
            "missing": missing,
        })
    expected = {r for sl in slices for r in _slice_ids(sl)}
    orphans = sorted(covered_ids - expected)
    all_complete = all(s["complete"] for s in out_slices) if out_slices else True
    return {
        "all_complete": all_complete,
        "slices": out_slices,
        "orphans": orphans,
        "statements": statements,
        "warnings": warnings,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="wf2 requirement-tag harvester")
    ap.add_argument("--slices", required=True, help="design slices JSON")
    ap.add_argument("--tests", action="append", required=True, metavar="PATH",
                    help="test-tree root to scan for tags (repeatable; coverage unions "
                         "across roots — pass each root of a split test tree)")
    ap.add_argument("--test-glob", action="append", dest="test_glob", metavar="GLOB",
                    help="extra test-file name glob, added to the built-in defaults "
                         "(repeatable) — e.g. '*Test.java'")
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
        for req in _slice_ids(sl):
            if req in seen and seen[req] != sl.get("id"):
                print(
                    f"reconcile: id {req!r} appears in two slices "
                    f"({seen[req]!r} and {sl.get('id')!r}); ids must be unique in the design",
                    file=sys.stderr,
                )
                return 2
            seen[req] = sl.get("id")

    for troot in args.tests:
        if not os.path.isdir(troot):
            print(f"reconcile: --tests {troot} is not a directory", file=sys.stderr)
            return 2

    globs = DEFAULT_TEST_GLOBS + tuple(args.test_glob or ())
    report = reconcile(slices, harvest(args.tests, globs))

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
        for rid in s["covered"]:
            if report["statements"].get(rid):
                print(f"  {rid}: {report['statements'][rid]}")
    done = sum(1 for s in report["slices"] if s["complete"])
    tail = "  -> backlog empty; design can be released" if report["all_complete"] and report["slices"] else ""
    print(f"backlog: {done}/{len(report['slices'])} slices complete{tail}")
    if report["orphans"]:
        print(f"historical/orphan tags (not in current design): {', '.join(report['orphans'])}")
    for w in report["warnings"]:
        texts = " | ".join(f'"{t}"' for t in w["statements"])
        print(f"warning: {w['id']} carries divergent statements across its tags: {texts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
