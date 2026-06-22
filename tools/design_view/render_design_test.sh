#!/usr/bin/env bash
#
# Tests for render_design.py — the SA design-view renderer (on the shared chassis).
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
    {"id": "auth", "label": "auth", "state": "existing", "note": "owns sessions"},
    {"id": "token-store", "label": "token-store", "state": "new", "note": "new persistence"}
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

# 4 — move-state reaches the output (new component carries its state)
grep -q '"state": *"new"' "$OUT" && ok "new-component move-state in output" \
  || bad "new-component move-state missing"

# 5 — allocation surfaces (requirement ids present)
grep -q "REQ-2" "$OUT" && ok "allocation (requirement ids) present" || bad "allocation missing"

# 6 — title appears
grep -q "Auth change" "$OUT" && ok "title present" || bad "title missing"

# --- Cluster 9: scales past ~6 nodes (spacing) + readable notes/reqs (side panel) ---

# 7 — spacing physics present so the graph spreads (was the pack-on-top defect)
grep -q "avoidOverlap" "$OUT" && grep -q "springLength" "$OUT" \
  && ok "spacing physics present (avoidOverlap + springLength)" \
  || bad "spacing physics missing — graph would pack"

# 8 — a side panel renders notes + requirements (not hover-only tooltips)
grep -q "All components" "$OUT" && grep -q "Requirements allocated here" "$OUT" \
  && ok "side panel lists components + their requirements" \
  || bad "side panel content missing"

# 9 — the note travels as panel data, not a vis hover 'title' tooltip
grep -q "owns sessions" "$OUT" && ok "component note present (rendered in panel)" \
  || bad "component note missing"
grep -q '"title": *"state:' "$OUT" && bad "note still encoded as a hover tooltip" \
  || ok "note is not a hover-only tooltip"

# 10 — malformed JSON on stdin errors out (non-zero, no partial file)
echo "{ not json" | python3 "$SCRIPT" --out "$TMP/bad.html" >/dev/null 2>"$TMP/err10"
[ $? -ne 0 ] && ok "malformed JSON exits non-zero" || bad "malformed JSON did not error"

# 11 — missing required key (components) errors out
echo '{"title":"x"}' | python3 "$SCRIPT" --out "$TMP/n.html" >/dev/null 2>"$TMP/err11"
[ $? -ne 0 ] && ok "missing 'components' exits non-zero" || bad "missing key did not error"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
