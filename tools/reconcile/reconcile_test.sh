#!/usr/bin/env bash
#
# Tests for reconcile.py — the shared [SYS-TC:] proving-tag harvester (library).
# Run: bash reconcile_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# hv <python-expr over `h`> — run harvest over $ROOTS (space-separated) and eval the expr
hv() {
    python3 - "$1" $ROOTS <<PY
import sys
sys.path.insert(0, "$HERE")
from reconcile import harvest
h = harvest(sys.argv[2:])
print(eval(sys.argv[1]))
PY
}

# --- fixtures -------------------------------------------------------------
TESTS="$TMP/tests"
mkdir -p "$TESTS/go" "$TESTS/e2e"
cat > "$TESTS/e2e/flow_test.go" <<'GO'
// [SYS-TC:SYS-TC-1] operator exports yesterday's memos end-to-end
func TestFlow(t *testing.T) {}
/* [SYS-TC: SYS-TC-2 ] a block-comment scenario */
GO
cat > "$TESTS/go/import_test.go" <<'GO'
// [REQ:REQ-1] a legacy component-requirement tag (retired lane)
func TestPersist(t *testing.T) {}
GO

# --- 1: SYS-TC tags harvested with their descriptions ----------------------
ROOTS="$TESTS"
[ "$(hv "h['SYS-TC-1']['statements'][0]")" = "operator exports yesterday's memos end-to-end" ] \
  && ok "description harvested from the tag line" || bad "description harvest wrong"
[ "$(hv "h['SYS-TC-2']['statements'][0]")" = "a block-comment scenario" ] \
  && ok "block-comment closer stripped from the description" || bad "closer not stripped"
[ "$(hv "'e2e/flow_test.go' in h['SYS-TC-1']['files']")" = "True" ] \
  && ok "proving file recorded relative to its root" || bad "file relpath wrong"

# --- 2: legacy [REQ:] tags are NOT harvested (the lane is retired) ---------
[ "$(hv "[k for k in h if k.startswith('REQ')]")" = "[]" ] \
  && ok "[REQ:] tokens ignored — component requirements are not tagged in code" \
  || bad "REQ lane harvested"

# --- 3: tags in NON-test files are not coverage (C30 false-survivor guard) --
CONTAM="$TMP/contam"; mkdir -p "$CONTAM"
cat > "$CONTAM/README.md" <<'MD'
Worked example: [SYS-TC:SYS-TC-9].
MD
cat > "$CONTAM/real_test.go" <<'GO'
// [SYS-TC:SYS-TC-3] a real scenario
GO
ROOTS="$CONTAM"
[ "$(hv "sorted(h)")" = "['SYS-TC-3']" ] \
  && ok "non-test files are not harvested" || bad "non-test token harvested"

# --- 4: multi-root union ----------------------------------------------------
R1="$TMP/r1"; R2="$TMP/r2"; mkdir -p "$R1" "$R2"
printf '// [SYS-TC:SYS-TC-4] tree one\n' > "$R1/a_test.go"
printf '// [SYS-TC:SYS-TC-5] tree two\n' > "$R2/b.test.ts"
ROOTS="$R1 $R2"
[ "$(hv "sorted(h)")" = "['SYS-TC-4', 'SYS-TC-5']" ] \
  && ok "harvest unions across roots" || bad "multi-root union wrong"

# --- 5: extra globs extend, exact id capture -------------------------------
JV="$TMP/jv"; mkdir -p "$JV"
printf '// [SYS-TC:SYS-TC-6] junit scenario\n' > "$JV/FooTest.java"
G6="$(python3 - "$JV" <<PY
import sys
sys.path.insert(0, "$HERE")
from reconcile import harvest, DEFAULT_TEST_GLOBS
print(sorted(harvest(sys.argv[1])), sorted(harvest(sys.argv[1], DEFAULT_TEST_GLOBS + ("*Test.java",))))
PY
)"
[ "$G6" = "[] ['SYS-TC-6']" ] \
  && ok "extra test-glob extends the defaults" || bad "glob extension wrong: $G6"

# --- 6: a description wrapped over following comment lines is harvested whole ----
# The register is the durable proof record, so a truncated description is worse than a
# warning: it disagrees with the work-set entry holding the full text and raises a false
# work-set/test-tree drift.
WRAP="$TMP/wrap"; mkdir -p "$WRAP"
cat > "$WRAP/wrapped_test.go" <<'GO'
func TestZoneCompliance(t *testing.T) {
	// [SYS-TC:SYS-TC-10] A selector that designates no member reports incomplete
	// rather than failing the request — Given a zone carrying a requirement; When
	// its compliance is requested; Then the request succeeds
	pool := testPool(t)
}
GO
ROOTS="$WRAP"
[ "$(hv "h['SYS-TC-10']['statements'][0]")" = "A selector that designates no member reports incomplete rather than failing the request — Given a zone carrying a requirement; When its compliance is requested; Then the request succeeds" ] \
  && ok "wrapped description joined across its comment lines" || bad "wrapped description truncated"

# --- 7: the wrap stops at every boundary that ends a description -----------------
STOP="$TMP/stop"; mkdir -p "$STOP"
cat > "$STOP/stops_test.go" <<'GO'
// [SYS-TC:SYS-TC-11] first scenario
// [SYS-TC:SYS-TC-12] second scenario, its own description
// wrapped once
//
// unrelated note after the blank comment line
func TestTwo(t *testing.T) {}

// [SYS-TC:SYS-TC-13] a scenario ending at code
func TestThree(t *testing.T) {}
GO
ROOTS="$STOP"
[ "$(hv "h['SYS-TC-11']['statements'][0]")" = "first scenario" ] \
  && ok "a following tag line starts its own description, never continues one" \
  || bad "wrap swallowed the next tag"
[ "$(hv "h['SYS-TC-12']['statements'][0]")" = "second scenario, its own description wrapped once" ] \
  && ok "wrap stops at a blank comment line" || bad "wrap crossed the blank comment line"
[ "$(hv "h['SYS-TC-13']['statements'][0]")" = "a scenario ending at code" ] \
  && ok "wrap stops at the first non-comment line" || bad "wrap crossed into code"

# --- 8: wrapping in block comments and non-slash markers -------------------------
MARK="$TMP/mark"; mkdir -p "$MARK"
cat > "$MARK/block_test.go" <<'GO'
/* [SYS-TC:SYS-TC-14] a block-comment scenario
 * continued on the next line */
func TestBlock(t *testing.T) {}
GO
cat > "$MARK/test_hash.py" <<'PY'
# [SYS-TC:SYS-TC-15] a hash-comment scenario
# continued on the next line
def test_hash(): pass
PY
ROOTS="$MARK"
[ "$(hv "h['SYS-TC-14']['statements'][0]")" = "a block-comment scenario continued on the next line" ] \
  && ok "block-comment wrap joined and closer stripped" || bad "block-comment wrap wrong"
[ "$(hv "h['SYS-TC-15']['statements'][0]")" = "a hash-comment scenario continued on the next line" ] \
  && ok "hash-comment wrap joined" || bad "hash-comment wrap wrong"

echo ""
echo "  reconcile: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
