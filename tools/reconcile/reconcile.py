#!/usr/bin/env python3
"""reconcile.py — the shared [SYS-TC:] proving-tag harvester (library, no CLI).

The one tag scanner wf2 has. A system test carries a plain comment token

    [SYS-TC:<id>] <scenario description>

in any language, any comment style (// , # , /* */ , <!-- -->). The tag marks the
executable test that proves a designed end-to-end scenario shipped; its trailing text
is the scenario's description, which the harvester captures (a test's description
describes the test, so it cannot rot apart from it). <id> is repo-unique
(SYS-TC-<n>, monotonic — `id_counters.sys_tc` in .wf/config.yaml is the high-water
mark, never reused). After a scenario retires, a lingering tag is a historical
breadcrumb, never an error.

Component requirements (REQ-<n>) are NOT tagged in code: they are planning-time ids
whose statements live in the transient backlog → slice → contract chain, drained from
the merge record at sprint close (`wf pipeline complete-sprint`). This harvester
ignores any legacy `[REQ:...]` token.

Coverage is not correctness: a tag proves a proving test EXISTS and is committed.
Passing is the merge gate's job; whether the test is worth anything is the review
quality gate's job.

Importers: register.py (the derived scenario register), retired.py (the
superseded-id sweep), and the wf CLI's complete-sprint superseded sweep.
"""
import fnmatch
import os
import re

TAG_RE = re.compile(r"\[SYS-TC:\s*([\w.:-]+)\s*\]")

# The honest scope of "the test tree": a file counts as a proving test only when its
# NAME matches one of these globs. A [SYS-TC:<id>] token in a non-test file — an
# archived contract under an archive dir, a skill doc, a tool README — is NOT
# coverage; counting it would raise a false survivor (retired) or a false register
# row. Extension-agnostic on the test/spec infix so one set spans Go, TS/JS, Python,
# Ruby, and more:
#   *_test.*   foo_test.go, foo_test.py       *.test.*   foo.test.ts, foo.test.js
#   *.spec.*   foo.spec.ts, e2e.spec.js       *_spec.*   foo_spec.rb
#   test_*.*   test_foo.py
# Extend per project with the harvesting caller's extra globs (added to, never
# replacing, these defaults).
DEFAULT_TEST_GLOBS = ("*_test.*", "*.test.*", "*.spec.*", "*_spec.*", "test_*.*")


def _is_test_file(name, test_globs):
    return any(fnmatch.fnmatch(name, g) for g in test_globs)


# Comment closers a description may drag along when the tag lives in a block comment
# (/* ... */ or <!-- ... -->); stripped so the harvested text is the description alone.
_COMMENT_CLOSERS = ("*/", "-->")


def _statement(line, tag_end):
    """The trailing text after the tag on its line — the description the tag carries."""
    text = line[tag_end:].strip()
    for closer in _COMMENT_CLOSERS:
        if text.endswith(closer):
            text = text[: -len(closer)].strip()
    return text


def harvest(tests_roots, test_globs=DEFAULT_TEST_GLOBS):
    """Return {id: {"files": [relpath, ...], "statements": [text, ...]}} for every
    [SYS-TC:<id>] tag in the proving TEST FILES under tests_roots.

    tests_roots is a single path or an iterable of paths — coverage is the union across
    all of them (so a repo with split test trees harvests in one call). A file counts
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
