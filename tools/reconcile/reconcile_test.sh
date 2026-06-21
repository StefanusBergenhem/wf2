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

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ] && echo "ALL GREEN" || { echo "FAILURES"; exit 1; }
