#!/usr/bin/env python3
"""Tests for spine.py merge() — the language-agnostic IR union.

Focus: test-infrastructure packages must NOT enter the production component graph
(they leak in only because they carry non-test helper files, e.g. a Go `package test`
testcontainers harness). Run:
  <venv>/bin/python tools/discover/spine_test.py   (exit 0 = all pass)
wf2-source-only — never rendered into an install target."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spine  # noqa: E402

PASS = 0
FAIL = 0


def ok(m):
    global PASS
    PASS += 1
    print(f"  ok   - {m}")


def bad(m):
    global FAIL
    FAIL += 1
    print(f"  FAIL - {m}")


def ir(lang, *components, module="m"):
    return {"language": lang, "module": module, "components": list(components)}


def comp(cid, **kw):
    c = {"id": cid, "name": cid.rsplit("/", 1)[-1], "path": cid, "loc": kw.pop("loc", 10)}
    c.update(kw)
    return c


# production components survive untouched
m = spine.merge([ir("go", comp("internal/handlers"), comp("store"))])
if set(m["nodes"]) == {"go:internal/handlers", "go:store"}:
    ok("production components retained")
else:
    bad(f"unexpected nodes: {list(m['nodes'])}")

# test-dir packages are excluded from the production graph (the memos leak)
m = spine.merge([ir("go", comp("store"), comp("store/test"),
                    comp("server/router/api/v1/test"))])
ids = set(m["nodes"])
if "go:store/test" not in ids and "go:server/router/api/v1/test" not in ids and "go:store" in ids:
    ok("test-dir packages excluded from production graph")
else:
    bad(f"test-dir leak: {ids}")

# an edge pointing at an excluded test package is dropped, not left dangling
m = spine.merge([ir("go", comp("store", depends_on=["store/test"]), comp("store/test"))])
if m["nodes"]["go:store"]["deps"] == []:
    ok("edges to excluded test packages are dropped")
else:
    bad(f"dangling dep: {m['nodes']['go:store']['deps']}")

# a TS __tests__ dir is excluded too (cross-language, the filter lives in the spine)
m = spine.merge([ir("ts", comp("web/src/components"), comp("web/src/__tests__"))])
ids = set(m["nodes"])
if "ts:web/src/__tests__" not in ids and "ts:web/src/components" in ids:
    ok("TS __tests__ dir excluded")
else:
    bad(f"TS test leak: {ids}")

# a helper NAMED test-ish but NOT under a test dir is retained (documented residual —
# name-only exclusion is too risky; only a test-DIR segment excludes)
m = spine.merge([ir("go", comp("internal/testutil"))])
if "go:internal/testutil" in m["nodes"]:
    ok("named test helper not under a test dir is retained (residual)")
else:
    bad("testutil wrongly excluded")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
