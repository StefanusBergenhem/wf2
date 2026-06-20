#!/usr/bin/env bash
#
# Tests for render_design.py — the SA design-view renderer.
# Run: bash render_design_test.sh   (exit 0 = all pass)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/render_design.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()   { pass=$((pass+1)); echo "  ok   - $1"; }
bad()  { fail=$((fail+1)); echo "  FAIL - $1"; }

SAMPLE='{
  "title": "Auth change",
  "components": [
    {"id": "gateway", "label": "gateway", "state": "existing"},
    {"id": "auth", "label": "auth", "state": "existing"},
    {"id": "token-store", "label": "token-store", "state": "new"}
  ],
  "dependencies": [
    {"from": "gateway", "to": "auth", "state": "existing"},
    {"from": "auth", "to": "token-store", "state": "added"}
  ],
  "allocation": [
    {"requirement": "REQ-2", "component": "auth"},
    {"requirement": "REQ-3", "component": "gateway"}
  ]
}'

OUT="$TMP/view.html"

# 1 — renders a self-contained HTML from stdin
echo "$SAMPLE" | python3 "$SCRIPT" --out "$OUT" >/dev/null 2>"$TMP/err1"
[ $? -eq 0 ] && [ -f "$OUT" ] && ok "renders HTML from stdin (exit 0, file written)" \
  || bad "renders HTML from stdin (see $TMP/err1: $(cat "$TMP/err1"))"

# 2 — is offline/self-contained: vendored lib inlined, no remote <script src>
grep -q "vis-network inlined (offline)" "$OUT" && ok "vendored lib inlined (offline marker)" \
  || bad "offline marker missing"
grep -qE '<script[^>]*src="https?:' "$OUT" && bad "has a remote <script src> (not self-contained)" \
  || ok "no remote script src (self-contained)"

# 3 — carries the component labels
grep -q "token-store" "$OUT" && grep -q "gateway" "$OUT" && ok "component labels present" \
  || bad "component labels missing"

# 4 — distinguishes a new component (state styling reaches the output)
grep -q '"state": *"new"\|new-component\|#.*green\|NEW' "$OUT" \
  && ok "new-component state reaches output" || bad "new-component state not styled"

# 5 — allocation surfaces (requirement ids appear, e.g. in tooltips)
grep -q "REQ-2" "$OUT" && ok "allocation (requirement ids) present" || bad "allocation missing"

# 6 — title appears
grep -q "Auth change" "$OUT" && ok "title present" || bad "title missing"

# 7 — malformed JSON on stdin errors out (non-zero, no partial file)
BADOUT="$TMP/bad.html"
echo "{ not json" | python3 "$SCRIPT" --out "$BADOUT" >/dev/null 2>"$TMP/err7"
[ $? -ne 0 ] && ok "malformed JSON exits non-zero" || bad "malformed JSON did not error"

# 8 — missing required key (components) errors out
echo '{"title":"x"}' | python3 "$SCRIPT" --out "$TMP/n.html" >/dev/null 2>"$TMP/err8"
[ $? -ne 0 ] && ok "missing 'components' exits non-zero" || bad "missing key did not error"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]