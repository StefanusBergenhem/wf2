#!/usr/bin/env bash
#
# Tests for chassis.py — the shared graph-view rendering shell.
# Run: bash chassis_test.sh   (exit 0 = all pass)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

OUT="$TMP/page.html"
PYTHONPATH="$HERE" python3 - "$OUT" <<'PY'
import sys, chassis
html = chassis.render_page(
    title="My View <x>",
    summary="the summary line",
    bar_html='<button id="b">go</button>',
    panel_html="<div>initial panel</div>",
    body_js='WF.draw([{id:"a",label:"a"}],[]); WF.onNode(i=>WF.panel(i)); WF.panelHead("Z");',
    data={"hello": "world"},
    extra_css=".mine{color:red}",
)
open(sys.argv[1], "w").write(html)
PY
[ -f "$OUT" ] || { echo "render_page produced no file"; exit 1; }

# 1 — offline/self-contained: vendored lib inlined, no remote <script src>
grep -q "vis-network inlined (offline)" "$OUT" && ok "vendored lib inlined (offline)" \
  || bad "offline marker missing"
grep -qE '<script[^>]*src="https?:' "$OUT" && bad "has a remote <script src>" \
  || ok "no remote script src (self-contained)"

# 2 — caller's parts all land
grep -q "the summary line" "$OUT" && ok "summary present" || bad "summary missing"
grep -q 'id="b">go' "$OUT" && ok "bar html present" || bad "bar html missing"
grep -q "initial panel" "$OUT" && ok "initial panel present" || bad "panel missing"
grep -q '.mine{color:red}' "$OUT" && ok "extra css appended" || bad "extra css missing"
grep -q '"hello": *"world"' "$OUT" && ok "DATA payload exposed" || bad "DATA payload missing"
grep -q 'WF.draw(\[{id:"a"' "$OUT" && ok "body js present" || bad "body js missing"

# 3 — title is HTML-escaped (no raw injection)
grep -q "My View &lt;x&gt;" "$OUT" && ok "title escaped" || bad "title not escaped"

# 4 — the shared shell: WF helper + base spacing physics
grep -q "const WF" "$OUT" && ok "WF helper preamble present" || bad "WF helper missing"
grep -q "avoidOverlap" "$OUT" && grep -q "springLength" "$OUT" \
  && ok "base spacing physics present (avoidOverlap + springLength)" \
  || bad "base spacing physics missing"

# 5 — the two-pane skeleton (graph + panel mount points)
grep -q 'id="graph"' "$OUT" && grep -q 'id="panel"' "$OUT" \
  && ok "two-pane mount points present" || bad "mount points missing"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
