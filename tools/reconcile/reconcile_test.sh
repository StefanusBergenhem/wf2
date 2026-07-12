#!/usr/bin/env bash
#
# Tests for reconcile.py — the requirement-tag harvester (reconciliation-by-grep).
# Run: bash reconcile_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/reconcile.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# --- fixtures -------------------------------------------------------------
SLICES="$TMP/slices.json"
cat > "$SLICES" <<'JSON'
{
  "slices": [
    {"id": "foundation", "requirements": ["REQ-1", "REQ-2"]},
    {"id": "endpoint",   "requirements": ["REQ-3"]}
  ]
}
JSON

TESTS="$TMP/tests"
mkdir -p "$TESTS/go" "$TESTS/ts"
# Go test tags REQ-1 and REQ-2 — different comment styles + whitespace
cat > "$TESTS/go/import_test.go" <<'GO'
// [REQ:REQ-1] imported rooms persist
func TestPersist(t *testing.T) {}
/* [REQ: REQ-2 ] registry stays consistent */
GO
# TS test carries an ORPHAN tag (REQ-99 not in the design) — a historical breadcrumb
cat > "$TESTS/ts/ui.test.ts" <<'TS'
// [REQ:REQ-99] a retired requirement's lingering tag
it("does stuff", () => {})
TS

# --- 1: partial coverage (REQ-3 missing) ---------------------------------
OUT="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$TESTS" 2>"$TMP/err1")"; rc=$?
[ "$rc" -eq 0 ] && ok "runs (exit 0)" || bad "run failed (rc=$rc): $(cat "$TMP/err1")"
echo "$OUT" | grep -qi "foundation: *COMPLETE" && ok "foundation slice complete" || bad "foundation not complete: $OUT"
echo "$OUT" | grep -qi "REQ-3" && ok "missing REQ-3 reported in text" || bad "missing REQ-3 not reported: $OUT"

# --- 2: JSON output structure --------------------------------------------
JOUT="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$TESTS" --json 2>/dev/null)"
echo "$JOUT" | python3 -c '
import sys, json
d = json.load(sys.stdin)
assert d["all_complete"] is False, d
s = {x["id"]: x for x in d["slices"]}
assert s["foundation"]["complete"] is True, s
assert s["endpoint"]["complete"] is False, s
assert "REQ-3" in s["endpoint"]["missing"], s
assert "REQ-99" in d["orphans"], d
' && ok "json structure correct" || bad "json structure wrong: $JOUT"

# --- 3: full coverage -> all_complete (design releasable) ----------------
cat > "$TESTS/ts/endpoint.test.ts" <<'TS'
// [REQ:REQ-3] endpoint returns the entity
TS
JOUT2="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$TESTS" --json 2>/dev/null)"
echo "$JOUT2" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["all_complete"] is True, d' \
  && ok "all_complete true when every req tagged" || bad "all_complete not true: $JOUT2"

# --- 4: orphan/historical tags are informational, never a failure --------
echo "$JOUT2" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert "REQ-99" in d["orphans"], d' \
  && ok "orphan tag still reported (historical breadcrumb), run still succeeds" || bad "orphan handling wrong"

# --- 5: duplicate req id across slices is a config (design) error --------
DUP="$TMP/dup.json"
cat > "$DUP" <<'JSON'
{"slices":[{"id":"a","requirements":["REQ-1"]},{"id":"b","requirements":["REQ-1"]}]}
JSON
python3 "$SCRIPT" --slices "$DUP" --tests "$TESTS" >/dev/null 2>"$TMP/err5"; rc=$?
[ "$rc" -eq 2 ] && ok "duplicate req id across slices rejected (exit 2)" || bad "duplicate req id not rejected (rc=$rc)"

# --- 6: empty test tree -> all pending -----------------------------------
EMPTY="$TMP/empty"; mkdir -p "$EMPTY"
JOUT3="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$EMPTY" --json 2>/dev/null)"
echo "$JOUT3" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["all_complete"] is False and all(not s["complete"] for s in d["slices"]), d' \
  && ok "empty tree -> all slices pending" || bad "empty tree handling wrong: $JOUT3"

# --- 7: missing slices file -> input error -------------------------------
python3 "$SCRIPT" --slices "$TMP/nope.json" --tests "$TESTS" >/dev/null 2>"$TMP/err7"; rc=$?
[ "$rc" -eq 2 ] && ok "missing slices file rejected (exit 2)" || bad "missing slices file not rejected (rc=$rc)"

# --- 8: SYS-TC lane — a slice's system_tests drain via [SYS-TC:...] tags ---
SYSSLICE="$TMP/sys-slice.json"
cat > "$SYSSLICE" <<'JSON'
{"slices":[{"id":"cap","requirements":["REQ-1"],"system_tests":["SYS-TC-1","SYS-TC-2"]}]}
JSON
SYSTESTS="$TMP/systests"; mkdir -p "$SYSTESTS"
cat > "$SYSTESTS/req.test.ts" <<'TS'
// [REQ:REQ-1] the component requirement
TS
cat > "$SYSTESTS/e2e.spec.ts" <<'TS'
// [SYS-TC:SYS-TC-1] operator exports yesterday's memos end-to-end
TS
# REQ-1 + SYS-TC-1 covered, SYS-TC-2 missing -> slice incomplete
JOUT8="$(python3 "$SCRIPT" --slices "$SYSSLICE" --tests "$SYSTESTS" --json 2>/dev/null)"
echo "$JOUT8" | python3 -c '
import sys, json
s = json.load(sys.stdin)["slices"][0]
assert "REQ-1" in s["covered"] and "SYS-TC-1" in s["covered"], s
assert "SYS-TC-2" in s["missing"] and s["complete"] is False, s
' && ok "system_tests drain via [SYS-TC:...] tags; missing SYS-TC reported" || bad "sys-tc lane wrong: $JOUT8"

# --- 9a: statements — the tag line's trailing text rides the id ----------
JOUT9A="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$TESTS" --json 2>/dev/null)"
echo "$JOUT9A" | python3 -c '
import sys, json
d = json.load(sys.stdin)
assert d["statements"]["REQ-1"] == "imported rooms persist", d
assert d["statements"]["REQ-2"] == "registry stays consistent", d
assert d["warnings"] == [], d
' && ok "statements harvested from tag lines (comment closers stripped)" || bad "statement harvest wrong: $JOUT9A"
OUT9A="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$TESTS" 2>/dev/null)"
echo "$OUT9A" | grep -q "REQ-1: imported rooms persist" \
  && ok "text report shows covered id with its statement" || bad "text report missing statement: $OUT9A"

# --- 9b: bare tag (no trailing text) -> empty statement, never a failure --
BARE="$TMP/bare"; mkdir -p "$BARE"
cat > "$BARE/t.test.ts" <<'TS'
// [REQ:REQ-1]
TS
JOUT9B="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$BARE" --json 2>/dev/null)"
echo "$JOUT9B" | python3 -c '
import sys, json
d = json.load(sys.stdin)
assert d["statements"]["REQ-1"] == "", d
assert d["warnings"] == [], d
' && ok "bare tag -> empty statement allowed, no warning" || bad "bare tag handling wrong: $JOUT9B"

# --- 9c: divergent non-empty texts on one id -> warning, never an error ---
DIV="$TMP/div"; mkdir -p "$DIV"
cat > "$DIV/a.test.ts" <<'TS'
// [REQ:REQ-1] the system shall persist rooms
TS
cat > "$DIV/b.test.py" <<'PY'
# [REQ:REQ-1] rooms persist across restart
PY
cat > "$DIV/c.test.ts" <<'TS'
// [REQ:REQ-1]
TS
python3 "$SCRIPT" --slices "$SLICES" --tests "$DIV" --json > "$TMP/jout9c" 2>/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "divergent texts still exit 0 (warning, never an error)" || bad "divergent texts errored (rc=$rc)"
python3 -c '
import sys, json
d = json.load(open(sys.argv[1]))
w = [x for x in d["warnings"] if x["id"] == "REQ-1"]
assert len(w) == 1, d
assert set(w[0]["statements"]) == {"the system shall persist rooms", "rooms persist across restart"}, d
' "$TMP/jout9c" && ok "divergent-text warning carries the differing statements (empty ignored)" || bad "warning shape wrong: $(cat "$TMP/jout9c")"
OUT9C="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$DIV" 2>/dev/null)"
echo "$OUT9C" | grep -qi "warning.*REQ-1" && ok "text mode surfaces the divergence warning" || bad "text warning missing: $OUT9C"

# --- 9: a system-test id duplicated across slices is a design error -------
SYSDUP="$TMP/sysdup.json"
cat > "$SYSDUP" <<'JSON'
{"slices":[{"id":"a","system_tests":["SYS-TC-1"]},{"id":"b","system_tests":["SYS-TC-1"]}]}
JSON
python3 "$SCRIPT" --slices "$SYSDUP" --tests "$SYSTESTS" >/dev/null 2>"$TMP/err9"; rc=$?
[ "$rc" -eq 2 ] && ok "duplicate SYS-TC id across slices rejected (exit 2)" || bad "duplicate SYS-TC id not rejected (rc=$rc)"

# --- 10: tags in NON-test files are not coverage (C30 false-drain guard) ---
# A [REQ:...] token in an archived contract, a skill doc, or a README is NOT a built
# requirement — counting it silently drains real backlog work. Only proving TEST files count.
CONTAM="$TMP/contam"; mkdir -p "$CONTAM/backend" "$CONTAM/.wf/archive/s1" "$CONTAM/.wf/tools"
cat > "$CONTAM/backend/endpoint_test.go" <<'GO'
// [REQ:REQ-1] the built component requirement
func TestOne(t *testing.T) {}
GO
# REQ-3 is genuinely UNBUILT — it appears only in machine-owned, non-test files:
cat > "$CONTAM/.wf/archive/s1/old__sprint.yaml" <<'YAML'
requirements: ["[REQ:REQ-3] a shipped-then-archived contract quoting its id"]
YAML
cat > "$CONTAM/.wf/tools/README.md" <<'MD'
Worked example: [REQ:REQ-3] and [SYS-TC:SYS-TC-9].
MD
JOUT10="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$CONTAM" --json 2>/dev/null)"
echo "$JOUT10" | python3 -c '
import sys, json
d = json.load(sys.stdin)
s = {x["id"]: x for x in d["slices"]}
assert "REQ-1" in s["foundation"]["covered"], d      # real test tag counts
assert "REQ-3" in s["endpoint"]["missing"], d        # non-test token does NOT count
assert s["endpoint"]["complete"] is False, d         # so the slice stays PENDING (no false drain)
assert "SYS-TC-9" not in d["orphans"], d             # README token is not an orphan tag either
' && ok "tags in non-test files are not coverage (C30 no false drain)" || bad "C30: non-test tags counted: $JOUT10"

# --- 11: --tests is repeatable; coverage unions across roots (C28/L-016) ---
R1="$TMP/r1"; R2="$TMP/r2"; mkdir -p "$R1" "$R2"
cat > "$R1/a_test.go" <<'GO'
// [REQ:REQ-1] built in tree one
GO
cat > "$R1/b_test.go" <<'GO'
// [REQ:REQ-2] also in tree one
GO
cat > "$R2/c.test.ts" <<'TS'
// [REQ:REQ-3] built in tree two
TS
JOUT11="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$R1" --tests "$R2" --json 2>/dev/null)"
echo "$JOUT11" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["all_complete"] is True, d' \
  && ok "--tests repeatable; coverage unions across roots" || bad "multi-root union wrong: $JOUT11"

# --- 12: --test-glob extends the default test-file globs -------------------
JV="$TMP/jv"; mkdir -p "$JV"
cat > "$JV/FooTest.java" <<'JAVA'
// [REQ:REQ-1] a JUnit test in a non-default-named file
JAVA
J12A="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$JV" --json 2>/dev/null)"
echo "$J12A" | python3 -c 'import sys,json; s={x["id"]:x for x in json.load(sys.stdin)["slices"]}; assert "REQ-1" in s["foundation"]["missing"]' \
  && ok "non-default test filename excluded by default globs" || bad "default glob wrongly matched .java: $J12A"
J12B="$(python3 "$SCRIPT" --slices "$SLICES" --tests "$JV" --test-glob '*Test.java' --json 2>/dev/null)"
echo "$J12B" | python3 -c 'import sys,json; s={x["id"]:x for x in json.load(sys.stdin)["slices"]}; assert "REQ-1" in s["foundation"]["covered"]' \
  && ok "--test-glob adds a project-specific test pattern" || bad "--test-glob not honored: $J12B"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ] && echo "ALL GREEN" || { echo "FAILURES"; exit 1; }
