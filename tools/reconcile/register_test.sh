#!/usr/bin/env bash
#
# Tests for register.py — the derived system-test register (read-only view over
# [SYS-TC:] proving-test tags; the auditable "what does the system promise
# end-to-end, right now?").
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
mkdir -p "$TESTS/e2e"
cat > "$TESTS/e2e/flow_test.go" <<'GO'
// [SYS-TC:SYS-TC-1] end-to-end import over the real path
func TestFlow(t *testing.T) {}
// [SYS-TC:SYS-TC-12] operator exports yesterday's memos
func TestExport(t *testing.T) {}
GO
cat > "$TESTS/e2e/again_test.go" <<'GO'
// [SYS-TC:SYS-TC-1] end-to-end import over the real path
func TestFlowAgain(t *testing.T) {}
// [SYS-TC:SYS-TC-2] a reworded scenario here
func TestTwo(t *testing.T) {}
// [SYS-TC:SYS-TC-2] a DIFFERENT description for the same id
func TestTwoB(t *testing.T) {}
GO
cat > "$TESTS/e2e/unit_test.go" <<'GO'
// [REQ:REQ-2] a legacy component-requirement tag
func TestLegacy(t *testing.T) {}
GO

# --- 1: emits the scenario register to stdout ------------------------------
OUT="$(python3 "$SCRIPT" --tests "$TESTS" 2>"$TMP/err1")"; rc=$?
[ "$rc" -eq 0 ] && ok "runs (exit 0)" || bad "run failed (rc=$rc): $(cat "$TMP/err1")"
echo "$OUT" | grep -q "SYS-TC-1" && ok "scenario id present" || bad "SYS-TC-1 missing: $OUT"
echo "$OUT" | grep -q "end-to-end import over the real path" && ok "description present" || bad "description missing"
echo "$OUT" | grep -q "e2e/flow_test.go" && ok "proving test path present" || bad "test path missing"
echo "$OUT" | grep -q "e2e/again_test.go" && ok "second proving test listed for shared id" || bad "second test path missing"

# --- 2: no REQ lane — legacy [REQ:] tags never appear ----------------------
echo "$OUT" | grep -q "REQ-2" && bad "legacy REQ tag rendered" || ok "no REQ lane (legacy tags ignored)"

# --- 3: numeric id ordering (SYS-TC-2 before SYS-TC-12) --------------------
POS2="$(echo "$OUT" | grep -n "SYS-TC-2 " | head -1 | cut -d: -f1)"
POS12="$(echo "$OUT" | grep -n "SYS-TC-12" | head -1 | cut -d: -f1)"
if [ -n "$POS2" ] && [ -n "$POS12" ] && [ "$POS2" -lt "$POS12" ]; then
    ok "ids ordered numerically"
else
    bad "numeric order broken (SYS-TC-2@$POS2 SYS-TC-12@$POS12)"
fi

# --- 4: divergent descriptions flagged -------------------------------------
echo "$OUT" | grep -q "SYS-TC-2.*divergent\|divergent.*SYS-TC-2" \
  && ok "divergent descriptions flagged" || bad "divergence not flagged"

# --- 5: --out writes the file ----------------------------------------------
DEST="$TMP/sub/register.md"
P="$(python3 "$SCRIPT" --tests "$TESTS" --out "$DEST")"
[ "$P" = "$DEST" ] && [ -f "$DEST" ] && grep -q "System test register" "$DEST" \
  && ok "--out writes the register and prints its path" || bad "--out failed: $P"

# --- 6: bad --tests dir rejected -------------------------------------------
if python3 "$SCRIPT" --tests "$TMP/nope" >/dev/null 2>&1; then
    bad "missing tests dir should exit non-zero"
else
    ok "missing tests dir rejected"
fi

# --- 7: tags in NON-test files are excluded from the register (C30) --------
cat > "$TESTS/e2e/NOTES.md" <<'MD'
Design note quoting a built [SYS-TC:SYS-TC-1] and an unbuilt [SYS-TC:SYS-TC-500].
MD
OUT7="$(python3 "$SCRIPT" --tests "$TESTS" 2>/dev/null)"
echo "$OUT7" | grep -q "SYS-TC-500" && bad "non-test file token leaked into register: $OUT7" \
  || ok "non-test tokens excluded from register"

# --- 8: --tests repeatable; register unions rows across roots --------------
T2="$TMP/tests2"; mkdir -p "$T2"
cat > "$T2/extra_test.go" <<'GO'
// [SYS-TC:SYS-TC-42] a scenario proven in a second tree
GO
OUT8="$(python3 "$SCRIPT" --tests "$TESTS" --tests "$T2" 2>/dev/null)"
echo "$OUT8" | grep -q "SYS-TC-42" && echo "$OUT8" | grep -q "SYS-TC-1 " \
  && ok "--tests repeatable; register unions rows across roots" || bad "multi-root register wrong: $OUT8"

echo ""
echo "  register: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
