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
**Date:** 20260810T101500Z   **Confidence:** high — read every call site

## Promise, quantified
Every entity whose verdict could be affected is re-validated.

## Falsifying paths → coverage
- store/rules.go:12 → SYS-TC-4 — covered
${4-- store/rules.go:212 → RESIDUAL: "every entity" · a rule edit leaves siblings stale}

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
**Date:** 20260810T101500Z   **Confidence:** medium — partial read

## RESIDUALS
- store/a.go:1 → RESIDUAL: "clause a" · sketch a
- store/b.go:2 → RESIDUAL: "clause b" · sketch b
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

echo ""
echo "  adequacy digest: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
