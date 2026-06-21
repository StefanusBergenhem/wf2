#!/usr/bin/env bash
#
# Tests for _contract.py — the subsystems.json scout-output contract validator.
# Run: bash _contract_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# Run validate_subsystems on a JSON literal (passed via stdin to dodge quoting).
# Prints "OK" on success, else the validator's exit message.
check() {
  printf '%s' "$1" | python3 -c "
import sys, json
sys.path.insert(0, '$HERE')
from _contract import validate_subsystems
validate_subsystems(json.load(sys.stdin))
print('OK')
" 2>&1
}

# --- 1: a complete valid map passes --------------------------------------
OUT="$(check '{"subsystems":[{"name":"API","members":["go:a"]}],"disagreements":[{"finding":"x","components":["go:a"]}]}')"
[ "$OUT" = "OK" ] && ok "valid map passes" || bad "valid map rejected: $OUT"

# --- 2: the shipped example satisfies its own contract -------------------
OUT="$(python3 -c "
import sys, json
sys.path.insert(0, '$HERE')
from _contract import validate_subsystems
validate_subsystems(json.load(open('$HERE/subsystems.example.json')))
print('OK')
" 2>&1)"
[ "$OUT" = "OK" ] && ok "subsystems.example.json satisfies its own contract" || bad "example invalid: $OUT"

# --- 3: a disagreement missing `finding` fails, pointedly ----------------
OUT="$(check '{"subsystems":[{"name":"A","members":[]}],"disagreements":[{"components":["go:a"]}]}')"
echo "$OUT" | grep -q "finding" && ok "missing disagreement finding -> error names 'finding'" || bad "no pointed finding error: $OUT"
echo "$OUT" | grep -q "subsystems.example.json" && ok "error points at the example" || bad "error lacks example pointer: $OUT"

# --- 4: a subsystem missing `name` fails ---------------------------------
OUT="$(check '{"subsystems":[{"members":[]}]}')"
echo "$OUT" | grep -qi "name" && ok "missing subsystem name -> error" || bad "no name error: $OUT"

# --- 5: a subsystem missing `members` fails ------------------------------
OUT="$(check '{"subsystems":[{"name":"A"}]}')"
echo "$OUT" | grep -qi "members" && ok "missing members -> error" || bad "no members error: $OUT"

# --- 6: non-list `subsystems` fails --------------------------------------
OUT="$(check '{"subsystems":{}}')"
echo "$OUT" | grep -qi "subsystems" && ok "non-list subsystems -> error" || bad "no subsystems error: $OUT"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ] && echo "ALL GREEN" || { echo "FAILURES"; exit 1; }
