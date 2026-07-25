#!/usr/bin/env bash
#
# Tests for retired.py — the superseded SYS-TC sweep.
# Run: bash retired_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/retired.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# --- fixtures -------------------------------------------------------------
TESTS="$TMP/tests"
mkdir -p "$TESTS/e2e"
cat > "$TESTS/e2e/flow_test.go" <<'GO'
// [SYS-TC:SYS-TC-3] the retired end-to-end scenario (superseded, not yet removed)
func TestFlow(t *testing.T) {}
GO
cat > "$TESTS/e2e/again_test.go" <<'GO'
// [SYS-TC:SYS-TC-3] the retired scenario's second proving test
func TestFlowAgain(t *testing.T) {}
// [SYS-TC:SYS-TC-30] a different, live scenario
func TestThirty(t *testing.T) {}
GO

# --- 1: all ids gone -> exit 0 ---------------------------------------------
python3 "$SCRIPT" --ids SYS-TC-88 SYS-TC-99 --tests "$TESTS" > "$TMP/out1" 2>"$TMP/err1"; rc=$?
[ "$rc" -eq 0 ] && ok "clean sweep exits 0" || bad "clean sweep rc=$rc: $(cat "$TMP/err1")"
grep -qi "gone" "$TMP/out1" && ok "clean sweep reports ids gone" || bad "no gone report: $(cat "$TMP/out1")"

# --- 2: a surviving tag -> exit non-zero, id listed -------------------------
python3 "$SCRIPT" --ids SYS-TC-3 SYS-TC-99 --tests "$TESTS" > "$TMP/out2" 2>&1; rc=$?
[ "$rc" -ne 0 ] && ok "surviving tag exits non-zero" || bad "surviving tag exited 0"
grep -q "SYS-TC-3" "$TMP/out2" && ok "surviving id listed" || bad "SYS-TC-3 not listed: $(cat "$TMP/out2")"

# --- 3: every proving file of the survivor is listed ------------------------
grep -q "e2e/flow_test.go" "$TMP/out2" && grep -q "e2e/again_test.go" "$TMP/out2" \
  && ok "surviving files listed" || bad "surviving files missing: $(cat "$TMP/out2")"

# --- 4: the clean id in a mixed run is not reported as surviving ------------
if grep -q "SYS-TC-99" "$TMP/out2"; then
    bad "gone id SYS-TC-99 wrongly listed as surviving"
else
    ok "gone id not listed among survivors"
fi

# --- 5: id must not substring-match (SYS-TC-3 does not match SYS-TC-30) -----
python3 "$SCRIPT" --ids SYS-TC-3 --tests "$TESTS" > "$TMP/out5" 2>&1; rc=$?
grep -q "SYS-TC-30" "$TMP/out5" && bad "SYS-TC-3 substring-matched SYS-TC-30" || ok "exact id match only"
[ "$rc" -ne 0 ] && ok "SYS-TC-3 itself still detected" || bad "SYS-TC-3 not detected (rc=$rc)"

# --- 6: a non-SYS-TC id -> input error (REQ ids are not tagged in code) -----
python3 "$SCRIPT" --ids REQ-4 --tests "$TESTS" >/dev/null 2>"$TMP/err6"; rc=$?
[ "$rc" -eq 2 ] && grep -q "REQ-4" "$TMP/err6" \
  && ok "a REQ id is rejected as an input error, not a vacuous pass" \
  || bad "REQ id not rejected (rc=$rc): $(cat "$TMP/err6")"

# --- 7: bad --tests dir -> input error (exit 2) ------------------------------
python3 "$SCRIPT" --ids SYS-TC-1 --tests "$TMP/nope" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "missing tests dir rejected (exit 2)" || bad "missing tests dir rc=$rc"

# --- 8: empty --ids -> input error (exit 2) ----------------------------------
python3 "$SCRIPT" --ids --tests "$TESTS" >/dev/null 2>&1; rc=$?
[ "$rc" -eq 2 ] && ok "empty ids rejected (exit 2)" || bad "empty ids rc=$rc"

# --- 9: an id lingering only in a NON-test file is reported GONE (C30) --------
NT="$TMP/nt"; mkdir -p "$NT"
cat > "$NT/keep_test.go" <<'GO'
// [SYS-TC:SYS-TC-1] a live, still-tagged scenario
GO
cat > "$NT/README.md" <<'MD'
Worked example uses [SYS-TC:SYS-TC-4].
MD
python3 "$SCRIPT" --ids SYS-TC-4 --tests "$NT" > "$TMP/out9" 2>&1; rc9=$?
[ "$rc9" -eq 0 ] && ok "id only in non-test files reported GONE (no false survivor)" \
  || bad "C30: non-test token read as surviving tag: $(cat "$TMP/out9")"

# --- 10: --tests repeatable; sweep spans every root --------------------------
A="$TMP/swa"; B="$TMP/swb"; mkdir -p "$A" "$B"
cat > "$A/live_test.go" <<'GO'
// [SYS-TC:SYS-TC-5] a superseded id still tagged in tree A
GO
cat > "$B/other_test.go" <<'GO'
// [SYS-TC:SYS-TC-6] an unrelated live scenario in tree B
GO
python3 "$SCRIPT" --ids SYS-TC-5 --tests "$A" --tests "$B" > "$TMP/out10" 2>&1; rc10=$?
[ "$rc10" -ne 0 ] && grep -q "live_test.go" "$TMP/out10" \
  && ok "--tests repeatable; survivor found in first root too" \
  || bad "multi-root sweep missed root A (rc=$rc10): $(cat "$TMP/out10")"

echo ""
echo "  retired: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
