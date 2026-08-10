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

Component requirements are NOT tagged in code: an acceptance criterion IS the
requirement, and it lives in the transient stage → task contract chain, drained from
the merge record (`wf pipeline complete-sprint`). This harvester
ignores any legacy `[REQ:...]` token.

Coverage is not correctness: a tag proves a proving test EXISTS and is committed.
Passing is the merge gate's job; whether the test is worth anything is the review
quality gate's job.

Importers: register.py (the derived scenario register), retired.py (the
superseded-id sweep), design_view/render_design.py (shipped-scenario context), and
the wf CLI's complete-sprint superseded sweep.
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
# Block comments, mapped to the closer that ends them. Checked before the line markers
# below, because `<!--` would otherwise match `--`.
_BLOCK_COMMENTS = (("/*", "*/"), ("<!--", "-->"))
# Line-comment markers a description may wrap under. `;` is deliberately absent: it ends
# a statement in far more languages than it opens a comment in, so it would read a
# trailing tag on a line of code as commented.
_LINE_COMMENTS = ("//", "#", "--")


def _strip_closers(text):
    for closer in _COMMENT_CLOSERS:
        if text.endswith(closer):
            text = text[: -len(closer)].strip()
    return text


def _statement(line, tag_end):
    """The text after the tag on its own line — where the description starts."""
    return _strip_closers(line[tag_end:].strip())


def _comment_of(prefix):
    """The comment the tag sits inside, read from the text before it on its line:
    ("block", <closer>), ("line", <marker>), or (None, None) when it is in no comment."""
    for opener, closer in _BLOCK_COMMENTS:
        if opener in prefix:
            return "block", closer
    found = max(((prefix.rfind(m), m) for m in _LINE_COMMENTS), key=lambda p: p[0])
    return ("line", found[1]) if found[0] >= 0 else (None, None)


def _wrapped(lines, idx, prefix):
    """The continuation lines of a description that wraps below its tag, in order.

    A scenario description is one sentence of Given/When/Then prose and routinely runs
    past one line. Reading only the tag's own line captures it truncated, and the
    register is the durable proof record — a silent truncation there disagrees with the
    work-set entry holding the full text and raises a false drift.

    The wrap ends at the first line that is no longer the same comment: code, a blank
    line, a paragraph break, the block's closer, or a line opening a tag of its own.
    An unterminated block comment does not stop it — that is a compile error upstream."""
    kind, token = _comment_of(prefix)
    if kind is None or (kind == "block" and token in lines[idx]):
        return []
    out = []
    for line in lines[idx + 1:]:
        text = line.strip()
        if not text or TAG_RE.search(text):
            break
        closing = kind == "block" and token in text
        if kind == "line":
            if not text.startswith(token):
                break
            text = text[len(token):].strip()
        else:
            text = _strip_closers(text).lstrip("*").strip()
        if not text:
            break
        out.append(text)
        if closing:
            break
    return out


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
                lines = text.splitlines()
                for idx, line in enumerate(lines):
                    matches = list(TAG_RE.finditer(line))
                    for pos, match in enumerate(matches):
                        statement = _statement(line, match.end())
                        if pos == len(matches) - 1:  # only the last tag owns the wrap
                            statement = " ".join(
                                part for part in
                                [statement, *_wrapped(lines, idx, line[: match.start()])]
                                if part
                            )
                        entry = covered.setdefault(
                            match.group(1), {"files": [], "statements": []}
                        )
                        entry["files"].append(os.path.relpath(path, tests_root))
                        entry["statements"].append(statement)
    return covered
