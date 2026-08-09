#!/usr/bin/env bash
#
# Tests for `wf stage check` (the single gate over the cut: its design header and
# that stage's task contracts), `wf stage task` (the build envelope: the stage's
# shape → covers → the four contract sections), and `wf stage materialize` (inline
# the scenario text the capabilities/learnings files carry).
# Run: bash tools/cli/tests/stage_test.sh   (exit 0 = all pass)
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
yget() { "$PYTHON" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"; }
has() { jget "$1" "any(f['code']=='$2' for f in d['${3:-errors}'])"; }
# edit_stage <<PY ... — mutate the stage in place with a python snippet over `d`
edit_stage() { "$PYTHON" - "$STAGE" "$(cat)" <<'PY'
import sys, yaml
p, snippet = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p))
exec(snippet)
open(p, 'w').write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
PY
}
# edit_caps <<PY ... — the same over the capabilities file
edit_caps() { "$PYTHON" - "$PROJ/.wf/CAPABILITIES.yaml" "$(cat)" <<'PY'
import sys, yaml
p, snippet = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p))
exec(snippet)
open(p, 'w').write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
PY
}

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf/transient" "$PROJ/store" "$PROJ/handlers"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  architecture: ".wf/architecture.md"
  design_issues: ".wf/transient/design-issues.yaml"
  transient: ".wf/transient"
  discover_model: ".wf/transient/discover/model.json"
limits:
  tasks_per_stage: 3
YAML
STAGE="$PROJ/.wf/transient/stage.yaml"
ARCH="$PROJ/.wf/architecture.md"
MODEL="$PROJ/.wf/transient/discover/model.json"
ISSUES="$PROJ/.wf/transient/design-issues.yaml"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

printf 'package store\n\nfunc Patch() {}\n' > "$PROJ/store/zones.go"
printf 'package handlers\n\nfunc Mount() {}\n' > "$PROJ/handlers/zones.go"

# The durable why — each in-flight entry carrying the scenario set that proves it.
# A scenario is nested under what it proves; it carries no `covers:` of its own.
write_capabilities() { cat > "$PROJ/.wf/CAPABILITIES.yaml" <<'YAML'
version: 1
capabilities:
  - id: "CAP-24"
    statement: "Operators can patch a zone."
    system_tests:
      - id: SYS-TC-1
        title: "end-to-end zone patch"
        given: "a stored zone"
        when: "it is patched over HTTP"
        then: "the change is readable"
YAML
}
cat > "$PROJ/.wf/LEARNINGS.yaml" <<'YAML'
version: 1
learnings:
  - id: "L-88"
    observation: "test names collide across parallel worktrees"
    system_tests:
      - id: SYS-TC-2
        title: "two contracts never claim one test name"
        given: "two tasks in one stage"
        when: "the gate runs"
        then: "a shared target is refused"
YAML
write_capabilities

# The derived half of the A12 inventory: a minimal model.json in the shape spine.py
# `merge` writes — nodes keyed by `<lang>:<id>`, each carrying `id` and `path`.
write_model() { mkdir -p "$(dirname "$MODEL")"; "$PYTHON" - "$MODEL" <<'PY'
import json, sys
def node(cid, path, loc):
    return {"uid": f"go:{cid}", "id": cid, "name": cid.rsplit("/", 1)[-1], "path": path,
            "loc": loc, "kind": "package", "lang": "go", "module": "example.com/demo",
            "synopsis": "", "has_doc": False, "has_tests": True, "types": [],
            "functions": [], "deps": []}
nodes = {n["uid"]: n for n in (node("internal/zones", "backend/internal/zones", 220),
                               node("internal/store", "backend/internal/store", 140))}
json.dump({"languages": ["go"], "nodes": nodes, "order": sorted(nodes),
           "title": "demo (go)",
           "meta": {"generated_at": "2026-01-01T00:00:00Z", "source_sha": "abc1234"}},
          open(sys.argv[1], "w"), indent=2)
PY
}
# The durable half: the DELTA only — structure the repo has not reached.
write_arch() { cat > "$ARCH" <<'MD'
# Architecture map

## Components

- **internal/httpapi** (planned) — Will mount the zone routes. Depends on: internal/zones.
MD
}
write_model; write_arch

# One cut: the design header the build envelope carries, and two tasks with no
# dependency between them — one build task, one e2e task landing the scenario.
write_stage() { cat > "$STAGE" <<'YAML'
# STAGE — one cut. TRANSIENT: written fresh, archived and deleted at stage merge.
stage: 7
serves: [CAP-24, L-88]
goal: "the zone patch path exists and is reachable over HTTP"
grounded_in: [".wf/transient/discover/brief.md"]
allocation:
  - component: internal/zones
    does: "patch one zone and expose it"
flow: |
  The handler reads the patch body, the zone service validates it, and the store
  writes only the named fields before the handler re-reads the row.
checkpoint: "after this stage, PATCH /zones/{id} demonstrably updates a stored zone"
supersessions: []
nfr: []
authz: []
soundness: {boundary_srp: "the store owns persistence, the handler owns transport"}
decisions: ["Assumption — a patch never creates a zone"]
tasks:
  - id: S7-T1
    title: "Zone store patch path"
    covers: [CAP-24]
    story: |
      Give the zone store a patch path. Today Patch() is a stub that ignores its
      argument, so nothing downstream can update a stored zone. This task adds the
      partial-update write and the service call that reaches it, leaving the HTTP
      surface to its sibling task.
    acceptance:
      - id: AC-1
        criterion: "When a patch names a field, the store writes only that field."
        tests:
          - {level: unit, target: "store/zones_test.go:TestPatchWritesNamedField"}
      - id: AC-2
        criterion: "When a patch names an unknown zone, the store returns not-found."
        tests:
          - {level: integration, seam: "postgres", target: "store/zones_test.go:TestPatchUnknownZone"}
    boundaries: |
      Out of scope: the HTTP handler and the router. Read-only: handlers/zones.go.
      Fixed interface: ZoneStore.Patch keeps its current signature.
    grounding:
      - "store/zones.go:Patch — the stub this task replaces"
  - id: S7-T2
    title: "the zone patch HTTP surface"
    covers: [CAP-24]
    story: |
      Mount the patch on the HTTP surface and prove it end to end. The handler
      package already mounts the read routes, so this task adds the PATCH route and
      the system test that drives it through the real stack.
    boundaries: |
      Out of scope: bulk patch. Read-only: store/zones.go.
    grounding:
      - "handlers/zones.go:Mount — the current mount point"
    system_tests: [SYS-TC-1]
YAML
}

# ---------------------------------------------------------------------------
# wf stage materialize — inline the scenario text from capabilities/learnings
# ---------------------------------------------------------------------------

write_stage
OUT="$(wf stage materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: clean stage exits 0" || bad "mat exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['stage']")" = "7" ] && ok "materialize: summary reports the stage number" || bad "mat stage" "$OUT"
[ "$(jget "$OUT" "d['tasks']")" = "2" ] && ok "materialize: summary counts tasks" || bad "mat tasks" "$OUT"
[ "$(jget "$OUT" "d['system_tests']")" = "1" ] && ok "materialize: summary counts scenarios" || bad "mat tcs" "$OUT"

DESC="$(yget "$STAGE" "d['tasks'][1]['system_tests'][0]['description']")"
echo "$DESC" | grep -q "end-to-end zone patch" \
  && ok "materialize: description carries the scenario title" || bad "mat title" "$DESC"
echo "$DESC" | grep -q "Given a stored zone" \
  && ok "materialize: description carries the Given" || bad "mat given" "$DESC"
echo "$DESC" | grep -q "Then the change is readable" \
  && ok "materialize: description carries the Then" || bad "mat then" "$DESC"
[ "$(yget "$STAGE" "d['tasks'][1]['system_tests'][0]['id']")" = "SYS-TC-1" ] \
  && ok "materialize: a bare id becomes an entry keeping its id" || bad "mat id" "$DESC"

# the file it rewrites is the file the role authored: comments and unicode survive
head -1 "$STAGE" | grep -q "^# STAGE — one cut" \
  && ok "materialize: the stage's leading comment survives the rewrite" || bad "mat comment" "$(head -1 "$STAGE")"
grep -q "^  story: |$" "$STAGE" \
  && ok "materialize: a contract's prose stays in literal block style" || bad "mat block" "$(sed -n '1,40p' "$STAGE")"

# idempotent: a second run leaves the file byte-identical, and writes nothing (L-106)
CP1="$(cat "$STAGE")"; BEFORE_MTIME="$(stat -c %Y "$STAGE")"
sleep 1
wf stage materialize --format json >/dev/null
[ "$CP1" = "$(cat "$STAGE")" ] && ok "materialize: idempotent (second run is a no-op)" || bad "mat idem" "changed"
[ "$BEFORE_MTIME" = "$(stat -c %Y "$STAGE")" ] \
  && ok "materialize: a no-op run does not rewrite the file (L-106)" || bad "mat no-op write" "mtime moved"

# refresh: a hand-drifted description is restored from the capability
edit_stage <<'PY'
d['tasks'][1]['system_tests'][0]['description'] = 'drifted paraphrase'
PY
wf stage materialize --format json >/dev/null
yget "$STAGE" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "end-to-end zone patch" \
  && ok "materialize: re-run restores the verbatim scenario" || bad "mat refresh" "$(yget "$STAGE" "d['tasks'][1]['system_tests'][0]")"

# a scenario nested under a LEARNING materializes the same way
write_stage
edit_stage <<'PY'
d['tasks'][1]['system_tests'] = ['SYS-TC-2']
PY
OUT="$(wf stage materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: a learning's scenario resolves too" || bad "mat learning" "rc=$RC $OUT"
yget "$STAGE" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "two contracts never claim one test name" \
  && ok "materialize: the learning's scenario text is inlined" || bad "mat learning text" "$(cat "$STAGE")"

# an id no entry carries → exit 1, stage untouched
write_stage
edit_stage <<'PY'
d['tasks'][1]['system_tests'] = ['SYS-TC-9']
PY
BEFORE="$(cat "$STAGE")"
OUT="$(wf stage materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: an unknown SYS-TC id → exit 1" || bad "mat unknown" "rc=$RC $OUT"
[ "$BEFORE" = "$(cat "$STAGE")" ] && ok "materialize: errors leave the stage untouched" || bad "mat atomic" "changed"

# a scenario cut off mid-sentence → exit 1 (L-065): build stamps it verbatim on the tag
write_stage
edit_caps <<'PY'
d['capabilities'][0]['system_tests'][0]['then'] = 'it reflects the same conflicts, the'
PY
OUT="$(wf stage materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: a truncated scenario → exit 1 (L-065)" || bad "mat trunc" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('truncat' in e.lower() and 'SYS-TC-1' in e for e in d['errors'])")" = "True" ] \
  && ok "materialize: the error names the truncated scenario" || bad "mat trunc msg" "$OUT"
write_capabilities

# a multi-line scenario field is joined, not cut at the line break
write_stage
edit_caps <<'PY'
d['capabilities'][0]['system_tests'][0]['given'] = 'a stored zone with a rule that spans\nmore than one line'
PY
OUT="$(wf stage materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: a wrapped scenario field is not flagged truncated" || bad "mat wrap" "rc=$RC $OUT"
yget "$STAGE" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "spans more than one line" \
  && ok "materialize: a wrapped scenario field is joined to one line" || bad "mat wrap join" "$(cat "$STAGE")"
write_capabilities

# ---------------------------------------------------------------------------
# wf stage task — the build envelope
# ---------------------------------------------------------------------------
# Exactly: the stage's shape (goal / allocation / flow / checkpoint) → covers →
# the four contract sections, plus the title. Nothing else travels.

write_stage; wf stage materialize >/dev/null
OUT="$(wf stage task S7-T1 --format json)"
[ "$(jget "$OUT" "d['id']")" = "S7-T1" ] && ok "stage task emits the requested task" || bad "env id" "$OUT"
[ "$(jget "$OUT" "list(d)")" = "['id', 'title', 'goal', 'allocation', 'flow', 'checkpoint', 'covers', 'story', 'acceptance', 'boundaries', 'grounding']" ] \
  && ok "stage task emits the envelope fields in reading order" || bad "env order" "$OUT"
[ "$(jget "$OUT" "d['goal']")" = "the zone patch path exists and is reachable over HTTP" ] \
  && ok "envelope carries the stage goal" || bad "env goal" "$OUT"
[ "$(jget "$OUT" "d['allocation'][0]['component']")" = "internal/zones" ] \
  && ok "envelope carries the stage allocation" || bad "env alloc" "$OUT"
echo "$OUT" | grep -q "the zone service validates it" \
  && ok "envelope carries the stage flow" || bad "env flow" "$OUT"
echo "$OUT" | grep -q "demonstrably updates a stored zone" \
  && ok "envelope carries the stage checkpoint" || bad "env checkpoint" "$OUT"
[ "$(jget "$OUT" "d['covers']")" = "['CAP-24']" ] \
  && ok "envelope carries covers (build/review judge scope against it)" || bad "env covers" "$OUT"
[ "$(jget "$OUT" "d['title']")" = "Zone store patch path" ] \
  && ok "envelope carries the task title (the build's commit subject)" || bad "env title" "$OUT"
[ "$(jget "$OUT" "any(k in d for k in ('increment', 'increment_narrative', 'depends_on', 'dependency_commits'))")" = "False" ] \
  && ok "envelope carries no increment, narrative or dependency metadata" || bad "env retired" "$OUT"
[ "$(jget "$OUT" "'prior_attempt' in d")" = "False" ] \
  && ok "envelope omits prior_attempt when the flag is absent" || bad "env prior absent" "$OUT"

# an e2e task's envelope carries the materialized scenario, and no acceptance
OUT="$(wf stage task S7-T2 --format json)"
[ "$(jget "$OUT" "d['system_tests'][0]['id']")" = "SYS-TC-1" ] \
  && ok "envelope inlines the SYS-TC on an e2e task" || bad "env systest" "$OUT"
[ "$(jget "$OUT" "list(d)[-1]")" = "system_tests" ] \
  && ok "envelope puts system_tests last on an e2e task" || bad "env systest order" "$OUT"

# --prior-attempt rides last, verbatim: without it wf-build's Red phase misreads
# prior-attempt code as a vacuous test
OUT="$(wf stage task S7-T2 --prior-attempt 'branch S6-T3; review rejected at attempt 3' --format json)"
[ "$(jget "$OUT" "d['prior_attempt']")" = "branch S6-T3; review rejected at attempt 3" ] \
  && ok "stage task --prior-attempt sets the field verbatim" || bad "env prior" "$OUT"
[ "$(jget "$OUT" "list(d)[-1]")" = "prior_attempt" ] \
  && ok "envelope puts prior_attempt last" || bad "env prior order" "$OUT"

# a task with no title still renders (B11 is the gate that refuses it, not the envelope)
edit_stage <<'PY'
del d['tasks'][1]['title']
PY
OUT="$(wf stage task S7-T2 --format json)"
[ "$(jget "$OUT" "'title' in d")" = "False" ] \
  && ok "envelope omits title when the task declares none" || bad "env no-title" "$OUT"

# --write drops the envelope at the given path (the driver's current_task)
write_stage; wf stage materialize >/dev/null
DEST="$PROJ/.wf/transient/current-task.yaml"
WR="$(wf stage task S7-T1 --write "$DEST" --format json)"
[ "$(jget "$WR" "d['written']")" = "$DEST" ] && ok "stage task --write reports the path" || bad "write report" "$WR"
grep -q "the zone service validates it" "$DEST" \
  && ok "written envelope holds the stage's shape" || bad "write body" "$(cat "$DEST")"

# unknown task → non-zero exit
if wf stage task NOPE >/dev/null 2>&1; then bad "unknown task should fail" "exited 0"; else ok "stage task: unknown id → non-zero exit"; fi

# ---------------------------------------------------------------------------
# wf stage check — the single gate
# ---------------------------------------------------------------------------

write_stage; wf stage materialize >/dev/null
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: clean stage exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "check: clean stage verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "d['stage']")" = "7" ] && ok "check: the result carries the stage number" || bad "result stage" "$OUT"
[ "$(jget "$OUT" "d['serves']")" = "['CAP-24', 'L-88']" ] \
  && ok "check: the result carries serves (the driver reads it here)" || bad "result serves" "$OUT"
[ "$(jget "$OUT" "'increments' in d")" = "False" ] \
  && ok "check: the result carries no increments key" || bad "result increments" "$OUT"
[ "$(jget "$OUT" "d['tasks']")" = "2" ] && ok "check: the result counts the stage's tasks" || bad "result tasks" "$OUT"

# --- B8: the story ---
write_stage
edit_stage <<'PY'
del d['tasks'][0]['story']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: errors → exit 1" || bad "fail exit" "rc=$RC $OUT"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a task with no story" || bad "B8" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['story'] = 'Add the patch path.'
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a one-line stub story" || bad "B8-short" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['story'] = 'TODO: describe the change, its flow through the code, and what is new against what already exists here.'
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a placeholder story" || bad "B8-placeholder" "$OUT"

# --- B11: the title, which the build makes its commit subject ---
write_stage
edit_stage <<'PY'
del d['tasks'][0]['title']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B11)" = "True" ] && ok "check: B11 flags a task with no title" || bad "B11" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][1]['title'] = '   '
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B11)" = "True" ] && ok "check: B11 flags a blank title" || bad "B11-blank" "$OUT"

# --- B9: boundaries is ONE section, and the retired fields stay retired ---
write_stage
edit_stage <<'PY'
del d['tasks'][0]['boundaries']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B9)" = "True" ] && ok "check: B9 flags a task with no boundaries" || bad "B9" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['out_of_scope'] = ['the HTTP handler']
d['tasks'][1]['interface_contract_ref'] = 'Zone port'
PY
OUT="$(wf stage check --format json)"
[ "$(jget "$OUT" "sum(1 for f in d['errors'] if f['code']=='B9')")" -ge 2 ] \
  && ok "check: B9 flags a separate out_of_scope and interface_contract_ref" || bad "B9-oos" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['implementation_notes'] = ['follow the retry pattern']
d['tasks'][0]['requirements'] = [{'id': 'REQ-1', 'statement': 'x'}]
d['tasks'][0]['serves'] = ['CAP-24']
d['tasks'][0]['files_to_touch'] = ['store/zones.go']
PY
OUT="$(wf stage check --format json)"
[ "$(jget "$OUT" "sum(1 for f in d['errors'] if f['code']=='B9')")" = "4" ] \
  && ok "check: B9 flags every retired REQ-layer field" || bad "B9-retired" "$OUT"
[ "$(jget "$OUT" "any('implementation_notes' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: B9 names the retired field" || bad "B9-retired msg" "$OUT"

# --- B10: an acceptance entry IS the requirement ---
write_stage
edit_stage <<'PY'
del d['tasks'][0]['acceptance'][0]['criterion']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B10)" = "True" ] && ok "check: B10 flags an acceptance entry with no criterion" || bad "B10" "$OUT"

write_stage
edit_stage <<'PY'
del d['tasks'][0]['acceptance'][1]['id']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B10)" = "True" ] && ok "check: B10 flags an acceptance entry with no id" || bad "B10-id" "$OUT"

# --- B3: every criterion carries proof ---
write_stage
edit_stage <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 flags a criterion with no test and no verified_by" || bad "B3" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
d['tasks'][0]['acceptance'][0]['verified_by'] = 'inspection'
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$(has "$OUT" B3)" = "False" ] && ok "check: B3 honours verified_by: inspection" || bad "B3-inspection" "$OUT"
[ "$RC" -eq 0 ] && ok "check: an inspection-verified criterion still exits 0" || bad "B3-inspection exit" "rc=$RC $OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
d['tasks'][0]['acceptance'][0]['verified_by'] = '  '
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 rejects a blank verified_by" || bad "B3-blank" "$OUT"

# --- B7: test entry shape ---
write_stage
edit_stage <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = [{'level': 'e2e', 'target': 'x'}]
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags an unknown test level" || bad "B7-level" "$OUT"

write_stage
edit_stage <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = [{'level': 'unit'}]
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags a test with no target" || bad "B7-target" "$OUT"

write_stage
edit_stage <<'PY'
del d['tasks'][0]['acceptance'][1]['tests'][0]['seam']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags an integration test with no seam" || bad "B7-seam" "$OUT"
[ "$(jget "$OUT" "any('seam' in f['msg'] for f in d['errors'] if f['code']=='B7')")" = "True" ] \
  && ok "check: B7 names the missing seam" || bad "B7-seam msg" "$OUT"

write_stage; wf stage materialize >/dev/null
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B7)" = "False" ] && ok "check: B7 does not demand a seam from a unit test" || bad "B7-unit" "$OUT"

# --- B6/C4: the e2e task shape ---
write_stage
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B6)" = "True" ] && ok "check: B6 flags an unmaterialized system_tests id" || bad "B6" "$OUT"
[ "$(jget "$OUT" "any('materialize' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: B6 names the materialize step" || bad "B6 hint" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['system_tests'][0]['description'] = ''
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" B6)" = "True" ] && ok "check: B6 flags an entry with an empty description" || bad "B6-empty" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x', 'tests': [{'level': 'unit', 'target': 'y'}]}]
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C4)" = "True" ] && ok "check: C4 flags an e2e task carrying acceptance" || bad "C4" "$OUT"

# --- C6/C16: the trace anchor ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
del d['tasks'][0]['covers']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 flags a task with no covers" || bad "C6" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['covers'] = ['REQ-1']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 rejects a covers id that is not CAP/L" || bad "C6-req" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['covers'] = ['CAP-99']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C16)" = "True" ] && ok "check: C16 flags a covers id absent from the working set" || bad "C16" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['covers'] = ['CAP-24', 'L-88']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$(has "$OUT" C16)" = "False" ] && [ "$RC" -eq 0 ] \
  && ok "check: C16 accepts an open learning id" || bad "C16-learning" "rc=$RC $OUT"

# no capabilities/learnings file resolved → C16 warns rather than failing every task
cat > "$PROJ/.wf/config-nodrivers.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
  architecture: ".wf/architecture.md"
  transient: ".wf/transient"
  discover_model: ".wf/transient/discover/model.json"
limits:
  tasks_per_stage: 3
YAML
write_stage; wf stage materialize >/dev/null
OUT="$("$PYTHON" "$WF" stage check --config "$PROJ/.wf/config-nodrivers.yaml" --format json)"; RC=$?
[ "$(has "$OUT" C16 warnings)" = "True" ] && [ "$RC" -eq 0 ] \
  && ok "check: C16 warns (never fails) when no driver file resolves" || bad "C16-nofile" "rc=$RC $OUT"

# --- C11: grounding points into code that exists ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['grounding'] = ['store/zones.go:PatchOrdinalMap — the write path']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: C11 exits 1" || bad "C11 exit" "rc=$RC $OUT"
[ "$(has "$OUT" C11)" = "True" ] && ok "check: C11 flags a symbol its cited file does not carry" || bad "C11" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['grounding'] = ['store/nowhere.go:42 — the mount point']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C11)" = "True" ] && ok "check: C11 flags a grounding path that is not a file" || bad "C11-file" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['grounding'] = ['store/zones.go:3 — the stub this task replaces', 'no external seam, e.g. no queue']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$(has "$OUT" C11)" = "False" ] && [ "$RC" -eq 0 ] \
  && ok "check: C11 accepts a line pointer and ignores prose tokens" || bad "C11-ok" "rc=$RC $OUT"

# --- C13: test-target collisions across contracts (L-088) ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['system_tests'] = []
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x',
  'tests': [{'level': 'unit', 'target': 'store/other_test.go:TestPatchWritesNamedField'}]}]
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C13)" = "True" ] && ok "check: C13 flags two contracts targeting one test name in a package" || bad "C13" "$OUT"
[ "$(jget "$OUT" "any('TestPatchWritesNamedField' in f['msg'] for f in d['errors'] if f['code']=='C13')")" = "True" ] \
  && ok "check: C13 names the colliding target" || bad "C13 msg" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['system_tests'] = []
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x',
  'tests': [{'level': 'unit', 'target': 'handlers/zones_test.go:TestPatchWritesNamedField'}]}]
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C13)" = "False" ] && ok "check: C13 keys on the package, not the bare name" || bad "C13-pkg" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['acceptance'][1]['tests'][0]['target'] = 'store/zones_test.go:TestPatchWritesNamedField'
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C13)" = "False" ] && ok "check: C13 ignores one task reusing its own target" || bad "C13-self" "$OUT"

# --- C14: the width cap reads limits.tasks_per_stage ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
base = dict(d['tasks'][0])
for n in (3, 4):
    t = dict(base); t['id'] = f'S7-T{n}'
    t['acceptance'] = [{'id': 'AC-1', 'criterion': 'the store writes the named field',
                        'tests': [{'level': 'unit', 'target': f'store/zones_test.go:TestFill{n}'}]}]
    d['tasks'].append(t)
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C14)" = "True" ] && ok "check: C14 flags a stage past limits.tasks_per_stage" || bad "C14" "$OUT"
[ "$(jget "$OUT" "any('tasks_per_stage' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: C14 names the cap it read from config" || bad "C14 msg" "$OUT"

# the cap is a config knob with no in-code default
cat > "$PROJ/.wf/config-nolimits.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
YAML
write_stage; wf stage materialize >/dev/null
if "$PYTHON" "$WF" stage check --config "$PROJ/.wf/config-nolimits.yaml" >/dev/null 2>&1; then
  bad "missing limits should fail" "exited 0"
else
  ok "check: unset limits.tasks_per_stage → non-zero exit"
fi

# --- C19: a stage IS the independent set — no edges inside it ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['depends_on'] = ['S7-T1']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(has "$OUT" C19)" = "True" ] \
  && ok "check: C19 flags a task carrying depends_on" || bad "C19" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('next stage' in f['msg'] for f in d['errors'] if f['code']=='C19')")" = "True" ] \
  && ok "check: C19 routes the dependent task to the next stage" || bad "C19 msg" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][0]['increment'] = 1
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C19)" = "True" ] && ok "check: C19 flags a task carrying increment" || bad "C19-incr" "$OUT"

# --- C20: the file partition, warned not errored ---
write_stage; wf stage materialize >/dev/null
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" C20 warnings)" = "False" ] \
  && ok "check: C20 is silent when the tasks ground on different files" || bad "C20-clean" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['grounding'] = ['store/zones.go:Patch — the write this route calls']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$(has "$OUT" C20 warnings)" = "True" ] && [ "$RC" -eq 0 ] \
  && ok "check: C20 warns (never errors) on overlapping grounding paths" || bad "C20" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('store/zones.go' in f['msg'] for f in d['warnings'] if f['code']=='C20')")" = "True" ] \
  && ok "check: C20 names the shared file" || bad "C20 msg" "$OUT"

# --- A7: the serves header (the driver's PR-body input) ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
del d['serves']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: a stage with no serves exits 1" || bad "A7 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A7)" = "True" ] && ok "check: A7 flags a stage with no serves key" || bad "A7" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['serves'] = ['the zone work']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" A7)" = "True" ] && ok "check: A7 flags a serves entry that is not a CAP/L id" || bad "A7-bare" "$OUT"

# --- A6: the design fields the build envelope carries ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
del d['goal']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(has "$OUT" A6)" = "True" ] \
  && ok "check: A6 flags a stage with no goal" || bad "A6-goal" "rc=$RC $OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['flow'] = '<how the behaviour moves through those components, wiring included>'
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" A6)" = "True" ] && ok "check: A6 flags a template placeholder flow" || bad "A6-flow" "$OUT"
[ "$(jget "$OUT" "any('flow' in f['msg'] for f in d['errors'] if f['code']=='A6')")" = "True" ] \
  && ok "check: A6 names the unauthored field" || bad "A6-flow msg" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['checkpoint'] = 'TBD'
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" A6)" = "True" ] && ok "check: A6 flags a TBD checkpoint" || bad "A6-checkpoint" "$OUT"

# the template authors the checkpoint as a LIST of observable facts — an unauthored
# one must not pass for want of being a list
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['checkpoint'] = ['<X demonstrably works — observed by <how>>']
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" A6)" = "True" ] && ok "check: A6 reads a list-valued checkpoint placeholder" || bad "A6-list" "$OUT"

write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['checkpoint'] = ['PATCH /zones/{id} updates a zone — observed by the e2e run']
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A6)" = "False" ] \
  && ok "check: A6 accepts an authored list-valued checkpoint" || bad "A6-list-ok" "rc=$RC $OUT"

# --- A12: the architecture bind ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['allocation'] = [{'component': 'internal/router', 'does': 'route it'}]
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: a component in neither source exits 1" || bad "A12 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A12)" = "True" ] && ok "check: A12 flags a component neither source carries" || bad "A12" "$OUT"
[ "$(jget "$OUT" "any('internal/router' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "check: A12 names the unknown component" || bad "A12 name" "$OUT"
[ "$(jget "$OUT" "any('SA session' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "check: A12 routes the structure change through an SA session" || bad "A12 route" "$OUT"

# a `(planned)` map entry the repo has not built is a legitimate allocation
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['allocation'] = [{'component': 'internal/httpapi', 'does': 'mount PATCH /zones/{id}'},
                   {'component': 'backend/internal/store', 'does': 'persist it'}]
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "check: A12 accepts a planned map entry and a repo-relative path" || bad "A12-planned" "rc=$RC $OUT"

# no derived inventory + an unresolved component → one error naming the run that settles it
write_stage; wf stage materialize >/dev/null
mv "$MODEL" "$MODEL.bak"
edit_stage <<'PY'
d['allocation'] = [{'component': 'internal/router', 'does': 'route it'}]
PY
OUT="$(wf stage check --format json)"
[ "$(jget "$OUT" "len([f for f in d['errors'] if f['code']=='A12'])")" = "1" ] \
  && ok "check: an absent discover model yields one A12 error" || bad "A12-nomodel" "$OUT"
[ "$(jget "$OUT" "any('wf-discover' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "check: the absent-inventory error names wf-discover" || bad "A12-nomodel msg" "$OUT"
mv "$MODEL.bak" "$MODEL"

# a stage that allocates nothing is silent — there is nothing to bind
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['allocation'] = []
PY
OUT="$(wf stage check --format json)"
[ "$(has "$OUT" A12)" = "False" ] && ok "check: A12 is silent on a stage with no allocation" || bad "A12-silent" "$OUT"

# --- C18: a design issue drains by naming its successor task ---
write_stage; wf stage materialize >/dev/null
cat > "$ISSUES" <<'YAML'
issues:
  - id: DI-3
    task_id: S6-T2
    status: resolved
    fix_kind: contract_rewrite
    task: S9-T4
    summary: "the patch write had no seam to test against"
YAML
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(has "$OUT" C18)" = "True" ] \
  && ok "check: C18 flags a resolution naming a task this stage lacks" || bad "C18" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('S9-T4' in f['msg'] for f in d['errors'] if f['code']=='C18')")" = "True" ] \
  && ok "check: C18 names the missing successor task" || bad "C18 msg" "$OUT"

"$PYTHON" - "$ISSUES" <<'PY'
import sys
p = sys.argv[1]
open(p, 'w').write(open(p).read().replace("task: S9-T4", "task: S7-T2"))
PY
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" C18)" = "False" ] \
  && ok "check: C18 passes when the successor task is in the stage" || bad "C18-ok" "rc=$RC $OUT"

# A resolution with no successor at all is the same empty claim as one naming a
# task the stage lacks — the role marked it resolved without authoring anything.
cat > "$ISSUES" <<'YAML'
issues:
  - id: DI-5
    task_id: S6-T2
    status: resolved
    fix_kind: contract_rewrite
    summary: "answered in the next cut"
YAML
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(has "$OUT" C18)" = "True" ] \
  && ok "check: C18 flags a resolution naming no successor task" || bad "C18-nosucc" "rc=$RC $OUT"

cat > "$ISSUES" <<'YAML'
issues:
  - id: DI-4
    status: resolved
    fix_kind: no_change
    task: S9-T4
    summary: "already covered by the merged patch path"
  - id: DI-5
    status: open
    task: S9-T9
    summary: "still open, so it authors nothing yet"
YAML
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" C18)" = "False" ] \
  && ok "check: C18 exempts fix_kind no_change and an open entry" || bad "C18-nochange" "rc=$RC $OUT"

printf 'issues: [ this is not: valid yaml\n' > "$ISSUES"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" C18)" = "False" ] \
  && ok "check: C18 is silent on an unreadable design-issues file" || bad "C18-bad" "rc=$RC $OUT"
rm -f "$ISSUES"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" C18)" = "False" ] \
  && ok "check: C18 is silent when no design-issues file exists" || bad "C18-absent" "rc=$RC $OUT"

# --- A4/A5: ADR citations, resolved against every ADR set in the tree ---
mkdir -p "$PROJ/.wf/adrs" "$PROJ/doc/design/adrs"
printf -- '---\nid: ADR-011\ntitle: baseline edited in place\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-011.md"
printf -- '---\nid: ADR-013\ntitle: zone port\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-013.md"
printf '# in-process goroutine workers\n' > "$PROJ/doc/design/adrs/ADR-011.md"

cite() { write_stage; wf stage materialize >/dev/null; "$PYTHON" - "$STAGE" "$1" <<'PY'
import sys, yaml
p, text = sys.argv[1], sys.argv[2]
d = yaml.safe_load(open(p))
d['decisions'] = [text]
open(p, 'w').write(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
PY
}

cite "Constraint — bound by ADR-013"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: a resolvable ADR citation exits 0" || bad "adr ok" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "zone port" ] \
  && ok "check: a citation echoes the ADR's own title" || bad "adr title" "$OUT"

cite "Constraint — bound by ADR-777"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(jget "$OUT" "any(f['code']=='A4' and 'ADR-777' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: A4 names an ADR no file defines" || bad "A4" "rc=$RC $OUT"

cite "Constraint — baseline edited in place (ADR-011)"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 1 ] && [ "$(jget "$OUT" "any(f['code']=='A5' and '.wf/adrs/ADR-011.md' in f['msg'] and 'doc/design/adrs/ADR-011.md' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: A5 lists both defining paths for a colliding id" || bad "A5" "rc=$RC $OUT"

cite "Constraint — baseline edited in place (doc/design/adrs/ADR-011)"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "in-process goroutine workers" ] \
  && ok "check: a path-qualified colliding id resolves to its own title" || bad "A5-qualified" "rc=$RC $OUT"

# the transient tree holds the per-task worktrees, each a whole checkout carrying its
# own copy of every ADR — walking it reports the repo as colliding with itself
mkdir -p "$PROJ/.wf/transient/worktrees/S7-T1/.wf/adrs"
cp "$PROJ/.wf/adrs/ADR-013.md" "$PROJ/.wf/transient/worktrees/S7-T1/.wf/adrs/ADR-013.md"
cite "Constraint — bound by ADR-013"
OUT="$(wf stage check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT" "any(f['code']=='A5' for f in d['errors'])")" = "False" ] \
  && ok "check: a worktree's ADR copy is not a second ADR set" || bad "adr worktree" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('transient' in s for s in d['adr_sets'])")" = "False" ] \
  && ok "check: adr_sets never reports a path inside transient" || bad "adr sets" "$OUT"
rm -rf "$PROJ/.wf/transient/worktrees"

# --- --strict promotes warnings to failure ---
write_stage; wf stage materialize >/dev/null
edit_stage <<'PY'
d['tasks'][1]['grounding'] = ['store/zones.go:Patch — the write this route calls']
PY
OUT="$(wf stage check --strict --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: --strict fails when only warnings are present" || bad "strict" "rc=$RC $OUT"

# --- no stage on disk is a mechanical failure, not a verdict ---
rm -f "$STAGE"
if wf stage check >/dev/null 2>&1; then
  bad "missing stage should exit non-zero" "exited 0"
else
  RC=$?
  [ "$RC" -eq 2 ] && ok "check: a missing stage file exits 2" || bad "missing stage rc" "rc=$RC"
fi
if wf stage materialize >/dev/null 2>&1; then
  bad "materialize without a stage should fail" "exited 0"
else
  ok "materialize: a missing stage file → non-zero exit"
fi

echo ""
echo "  stage: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
