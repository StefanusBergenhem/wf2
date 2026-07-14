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
grep -q "Components (" "$OUT" && grep -q "New in this slice" "$OUT" \
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

# 11 — neither 'components' nor 'entities' errors out
echo '{"title":"x"}' | python3 "$SCRIPT" --out "$TMP/n.html" >/dev/null 2>"$TMP/err11"
[ $? -ne 0 ] && ok "missing 'components'/'entities' exits non-zero" || bad "missing key did not error"

# --- ER mode: a domain model of entities + labelled relations ---------------

ER_SAMPLE='{
  "title": "dems domain model",
  "entities": [
    {"id": "Room", "label": "Room", "attributes": ["name: string", "boundary: Polygon"], "note": "a physical space"},
    {"id": "Boundary", "label": "Boundary", "attributes": ["kind: wall|zone"]},
    {"id": "Member", "label": "Member"}
  ],
  "relations": [
    {"from": "Room", "to": "Boundary", "label": "derived from", "cardinality": "1..*"},
    {"from": "Member", "to": "Room", "label": "assigned to"}
  ]
}'
EROUT="$TMP/er.html"

# 12 — ER input renders (exit 0, file written)
echo "$ER_SAMPLE" | python3 "$SCRIPT" --out "$EROUT" >/dev/null 2>"$TMP/err12"
[ $? -eq 0 ] && [ -f "$EROUT" ] && ok "ER: renders from entities+relations" \
  || bad "ER render failed (see $TMP/err12: $(cat "$TMP/err12"))"

# 13 — entity labels + attributes reach the output
grep -q "Boundary" "$EROUT" && grep -q "boundary: Polygon" "$EROUT" \
  && ok "ER: entity labels + attributes present" || bad "ER: labels/attributes missing"

# 14 — the relationship label and cardinality reach the output (the F-1 gap)
grep -q "derived from" "$EROUT" && grep -q "1\.\.\*" "$EROUT" \
  && ok "ER: labelled relation + cardinality present" || bad "ER: relation label/cardinality missing"

# 15 — ER side panel present
grep -q "All entities" "$EROUT" && ok "ER: side panel lists entities" || bad "ER: panel missing"

# 16 — a relation without a label is rejected (labelled edges are the point)
echo '{"entities":[{"id":"A"},{"id":"B"}],"relations":[{"from":"A","to":"B"}]}' \
  | python3 "$SCRIPT" --out "$TMP/er-bad.html" >/dev/null 2>&1
[ $? -ne 0 ] && ok "ER: unlabelled relation exits non-zero" || bad "ER: unlabelled relation accepted"

# --- the slice in context: what the toolchain already knows is DERIVED, not authored ---
#
# A component's description, the requirements already shipped into it, and the system
# tests already proven are all recoverable from discover's model + the proving-test tags.
# The agent authors only the change; the renderer joins it onto the derived context.

REPO="$TMP/repo"
mkdir -p "$REPO/backend/internal/auth" "$REPO/frontend/src/api" "$REPO/e2e"
cat > "$REPO/backend/internal/auth/auth_test.go" <<'EOF'
// [REQ:REQ-1] When a credential is valid, the auth component shall issue a signed token.
func TestIssue(t *testing.T) {}
EOF
cat > "$REPO/frontend/src/api/client.test.ts" <<'EOF'
// [REQ:REQ-5] The api client shall retry an idempotent request once on a 503.
EOF
cat > "$REPO/e2e/login.spec.ts" <<'EOF'
// [SYS-TC:SYS-TC-1] Given a registered user, when they log in, then a session cookie is set.
EOF

cat > "$TMP/model.json" <<'EOF'
{"nodes": {
  "go:internal/auth":    {"uid":"go:internal/auth","id":"internal/auth","path":"backend/internal/auth","lang":"go","loc":120,"synopsis":"exposes: Token, Verify"},
  "ts:frontend/src/api": {"uid":"ts:frontend/src/api","id":"frontend/src/api","path":"frontend/src/api","lang":"ts","loc":40,"synopsis":"exposes: client"}
}}
EOF
cat > "$TMP/subsystems.json" <<'EOF'
{"subsystems": [], "component_descriptions": {
  "go:internal/auth": "Owns session issuance and validation for every entry point."
}}
EOF

RICH='{
  "title": "Token store",
  "components": [
    {"id": "internal/auth", "label": "auth", "state": "existing"},
    {"id": "frontend/src/api", "label": "api client", "state": "existing"},
    {"id": "token-store", "label": "token-store", "state": "new", "note": "new persistence"}
  ],
  "dependencies": [{"from": "internal/auth", "to": "token-store", "state": "added"}],
  "allocation": [
    {"requirement": "REQ-9", "component": "internal/auth",
     "statement": "The auth component shall persist an issued token to the token store."}
  ],
  "system_tests": [
    {"id": "SYS-TC-9", "title": "Token survives a restart", "covers": "CAP-3",
     "given": "a logged-in user", "when": "the service restarts", "then": "the session still resolves"}
  ],
  "decisions": [
    {"id": "D-1", "title": "Where the token is persisted",
     "question": "Should the token store be its own component or live inside auth?",
     "options": [
       {"label": "Own component", "pros": "keeps auth free of storage concerns",
        "cons": "one more dependency edge"},
       {"label": "Inside auth", "pros": "no new component", "cons": "auth grows a second responsibility"}
     ],
     "recommended": "Own component", "status": "open",
     "components": ["internal/auth", "token-store"]}
  ]
}'
ROUT="$TMP/rich.html"
echo "$RICH" | python3 "$SCRIPT" --out "$ROUT" \
  --model "$TMP/model.json" --descriptions "$TMP/subsystems.json" \
  --tests "$REPO/backend" --tests "$REPO/frontend/src" --tests "$REPO/e2e" \
  >"$TMP/rich.log" 2>"$TMP/err17"
[ $? -eq 0 ] && [ -f "$ROUT" ] && ok "rich: renders with model + descriptions + tests" \
  || bad "rich: render failed (see $TMP/err17: $(cat "$TMP/err17"))"

# 17 — the component description is DERIVED from discover, not retyped by the agent
grep -q "Owns session issuance and validation" "$ROUT" \
  && ok "rich: component description derived from discover" \
  || bad "rich: derived component description missing"

# 18 — an authored note still wins for a component discover cannot know (a NEW one)
grep -q "new persistence" "$ROUT" && ok "rich: authored note kept for a new component" \
  || bad "rich: authored note lost"

# 19 — requirements ALREADY shipped into the component, with their statements, derived
#      from the proving-test tags (the whole point: the slice seen in context)
grep -q "REQ-1" "$ROUT" && grep -q "shall issue a signed token" "$ROUT" \
  && ok "rich: shipped requirement + statement derived from tags" \
  || bad "rich: shipped requirement/statement missing"
grep -q "retry an idempotent request" "$ROUT" \
  && ok "rich: shipped requirement found across a second test root" \
  || bad "rich: second test root not harvested"

# 20 — a shipped requirement is attributed to the component whose path proves it
python3 - "$ROUT" <<'PY' && ok "rich: shipped reqs attributed to the right component" \
  || bad "rich: shipped-req attribution wrong"
import json, re, sys
d = json.loads(re.search(r'\nconst DATA = (\{.*?\});\n', open(sys.argv[1]).read(), re.S).group(1))
n = {x["id"]: x for x in d["nodes"]}
auth = [r["id"] for r in n["internal/auth"]["shipped"]]
api  = [r["id"] for r in n["frontend/src/api"]["shipped"]]
sys.exit(0 if auth == ["REQ-1"] and api == ["REQ-5"] and not n["token-store"]["shipped"] else 1)
PY

# 21 — the NEW requirement carries its statement, not just its id
grep -q "shall persist an issued token" "$ROUT" \
  && ok "rich: new requirement carries its statement" || bad "rich: new requirement statement missing"

# 22 — system test cases reach the view: the slice's new one AND the shipped one
grep -q "SYS-TC-9" "$ROUT" && grep -q "Token survives a restart" "$ROUT" \
  && ok "rich: new system test case present" || bad "rich: new system test case missing"
grep -q "SYS-TC-1" "$ROUT" && grep -q "session cookie is set" "$ROUT" \
  && ok "rich: shipped system test case derived from tags" || bad "rich: shipped system test case missing"
grep -q "System tests" "$ROUT" && ok "rich: system-tests panel view present" \
  || bad "rich: no system-tests panel view"

# 23 — decisions are visible and tied to the components they move
grep -q "Where the token is persisted" "$ROUT" && grep -q "auth grows a second responsibility" "$ROUT" \
  && ok "rich: decision + option trade-offs present" || bad "rich: decision content missing"
grep -q "Decisions" "$ROUT" && ok "rich: decisions panel view present" || bad "rich: no decisions panel view"
grep -q "highlight" "$ROUT" && ok "rich: decision highlights its components on the graph" \
  || bad "rich: decision does not highlight components"

# 24 — a shipped tag that maps to NO component at all is reported, never silently dropped
mkdir -p "$REPO/backend/internal/orphan"
cat > "$REPO/backend/internal/orphan/o_test.go" <<'EOF'
// [REQ:REQ-77] An orphan requirement in no modelled component.
EOF
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/orph.html" --model "$TMP/model.json" \
  --tests "$REPO/backend" >"$TMP/orph.log" 2>&1
grep -q "REQ-77" "$TMP/orph.html" && ok "rich: unclaimed shipped requirement still surfaced" \
  || bad "rich: unclaimed shipped requirement silently dropped"

# 25 — a requirement shipped into a REAL component that this slice simply does not touch is
#      out of scope, not an orphan: counted, not listed. Every repo has hundreds of these;
#      listing them buries the slice the view exists to explain.
mkdir -p "$REPO/backend/internal/billing"
cat > "$REPO/backend/internal/billing/b_test.go" <<'EOF'
// [REQ:REQ-88] The billing component shall invoice monthly.
EOF
cat > "$TMP/model2.json" <<'EOF'
{"nodes": {
  "go:internal/auth":    {"uid":"go:internal/auth","id":"internal/auth","path":"backend/internal/auth","lang":"go"},
  "go:internal/billing": {"uid":"go:internal/billing","id":"internal/billing","path":"backend/internal/billing","lang":"go"}
}}
EOF
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/oos.html" --model "$TMP/model2.json" \
  --tests "$REPO/backend" >/dev/null 2>&1
python3 - "$TMP/oos.html" <<'PY' && ok "rich: out-of-scope requirement counted, not listed as an orphan" \
  || bad "rich: out-of-scope requirement mis-reported"
import json, re, sys
d = json.loads(re.search(r'\nconst DATA = (\{.*?\});\n', open(sys.argv[1]).read(), re.S).group(1))
orphan_ids = [o["id"] for o in d["orphans"]]
# REQ-88 lives in a modelled component outside the view -> counted only.
# REQ-77 lives in no modelled component at all -> a genuine orphan.
sys.exit(0 if "REQ-88" not in orphan_ids and "REQ-77" in orphan_ids
         and d["out_of_scope"] >= 1 else 1)
PY

# 25 — the derivation flags stay optional (the bare authored graph still renders)
echo "$SAMPLE" | python3 "$SCRIPT" --out "$TMP/plain.html" >/dev/null 2>&1
[ $? -eq 0 ] && ok "rich: derivation flags optional (bare graph still renders)" \
  || bad "rich: bare graph broke"

# 26 — a bad --model path fails loudly rather than silently rendering a context-free view
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/nm.html" --model "$TMP/nope.json" >/dev/null 2>&1
[ $? -ne 0 ] && ok "rich: missing --model file exits non-zero" || bad "rich: missing --model ignored"

# --- config defaults: the mechanical inputs resolve themselves ------------------
#
# model/subsystems are written by discover to a config-declared location, and the test
# roots are config-declared too. None of that needs an agent to type it: with a config
# present, the bare command derives everything.

PROJ="$TMP/proj"
mkdir -p "$PROJ/.wf/transient/discover" "$PROJ/backend/internal/auth" "$PROJ/frontend/src/api"
cp "$TMP/model.json" "$PROJ/.wf/transient/discover/model.json"
cp "$TMP/subsystems.json" "$PROJ/.wf/transient/discover/subsystems.json"
cp "$REPO/backend/internal/auth/auth_test.go" "$PROJ/backend/internal/auth/"
cp "$REPO/frontend/src/api/client.test.ts" "$PROJ/frontend/src/api/"
cat > "$PROJ/.wf/config.yaml" <<'EOF'
version: 1
paths:
  discover_model: ".wf/transient/discover/model.json"
  discover_subsystems: ".wf/transient/discover/subsystems.json"
  tests: ["backend", "frontend/src"]
EOF

# 27 — with a config, the bare command derives descriptions AND shipped requirements
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/cfg.html" --config "$PROJ/.wf/config.yaml" \
  >"$TMP/cfg.log" 2>"$TMP/cfg.err"
[ $? -eq 0 ] || bad "config: bare render failed ($(cat "$TMP/cfg.err"))"
grep -q "Owns session issuance" "$TMP/cfg.html" \
  && ok "config: descriptions resolved from paths.discover_subsystems (no flag passed)" \
  || bad "config: descriptions not resolved from config"
grep -q "shall issue a signed token" "$TMP/cfg.html" \
  && ok "config: shipped requirements resolved from paths.tests (no flag passed)" \
  || bad "config: test roots not resolved from config"
python3 - "$TMP/cfg.html" <<'PY' && ok "config: paths.discover_model resolved (exact attribution)" \
  || bad "config: model not resolved from config"
import json, re, sys
d = json.loads(re.search(r'\nconst DATA = (\{.*?\});\n', open(sys.argv[1]).read(), re.S).group(1))
n = {x["id"]: x for x in d["nodes"]}
# path/loc only exist when the model was read.
sys.exit(0 if n["internal/auth"]["path"] == "backend/internal/auth" else 1)
PY

# 28 — an explicit flag still beats the config default
cat > "$TMP/other.json" <<'EOF'
{"nodes": {"go:internal/auth": {"uid":"go:internal/auth","id":"internal/auth","path":"OVERRIDDEN/auth","lang":"go"}}}
EOF
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/ovr.html" --config "$PROJ/.wf/config.yaml" \
  --model "$TMP/other.json" >/dev/null 2>&1
grep -q "OVERRIDDEN/auth" "$TMP/ovr.html" && ok "config: explicit --model overrides the config default" \
  || bad "config: explicit flag did not override config"

# 29 — no config anywhere: still renders, just without the derived context
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/nocfg.html" --config "$TMP/absent.yaml" >/dev/null 2>&1
[ $? -eq 0 ] && ok "config: absent config still renders (standalone use)" \
  || bad "config: absent config broke the render"

# 30b — no test roots at all: "already required here (0)" would be a LIE presented as fact.
#       Say the tags were never scanned, and warn — an unscanned view must not read as an
#       empty one.
cat > "$PROJ/.wf/config3.yaml" <<'EOF'
version: 1
paths:
  discover_model: ".wf/transient/discover/model.json"
EOF
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/noscan.html" --config "$PROJ/.wf/config3.yaml" \
  >/dev/null 2>"$TMP/noscan.err"
grep -qi "no test roots" "$TMP/noscan.err" \
  && ok "no-scan: warns when no test roots are configured" \
  || bad "no-scan: silently reported zero shipped requirements (got: $(cat "$TMP/noscan.err"))"
python3 - "$TMP/noscan.html" <<'PY' && ok "no-scan: view marks tags unscanned, not empty" \
  || bad "no-scan: view claims zero shipped requirements as fact"
import json, re, sys
src = open(sys.argv[1]).read()
d = json.loads(re.search(r'\nconst DATA = (\{.*?\});\n', src, re.S).group(1))
sys.exit(0 if d["tags_scanned"] is False and "not scanned" in src else 1)
PY

# 30 — a config-declared path that does not exist yet (discover not run) warns, never dies:
#      the SA must not be blocked, but must not be told a context-free view is complete.
cat > "$PROJ/.wf/config2.yaml" <<'EOF'
version: 1
paths:
  discover_model: ".wf/transient/discover/gone.json"
  tests: ["backend"]
EOF
echo "$RICH" | python3 "$SCRIPT" --out "$TMP/warn.html" --config "$PROJ/.wf/config2.yaml" \
  >/dev/null 2>"$TMP/warn.err"
[ $? -eq 0 ] && grep -qi "warn" "$TMP/warn.err" \
  && ok "config: missing config-declared model warns but still renders" \
  || bad "config: missing config-declared model should warn and render (got: $(cat "$TMP/warn.err"))"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ]
