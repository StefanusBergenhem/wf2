#!/usr/bin/env bash
#
# Tests for `wf adequacy check` — the form gate on an adequacy digest.
# Run: bash tools/cli/tests/digest_test.sh (exit 0 = pass).
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CLI/../.." && pwd)"
WF="$CLI/wf"
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; echo "         $2"; }
jget() { "$PYTHON" -c 'import sys,json; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }
wf() { "$PYTHON" "$WF" "$@"; }

W="$(mktemp -d)"

# A digest that satisfies every rule — the shape the reference template carries.
good() {  # good <path> [verdict] [residuals] [residual-lines]
    local p="$1" verdict="${2:-inadequate}" n="${3:-1}"
    cat > "$p" <<EOF
# Adequacy: CAP-015 — $verdict
**Question:** full-promise
**Residuals:** $n
**Breaks:** $n
**Date:** 20260810T101500Z   **Confidence:** high — read every call site

## Promise, quantified
Every entity whose verdict could be affected is re-validated.

## Falsifying paths → coverage
- store/rules.go:12 → SYS-TC-4 — covered
${4-- store/rules.go:212 → RESIDUAL(breaks): "every entity" · a rule edit leaves siblings stale}

## Prune-worthy scenarios
- none
EOF
}

# ── the happy path ───────────────────────────────────────────────────────────

good "$W/ok.md"
OUT="$(wf adequacy check "$W/ok.md" --format json)"
RC=$?
[ "$RC" -eq 0 ] && ok "a well-formed digest exits 0" || bad "well-formed exit" "$OUT"
[ "$(jget "$OUT" "d['residuals']")" = "1" ] \
    && ok "the residual count is reported" || bad "count reported" "$OUT"
[ "$(jget "$OUT" "d['verdict']")" = "inadequate" ] \
    && ok "the verdict is reported" || bad "verdict reported" "$OUT"

good "$W/adequate.md" adequate 0 ""
wf adequacy check "$W/adequate.md" >/dev/null 2>&1 \
    && ok "an adequate digest with zero residuals exits 0" || bad "adequate zero" ""

# ── the misses C45 found in 10 of 10 dems digests ────────────────────────────

# no Residuals header at all — the whole reason convergence could not be counted
good "$W/noheader.md"
grep -v '^\*\*Residuals:\*\*' "$W/ok.md" > "$W/noheader.md"
if wf adequacy check "$W/noheader.md" >/dev/null 2>&1; then
    bad "a digest with no Residuals header should fail" "exited 0"
else
    ok "a digest with no Residuals header fails"
fi

# the count disagrees with the enumeration
good "$W/mismatch.md" inadequate 4
if wf adequacy check "$W/mismatch.md" >/dev/null 2>&1; then
    bad "a count that disagrees with the list should fail" "exited 0"
else
    ok "a count that disagrees with the enumerated residuals fails"
fi

# inadequate with nothing countable — the exact shape that blocks the stop rule
good "$W/uncountable.md" inadequate 0 ""
if wf adequacy check "$W/uncountable.md" >/dev/null 2>&1; then
    bad "inadequate with zero residuals should fail" "exited 0"
else
    ok "inadequate with zero residuals fails"
fi

# adequate while the list still names a residual
good "$W/contradict.md" adequate 1
if wf adequacy check "$W/contradict.md" >/dev/null 2>&1; then
    bad "adequate with a residual line should fail" "exited 0"
else
    ok "adequate with a residual line fails"
fi

# a reworded section heading does not hide the residuals from the count
cat > "$W/reworded.md" <<'EOF'
# Adequacy: CAP-015 — inadequate
**Question:** full-promise
**Residuals:** 2
**Breaks:** 2
**Date:** 20260810T101500Z   **Confidence:** medium — partial read

## RESIDUALS
- store/a.go:1 → RESIDUAL(breaks): "clause a" · sketch a
- store/b.go:2 → RESIDUAL(breaks): "clause b" · sketch b
EOF
wf adequacy check "$W/reworded.md" >/dev/null 2>&1 \
    && ok "residuals are counted by line form, not by section heading" || bad "reworded" ""

# ── the other header rules ───────────────────────────────────────────────────

good "$W/badq.md"
sed -i 's/^\*\*Question:\*\* full-promise/**Question:** the whole promise/' "$W/badq.md"
if wf adequacy check "$W/badq.md" >/dev/null 2>&1; then
    bad "a reworded question token should fail" "exited 0"
else
    ok "a reworded question token fails — the drain globs on that token"
fi

good "$W/badverdict.md" "probably fine" 1
if wf adequacy check "$W/badverdict.md" >/dev/null 2>&1; then
    bad "a non-verdict word should fail" "exited 0"
else
    ok "a heading with no adequate/inadequate verdict fails"
fi

good "$W/nonint.md" inadequate "one"
if wf adequacy check "$W/nonint.md" >/dev/null 2>&1; then
    bad "a spelled-out count should fail" "exited 0"
else
    ok "a Residuals value that is not an integer fails"
fi

if wf adequacy check "$W/nope.md" >/dev/null 2>&1; then
    bad "a missing digest should fail" "exited 0"
else
    ok "a missing digest file fails"
fi

# every failure names what to fix
good "$W/mismatch2.md" inadequate 4
ERRS="$(wf adequacy check "$W/mismatch2.md" 2>&1)"
case "$ERRS" in
    *Residuals*4*|*4*Residuals*) ok "the failure names the offending header and count" ;;
    *) bad "failure message" "$ERRS" ;;
esac

# ── the two residual classes, and which one gates ────────────────────────────
# A residual is one of two species with opposite mathematics. `breaks` — a user gets a
# wrong answer today — is bounded by what is actually wrong, so it can gate a drain and
# still terminate. `unproven` — the code is right, nothing pins it — is bounded by CODE
# SIZE, which grows every time a residual is fixed, so gating on it has no fixed point.
# The verdict tracks breaks alone; unproven lines are reported and routed, never blocking.

two_class() {  # path, verdict, residuals-header, breaks-header, lines
    cat > "$1" <<EOF
# Adequacy: CAP-015 — $2
**Question:** full-promise
**Residuals:** $3
**Breaks:** $4
**Date:** 20260810T101500Z   **Confidence:** high — read every call site

## Promise, quantified
Every entity whose verdict could be affected is re-validated.

## Falsifying paths → coverage
$5

## Prune-worthy scenarios
- none
EOF
}

two_class "$W/mixed.md" inadequate 2 1 '- handlers/attach.go:77 → RESIDUAL(breaks): "can carry" · unserved pairing accepted
- engine/fold.go:151 → RESIDUAL(unproven): "held open" · arm exists, unpinned'
OUT="$(wf adequacy check "$W/mixed.md" --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "a digest carrying both classes passes the form gate" || bad "2c mixed" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['breaks']")" = "1" ] \
    && ok "the breaks count is reported separately from the total" || bad "2c breaks field" "$OUT"

# THE RULE: adequate with unproven residuals is legal — this is the whole fix
two_class "$W/unproven-only.md" adequate 3 0 '- engine/fold.go:151 → RESIDUAL(unproven): "held open" · unpinned
- main.go:970 → RESIDUAL(unproven): "reported against that zone" · bound, unpinned
- agg.go:150 → RESIDUAL(unproven): "at least a given number" · unpinned'
OUT="$(wf adequacy check "$W/unproven-only.md" --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT" "d['errors']")" = "[]" ] \
    && ok "adequate WITH unproven residuals is legal — they never gate" || bad "2c adequate unproven" "rc=$RC $OUT"

# ...and adequate with a breaks residual is not
two_class "$W/adequate-breaks.md" adequate 1 1 '- handlers/attach.go:77 → RESIDUAL(breaks): "can carry" · unserved pairing'
OUT="$(wf adequacy check "$W/adequate-breaks.md" --format json)"; RC=$?
[ "$RC" -ne 0 ] && ok "adequate with a breaks residual is refused" || bad "2c adequate breaks" "rc=$RC $OUT"

# an inadequate verdict has to name a break, not just test debt
two_class "$W/inadequate-nobreak.md" inadequate 1 0 '- engine/fold.go:151 → RESIDUAL(unproven): "held open" · unpinned'
OUT="$(wf adequacy check "$W/inadequate-nobreak.md" --format json)"; RC=$?
[ "$RC" -ne 0 ] && ok "inadequate with no breaks residual is refused" || bad "2c inadequate nobreak" "rc=$RC $OUT"

# the Breaks header must match the enumeration, like the Residuals header does
two_class "$W/breaks-mismatch.md" inadequate 2 2 '- handlers/attach.go:77 → RESIDUAL(breaks): "can carry" · x
- engine/fold.go:151 → RESIDUAL(unproven): "held open" · y'
OUT="$(wf adequacy check "$W/breaks-mismatch.md" --format json)"; RC=$?
[ "$RC" -ne 0 ] && ok "a Breaks header the enumeration contradicts is refused" || bad "2c breaks mismatch" "rc=$RC $OUT"

# an unclassified residual is refused: the routing and the verdict both key on the class,
# so a bare `RESIDUAL:` line leaves both undecided
two_class "$W/unclassified.md" inadequate 1 1 '- handlers/attach.go:77 → RESIDUAL: "can carry" · no class'
OUT="$(wf adequacy check "$W/unclassified.md" --format json)"; RC=$?
[ "$RC" -ne 0 ] && ok "an unclassified residual is refused" || bad "2c unclassified" "rc=$RC $OUT"
case "$OUT" in *breaks*unproven*|*unproven*breaks*)
    ok "the refusal names the two classes to choose between" ;;
  *) bad "2c unclassified msg" "$OUT" ;; esac

# a digest with no residuals at all still needs no Breaks header to be adequate
good "$W/clean.md" adequate 0 ""
wf adequacy check "$W/clean.md" >/dev/null 2>&1 \
    && ok "a clean adequate digest needs no Breaks header" || bad "2c clean" ""

echo ""
echo "  adequacy digest: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
