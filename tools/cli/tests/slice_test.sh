#!/usr/bin/env bash
#
# Tests for `wf slice check` — the mechanical gate over the design-slice itself:
# no UNCONFIRMED assumption may leave the SA or enter the TL.
# Run: bash tools/cli/tests/slice_test.sh   (exit 0 = all pass)
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

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  design_slice: ".wf/design-slice.md"
YAML
SLICE="$PROJ/.wf/design-slice.md"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

# no assumptions section at all -> pass
cat > "$SLICE" <<'MD'
# Design-slice — widget

## Component requirements

- **REQ-1** — the widget does X  *(owner: core · CAP-1)*
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "no assumptions section exits 0" || bad "no-section exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "no assumptions section verdict pass" || bad "no-section verdict" "$OUT"

# all-CONFIRMED -> pass
cat >> "$SLICE" <<'MD'

## Assumptions requiring confirmation

- **A-1 · CONFIRMED** — CAP-1 read as widget-per-user, not widget-per-team.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "confirmed-only exits 0" || bad "confirmed exit" "rc=$RC $OUT"

# an UNCONFIRMED entry -> fail, code A3, id named
cat >> "$SLICE" <<'MD'
- **A-2 · UNCONFIRMED** — "driven" read as manual trigger only.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "unconfirmed exits 1" || bad "unconfirmed exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "fail" ] && ok "unconfirmed verdict fail" || bad "unconfirmed verdict" "$OUT"
[ "$(jget "$OUT" "any(f['code']=='A3' for f in d['errors'])")" = "True" ] && ok "finding code A3" || bad "A3 code" "$OUT"
[ "$(jget "$OUT" "any('A-2' in f['msg'] for f in d['errors'])")" = "True" ] && ok "names the assumption id" || bad "A3 id" "$OUT"

# --slice override wins over config
ALT="$PROJ/alt-slice.md"
printf '## Assumptions requiring confirmation\n\n- **A-9 · UNCONFIRMED** — x.\n' > "$ALT"
cat > "$SLICE" <<'MD'
# clean
MD
OUT="$(wf slice check --slice "$ALT" --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "--slice override is checked" || bad "--slice override" "rc=$RC $OUT"

# missing slice file -> mechanical failure (exit 2)
rm -f "$SLICE"
if wf slice check >/dev/null 2>&1; then
    bad "missing slice should exit non-zero" "exited 0"
else
    RC=$?
    [ "$RC" -eq 2 ] && ok "missing slice exits 2" || bad "missing slice rc" "rc=$RC"
fi

echo ""
echo "  slice: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
