#!/usr/bin/env bash
#
# Tests for register.py — the derived requirement register (read-only view over
# proving-test tags; the auditable "what does the system require, right now?").
# Run: bash register_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/register.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# --- fixtures -------------------------------------------------------------
TESTS="$TMP/tests"
mkdir -p "$TESTS/go" "$TESTS/e2e"
cat > "$TESTS/go/import_test.go" <<'GO'
// [REQ:REQ-2] imported rooms persist across restart
func TestPersist(t *testing.T) {}
// [REQ:REQ-10] the registry rejects a duplicate id
func TestDuplicate(t *testing.T) {}
GO
cat > "$TESTS/go/registry_test.go" <<'GO'
// [REQ:REQ-2] imported rooms persist across restart
func TestPersistAgain(t *testing.T) {}
// [REQ:REQ-7] a reworded statement here
func TestSeven(t *testing.T) {}
// [REQ:REQ-7] a DIFFERENT statement for the same id
func TestSevenB(t *testing.T) {}
GO
cat > "$TESTS/e2e/flow_test.go" <<'GO'
// [SYS-TC:SYS-TC-1] end-to-end import over the real path
func TestFlow(t *testing.T) {}
GO

# --- 1: emits a register to stdout ---------------------------------------
OUT="$(python3 "$SCRIPT" --tests "$TESTS" 2>"$TMP/err1")"; rc=$?
[ "$rc" -eq 0 ] && ok "runs (exit 0)" || bad "run failed (rc=$rc): $(cat "$TMP/err1")"
echo "$OUT" | grep -q "REQ-2" && ok "REQ id present" || bad "REQ-2 missing: $OUT"
echo "$OUT" | grep -q "imported rooms persist across restart" && ok "statement present" || bad "statement missing"
echo "$OUT" | grep -q "go/import_test.go" && ok "proving test path present" || bad "test path missing"
echo "$OUT" | grep -q "go/registry_test.go" && ok "second proving test listed for shared id" || bad "second test path missing"

# --- 2: SYS-TC lane in its own section ------------------------------------
echo "$OUT" | grep -q "SYS-TC-1" && ok "SYS-TC id present" || bad "SYS-TC-1 missing"
echo "$OUT" | grep -qi "system test" && ok "system-test section present" || bad "no system-test section"

# --- 3: numeric ordering (REQ-2 < REQ-7 < REQ-10) --------------------------
POS2="$(echo "$OUT" | grep -n "REQ-2 " | head -1 | cut -d: -f1)"
POS7="$(echo "$OUT" | grep -n "REQ-7" | head -1 | cut -d: -f1)"
POS10="$(echo "$OUT" | grep -n "REQ-10" | head -1 | cut -d: -f1)"
if [ -n "$POS2" ] && [ -n "$POS7" ] && [ -n "$POS10" ] && [ "$POS2" -lt "$POS7" ] && [ "$POS7" -lt "$POS10" ]; then
    ok "ids ordered numerically"
else
    bad "numeric order broken (REQ-2@$POS2 REQ-7@$POS7 REQ-10@$POS10)"
fi

# --- 4: divergent statements flagged --------------------------------------
echo "$OUT" | grep -q "REQ-7.*divergent\|divergent.*REQ-7" && ok "divergent statements flagged" || bad "divergence not flagged"

# --- 5: --out writes the file ---------------------------------------------
DEST="$TMP/register.md"
python3 "$SCRIPT" --tests "$TESTS" --out "$DEST" >/dev/null 2>&1
[ -f "$DEST" ] && grep -q "REQ-10" "$DEST" && ok "--out writes the register" || bad "--out failed"

# --- 6: bad --tests dir rejected -------------------------------------------
if python3 "$SCRIPT" --tests "$TMP/nope" >/dev/null 2>&1; then
    bad "missing tests dir should exit non-zero"
else
    ok "missing tests dir rejected"
fi

echo ""
echo "  register: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
