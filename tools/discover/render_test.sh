#!/usr/bin/env bash
#
# Smoke tests for render.py — the discover two-level read-view (on the shared chassis).
# Run: bash render_test.sh   (exit 0 = all pass)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/render.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

cat > "$TMP/model.json" <<'JSON'
{"nodes":{
  "go:internal/handlers":{"name":"handlers","path":"internal/handlers","loc":400,"lang":"go","has_doc":true,"has_tests":true,"synopsis":"HTTP entry points.","deps":["go:internal/engine"]},
  "go:cmd/server":{"name":"server","path":"cmd/server","loc":120,"lang":"go","has_doc":false,"has_tests":false,"synopsis":"","deps":["go:internal/handlers"]},
  "go:internal/engine":{"name":"engine","path":"internal/engine","loc":800,"lang":"go","has_doc":true,"has_tests":true,"synopsis":"Rule evaluation.","deps":["go:internal/repository"]},
  "go:internal/repository":{"name":"repository","path":"internal/repository","loc":600,"lang":"go","has_doc":true,"has_tests":true,"synopsis":"DB access.","deps":[]}
}}
JSON

cat > "$TMP/sub.json" <<'JSON'
{"system_summary":"A two-tier service.",
 "subsystems":[
   {"name":"API","summary":"HTTP entry points.","basis":"import cluster","members":["go:internal/handlers","go:cmd/server"]},
   {"name":"Domain core","summary":"Rules and persistence.","basis":"depgraph","members":["go:internal/engine","go:internal/repository"]}],
 "component_descriptions":{"go:internal/engine":"Evaluates rules."},
 "disagreements":[{"finding":"handlers and repository co-change with no import.","components":["go:internal/handlers","go:internal/repository"]}]}
JSON

OUT="$TMP/view.html"
python3 "$SCRIPT" --model "$TMP/model.json" --subsystems "$TMP/sub.json" --out "$OUT" --title "T" >/dev/null 2>"$TMP/err"
[ $? -eq 0 ] && [ -f "$OUT" ] && ok "renders HTML (exit 0, file written)" \
  || bad "render failed (see $TMP/err: $(cat "$TMP/err"))"

# offline/self-contained
grep -q "vis-network inlined (offline)" "$OUT" && ok "vendored lib inlined (offline)" || bad "offline marker missing"
grep -qE '<script[^>]*src="https?:' "$OUT" && bad "has a remote <script src>" || ok "no remote script src"

# two-level read-view: subsystem nav + component drill functions
grep -q "function goSystem" "$OUT" && grep -q "function enterSub" "$OUT" && grep -q "function showComp" "$OUT" \
  && ok "two-level nav present (system / subsystem / component)" || bad "two-level nav missing"

# the data lands: subsystem + component names, the disagreement finding
grep -q "Domain core" "$OUT" && grep -q "repository" "$OUT" && ok "subsystem + component names present" || bad "names missing"
grep -q "co-change with no import" "$OUT" && ok "disagreement finding present" || bad "disagreement missing"

# spacing physics from the shared chassis (the thing that makes 10+ nodes legible)
grep -q "avoidOverlap" "$OUT" && ok "spacing physics present" || bad "spacing physics missing"

# malformed subsystems.json fails the contract (non-zero), not a KeyError deep inside
echo '{"subsystems":"nope"}' > "$TMP/badsub.json"
python3 "$SCRIPT" --model "$TMP/model.json" --subsystems "$TMP/badsub.json" --out "$TMP/x.html" >/dev/null 2>"$TMP/err2"
[ $? -ne 0 ] && ok "malformed subsystems.json exits non-zero" || bad "malformed subsystems did not error"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
