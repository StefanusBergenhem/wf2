#!/usr/bin/env bash
#
# Tests for `wf sprint task` (the build envelope: stage narrative → covers → story
# → acceptance → boundaries → grounding, plus the title), `wf sprint materialize` (inline
# the slice's SYS-TC text), and `wf sprint check` (the contract gate over the
# four-section schema: story / acceptance / boundaries / grounding).
# Run: bash tools/cli/tests/sprint_test.sh   (exit 0 = all pass)
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
# edit_sprint <<PY ... — mutate the sprint in place with a python snippet over `d`
edit_sprint() { "$PYTHON" - "$SPRINT" "$(cat)" <<'PY'
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
  sprint: ".wf/sprint.yaml"
  design_slice: ".wf/design-slice.md"
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
limits:
  increments_per_sprint: 4
  tasks_per_increment: 3
YAML
cat > "$PROJ/.wf/CAPABILITIES.yaml" <<'YAML'
version: 1
capabilities:
  - id: "CAP-24"
    statement: "Operators can patch a zone."
YAML
cat > "$PROJ/.wf/LEARNINGS.yaml" <<'YAML'
version: 1
learnings:
  - id: "L-88"
    observation: "test names collide across parallel worktrees"
YAML
printf 'package store\n\nfunc Patch() {}\n' > "$PROJ/store/zones.go"
printf 'package handlers\n\nfunc Mount() {}\n' > "$PROJ/handlers/zones.go"

SPRINT="$PROJ/.wf/sprint.yaml"; SLICE="$PROJ/.wf/design-slice.md"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

write_slice() { cat > "$SLICE" <<'MD'
# Design-slice — zones

**Serves:** CAP-24, L-88

## Design narrative

The zone store gains a patch seam.

## Claimed scope

- **CAP-24** — single-zone patch end to end.

## Increments

### Increment 1 — Zone store seam

Goal: the store can patch one zone.
Checkpoint: a patch round-trips through the service.

### Increment 2 — HTTP surface

Goal: the patch is reachable over HTTP.
Checkpoint: PATCH /zones/{id} demonstrably updates a zone.

## System test cases

- **SYS-TC-1:** end-to-end zone patch
  **Covers:** CAP-24
  - **Given** a stored zone
  - **When** it is patched over HTTP
  - **Then** the change is readable
MD
}

# A structurally clean sprint: one build task in increment 1, one e2e task in
# increment 2 that depends on it.
write_sprint() { cat > "$SPRINT" <<'YAML'
sprint_id: sprint-20260731-zones
tasks:
  - id: T1
    increment: 1
    title: "Zone store patch path"
    depends_on: []
    covers: [CAP-24]
    story: |
      Give the zone store a patch path. Today Patch() is a stub that ignores its
      argument, so nothing downstream can update a stored zone. This task adds the
      partial-update write and the service call that reaches it, leaving the HTTP
      surface to the next increment.
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
  - id: T2
    increment: 2
    depends_on: [T1]
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
# wf sprint materialize — inline the slice's SYS-TC text
# ---------------------------------------------------------------------------

write_slice; write_sprint
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: clean sprint exits 0" || bad "mat exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['tasks']")" = "2" ] && ok "materialize: summary counts tasks" || bad "mat tasks" "$OUT"
[ "$(jget "$OUT" "d['system_tests']")" = "1" ] && ok "materialize: summary counts scenarios" || bad "mat tcs" "$OUT"

# a bare id becomes an entry carrying the slice's scenario text + covered capability
DESC="$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['description']")"
echo "$DESC" | grep -q "end-to-end zone patch" \
  && ok "materialize: SYS-TC description carries the case title" || bad "mat tc title" "$DESC"
echo "$DESC" | grep -q "Given a stored zone" \
  && ok "materialize: SYS-TC description carries the scenario" || bad "mat tc gwt" "$DESC"
[ "$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['covers']")" = "['CAP-24']" ] \
  && ok "materialize: SYS-TC covers read off the slice" || bad "mat tc covers" "$DESC"

# idempotent: a second run leaves the file byte-identical (no no-op write, L-106)
CP1="$(cat "$SPRINT")"; BEFORE_MTIME="$(stat -c %Y "$SPRINT")"
sleep 1
wf sprint materialize --format json >/dev/null
[ "$CP1" = "$(cat "$SPRINT")" ] && ok "materialize: idempotent (second run is a no-op)" || bad "mat idem" "file changed"
[ "$BEFORE_MTIME" = "$(stat -c %Y "$SPRINT")" ] \
  && ok "materialize: a no-op run does not rewrite the file (L-106)" || bad "mat no-op write" "mtime moved"

# refresh: a hand-drifted description is restored from the slice
edit_sprint <<'PY'
d['tasks'][1]['system_tests'][0]['description'] = 'drifted paraphrase'
PY
wf sprint materialize --format json >/dev/null
yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "end-to-end zone patch" \
  && ok "materialize: re-run restores the verbatim scenario" || bad "mat refresh" "$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]")"

# an unknown SYS-TC id → exit 1, sprint untouched
write_sprint
edit_sprint <<'PY'
d['tasks'][1]['system_tests'] = ['SYS-TC-9']
PY
BEFORE="$(cat "$SPRINT")"
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: unknown SYS-TC id → exit 1" || bad "mat tcunk" "rc=$RC $OUT"
[ "$BEFORE" = "$(cat "$SPRINT")" ] && ok "materialize: errors leave the sprint untouched" || bad "mat atomic" "file changed"

# a SYS-TC scenario cut off mid-sentence → exit 1 (L-065)
write_sprint
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("  - **Then** the change is readable", "  - **Then** it reflects the same conflicts, the"))
PY
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: a truncated SYS-TC description → exit 1 (L-065)" || bad "mat trunc" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('truncat' in e.lower() and 'SYS-TC-1' in e for e in d['errors'])")" = "True" ] \
  && ok "materialize: error names the truncated SYS-TC" || bad "mat trunc msg" "$OUT"

# a wrapped case title / Given bullet is joined, not cut at the line break (L-074)
write_sprint
cat > "$SLICE" <<'MD'
# Design-slice — zones

**Serves:** CAP-24

## Increments

### Increment 1 — seam

Goal: x.

### Increment 2 — surface

Goal: y.

## System test cases

- **SYS-TC-1:** end-to-end zone patch across
  the whole stack
  **Covers:** CAP-24
  - **Given** a stored zone with a rule that spans
    more than one line in the slice
  - **When** patched
  - **Then** it works
MD
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: a wrapped SYS-TC scenario is not flagged truncated" || bad "mat wrap exit" "rc=$RC $OUT"
DESC="$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['description']")"
echo "$DESC" | grep -q "patch across the whole stack" \
  && ok "materialize: a wrapped case title is joined" || bad "mat wrap title" "$DESC"
echo "$DESC" | grep -q "spans more than one line in the slice" \
  && ok "materialize: a wrapped Given bullet is joined" || bad "mat wrap gwt" "$DESC"
[ "$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['covers']")" = "['CAP-24']" ] \
  && ok "materialize: a wrapped title does not swallow the Covers line" || bad "mat wrap covers" "$DESC"

# a missing slice → hard error
write_slice; write_sprint; rm -f "$SLICE"
if wf sprint materialize >/dev/null 2>&1; then bad "materialize without slice should fail" "exited 0"; else ok "materialize: missing slice → non-zero exit"; fi

# ---------------------------------------------------------------------------
# wf sprint task — the build envelope
# ---------------------------------------------------------------------------
# Exactly: the increment's stage narrative → covers → story → acceptance →
# boundaries → grounding, plus the task's title (+ the inlined SYS-TC on an e2e
# task). Nothing else travels.

write_slice; write_sprint
wf sprint materialize >/dev/null
OUT="$(wf sprint task T1 --format json)"
[ "$(jget "$OUT" "d['id']")" = "T1" ] && ok "sprint task emits the requested task" || bad "envelope id" "$OUT"
[ "$(jget "$OUT" "list(d)")" = "['id', 'increment', 'title', 'increment_narrative', 'covers', 'story', 'acceptance', 'boundaries', 'grounding']" ] \
  && ok "sprint task emits the envelope fields in reading order" || bad "envelope order" "$OUT"
echo "$OUT" | grep -q "Zone store seam" \
  && ok "envelope carries the increment's stage narrative" || bad "envelope narrative" "$OUT"
[ "$(jget "$OUT" "'Increment 2' in d['increment_narrative']")" = "False" ] \
  && ok "envelope carries ONLY this increment's section" || bad "envelope narrative bleed" "$OUT"
# covers travels: build and review judge scope against the driver this task serves
[ "$(jget "$OUT" "d['covers']")" = "['CAP-24']" ] \
  && ok "envelope carries covers (build/review judge scope against it)" || bad "envelope covers" "$OUT"
[ "$(jget "$OUT" "d['title']")" = "Zone store patch path" ] \
  && ok "envelope carries the task title (the build's commit subject)" || bad "envelope title" "$OUT"
[ "$(jget "$OUT" "'depends_on' in d")" = "False" ] \
  && ok "envelope drops planning metadata (depends_on)" || bad "envelope metadata" "$OUT"

# an e2e task's envelope carries the materialized scenario
OUT="$(wf sprint task T2 --format json)"
[ "$(jget "$OUT" "d['system_tests'][0]['id']")" = "SYS-TC-1" ] \
  && ok "envelope inlines the SYS-TC on an e2e task" || bad "envelope systest" "$OUT"
echo "$OUT" | grep -q "HTTP surface" && ok "e2e envelope carries increment 2's narrative" || bad "envelope inc2" "$OUT"
# title is optional: a task that carries none simply has no title field
[ "$(jget "$OUT" "'title' in d")" = "False" ] \
  && ok "envelope omits title when the task declares none" || bad "envelope no-title" "$OUT"

# --write drops the envelope at the given path (the driver's current_task)
DEST="$PROJ/.wf/transient/current-task.yaml"
WR="$(wf sprint task T1 --write "$DEST" --format json)"
[ "$(jget "$WR" "d['written']")" = "$DEST" ] && ok "sprint task --write reports the path" || bad "write report" "$WR"
grep -q "Zone store seam" "$DEST" && ok "written envelope holds the stage narrative" || bad "write body" "$(cat "$DEST")"

# unknown task → non-zero exit
if wf sprint task NOPE >/dev/null 2>&1; then bad "unknown task should fail" "exited 0"; else ok "sprint task: unknown id → non-zero exit"; fi

# an increment the slice does not declare → hard error (the narrative cannot be built)
edit_sprint <<'PY'
d['tasks'][0]['increment'] = 9
PY
if wf sprint task T1 >/dev/null 2>&1; then bad "undeclared increment should fail" "exited 0"; else ok "sprint task: an increment the slice lacks → non-zero exit"; fi

# ---------------------------------------------------------------------------
# wf sprint task — dependency_commits injection into grounding
# ---------------------------------------------------------------------------

write_sprint; wf sprint materialize >/dev/null
OUT="$(wf sprint task T2 --format json)"
[ "$(jget "$OUT" "any(isinstance(g, dict) for g in d['grounding'])")" = "False" ] \
  && ok "sprint task: no pipeline-state → no dependency_commits" || bad "dep none" "$OUT"

cat > "$PROJ/.wf/transient/pipeline-state.yaml" <<'YAML'
task_states:
  T1: {status: completed, build_commit: abc1234, merge_commit: def5678}
YAML
OUT="$(wf sprint task T2 --format json)"
[ "$(jget "$OUT" "[g for g in d['grounding'] if isinstance(g, dict)][0]['dependency_commits']['T1']")" = "def5678" ] \
  && ok "sprint task injects a merged dep's merge_commit into grounding" || bad "dep inject" "$OUT"

# an unmerged dep contributes nothing
cat > "$PROJ/.wf/transient/pipeline-state.yaml" <<'YAML'
task_states:
  T1: {status: building}
YAML
OUT="$(wf sprint task T2 --format json)"
[ "$(jget "$OUT" "any(isinstance(g, dict) for g in d['grounding'])")" = "False" ] \
  && ok "sprint task omits an unmerged dep from dependency_commits" || bad "dep unmerged" "$OUT"
rm -f "$PROJ/.wf/transient/pipeline-state.yaml"

# ---------------------------------------------------------------------------
# wf sprint check — the contract gate
# ---------------------------------------------------------------------------
has() { jget "$1" "any(f['code']=='$2' for f in d['${3:-errors}'])"; }

write_slice; write_sprint; wf sprint materialize >/dev/null
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: clean sprint exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "check: clean sprint verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "d['increments']")" = "{'1': 1, '2': 1}" ] \
  && ok "check: reports the per-increment task counts" || bad "increment counts" "$OUT"

# --- B8: the story ---
write_sprint
edit_sprint <<'PY'
del d['tasks'][0]['story']
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: errors → exit 1" || bad "fail exit" "rc=$RC $OUT"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a task with no story" || bad "B8" "$OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['story'] = 'Add the patch path.'
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a one-line stub story" || bad "B8-short" "$OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['story'] = 'TODO: describe the change, its flow through the code, and what is new against what already exists here.'
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B8)" = "True" ] && ok "check: B8 flags a placeholder story" || bad "B8-placeholder" "$OUT"

# --- B9: boundaries is ONE section ---
write_sprint
edit_sprint <<'PY'
del d['tasks'][0]['boundaries']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B9)" = "True" ] && ok "check: B9 flags a task with no boundaries" || bad "B9" "$OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['out_of_scope'] = ['the HTTP handler']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B9)" = "True" ] && ok "check: B9 flags a separate out_of_scope field" || bad "B9-oos" "$OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['fixed_interfaces'] = ['ZoneStore.Patch']
d['tasks'][1]['interface_contract_ref'] = 'Zone port'
PY
OUT="$(wf sprint check --format json)"
[ "$(jget "$OUT" "sum(1 for f in d['errors'] if f['code']=='B9')")" -ge 2 ] \
  && ok "check: B9 flags fixed_interfaces and interface_contract_ref" || bad "B9-iface" "$OUT"

# the retired REQ-layer fields are errors, not silently-ignored extras
write_sprint
edit_sprint <<'PY'
d['tasks'][0]['implementation_notes'] = ['follow the retry pattern']
d['tasks'][0]['requirements'] = [{'id': 'REQ-1', 'statement': 'x'}]
d['tasks'][0]['serves'] = ['CAP-24']
d['tasks'][0]['files_to_touch'] = ['store/zones.go']
PY
OUT="$(wf sprint check --format json)"
[ "$(jget "$OUT" "sum(1 for f in d['errors'] if f['code']=='B9')")" = "4" ] \
  && ok "check: B9 flags every retired REQ-layer field" || bad "B9-retired" "$OUT"
[ "$(jget "$OUT" "any('implementation_notes' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: B9 names the retired field" || bad "B9-retired msg" "$OUT"

# --- B10: an acceptance entry IS the requirement ---
write_sprint
edit_sprint <<'PY'
del d['tasks'][0]['acceptance'][0]['criterion']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B10)" = "True" ] && ok "check: B10 flags an acceptance entry with no criterion" || bad "B10" "$OUT"

write_sprint
edit_sprint <<'PY'
del d['tasks'][0]['acceptance'][1]['id']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B10)" = "True" ] && ok "check: B10 flags an acceptance entry with no id" || bad "B10-id" "$OUT"

# --- B3: every criterion carries proof ---
write_sprint
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 flags a criterion with no test and no verified_by" || bad "B3" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
d['tasks'][0]['acceptance'][0]['verified_by'] = 'inspection'
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" B3)" = "False" ] && ok "check: B3 honours verified_by: inspection" || bad "B3-inspection" "$OUT"
[ "$RC" -eq 0 ] && ok "check: an inspection-verified criterion still exits 0" || bad "B3-inspection exit" "rc=$RC $OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = []
d['tasks'][0]['acceptance'][0]['verified_by'] = '  '
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 rejects a blank verified_by" || bad "B3-blank" "$OUT"

# --- B7: test entry shape ---
write_sprint
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = [{'level': 'e2e', 'target': 'x'}]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags an unknown test level" || bad "B7-level" "$OUT"

write_sprint
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][0]['tests'] = [{'level': 'unit'}]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags a test with no target" || bad "B7-target" "$OUT"

write_sprint
edit_sprint <<'PY'
del d['tasks'][0]['acceptance'][1]['tests'][0]['seam']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags an integration test with no seam" || bad "B7-seam" "$OUT"
[ "$(jget "$OUT" "any('seam' in f['msg'] for f in d['errors'] if f['code']=='B7')")" = "True" ] \
  && ok "check: B7 names the missing seam" || bad "B7-seam msg" "$OUT"

# a unit test needs no seam
write_sprint
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B7)" = "False" ] && ok "check: B7 does not demand a seam from a unit test" || bad "B7-unit" "$OUT"

# --- B6/C4: the e2e task shape ---
write_sprint
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B6)" = "True" ] && ok "check: B6 flags an unmaterialized system_tests id" || bad "B6" "$OUT"
[ "$(jget "$OUT" "any('materialize' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: B6 names the materialize step" || bad "B6 hint" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x', 'tests': [{'level': 'unit', 'target': 'y'}]}]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C4)" = "True" ] && ok "check: C4 flags an e2e task carrying acceptance" || bad "C4" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['system_tests'][0]['covers'] = ['L-88']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C4)" = "True" ] && ok "check: C4 flags a SYS-TC covering something other than a CAP" || bad "C4-cap" "$OUT"

# --- C6/C16: the trace anchor ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
del d['tasks'][0]['covers']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 flags a task with no covers" || bad "C6" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['covers'] = ['REQ-1']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 rejects a covers id that is not CAP/L" || bad "C6-req" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['covers'] = ['CAP-99']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C16)" = "True" ] && ok "check: C16 flags a covers id absent from the working set" || bad "C16" "$OUT"

# a learning id in the working set is a legitimate driver
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['covers'] = ['CAP-24', 'L-88']
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C16)" = "False" ] && [ "$RC" -eq 0 ] \
  && ok "check: C16 accepts an open learning id" || bad "C16-learning" "rc=$RC $OUT"

# no capabilities/learnings file resolved → C16 warns rather than failing every task
cat > "$PROJ/.wf/config-nodrivers.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/sprint.yaml"
  design_slice: ".wf/design-slice.md"
limits:
  increments_per_sprint: 4
  tasks_per_increment: 3
YAML
write_sprint; wf sprint materialize >/dev/null
OUT="$("$PYTHON" "$WF" sprint check --config "$PROJ/.wf/config-nodrivers.yaml" --format json)"; RC=$?
[ "$(has "$OUT" C16 warnings)" = "True" ] && [ "$RC" -eq 0 ] \
  && ok "check: C16 warns (never fails) when no driver file resolves" || bad "C16-nofile" "rc=$RC $OUT"

# --- C11: grounding points into code that exists ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['grounding'] = ['store/zones.go:PatchOrdinalMap — the write path']
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: C11 exits 1" || bad "C11 exit" "rc=$RC $OUT"
[ "$(has "$OUT" C11)" = "True" ] && ok "check: C11 flags a symbol its cited file does not carry" || bad "C11" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['grounding'] = ['store/nowhere.go:42 — the mount point']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C11)" = "True" ] && ok "check: C11 flags a grounding path that is not a file" || bad "C11-file" "$OUT"

# a line-number pointer into a real file resolves
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['grounding'] = ['store/zones.go:3 — the stub this task replaces', 'no external seam, e.g. no queue']
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C11)" = "False" ] && [ "$RC" -eq 0 ] \
  && ok "check: C11 accepts a line pointer and ignores prose tokens" || bad "C11-ok" "rc=$RC $OUT"

# --- C12: a declared dependency commit names a declared dependency ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['grounding'].append({'dependency_commits': {'T9': 'abc1234'}})
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C12)" = "True" ] && ok "check: C12 flags a dependency_commit for an undeclared dep" || bad "C12" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['grounding'].append({'dependency_commits': {'T1': 'abc1234'}})
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C12)" = "False" ] && [ "$RC" -eq 0 ] \
  && ok "check: C12 accepts a dependency_commit for a declared dep" || bad "C12-ok" "rc=$RC $OUT"

# --- C13: test-target collisions across contracts (L-088) ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['system_tests'] = []
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x',
  'tests': [{'level': 'unit', 'target': 'store/other_test.go:TestPatchWritesNamedField'}]}]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C13)" = "True" ] && ok "check: C13 flags two contracts targeting one test name in a package" || bad "C13" "$OUT"
[ "$(jget "$OUT" "any('TestPatchWritesNamedField' in f['msg'] for f in d['errors'] if f['code']=='C13')")" = "True" ] \
  && ok "check: C13 names the colliding target" || bad "C13 msg" "$OUT"

# the same name in a DIFFERENT package is not a collision
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['system_tests'] = []
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'x',
  'tests': [{'level': 'unit', 'target': 'handlers/zones_test.go:TestPatchWritesNamedField'}]}]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C13)" = "False" ] && ok "check: C13 keys on the package, not the bare name" || bad "C13-pkg" "$OUT"

# one task reusing its own target across criteria is not a collision
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['acceptance'][1]['tests'][0]['target'] = 'store/zones_test.go:TestPatchWritesNamedField'
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C13)" = "False" ] && ok "check: C13 ignores one task reusing its own target" || bad "C13-self" "$OUT"

# --- C1: depends_on integrity ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['depends_on'] = ['T99']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C1)" = "True" ] && ok "check: C1 flags a dangling depends_on" || bad "C1-dangling" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['depends_on'] = ['T2']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C1)" = "True" ] && ok "check: C1 flags a cycle" || bad "C1-cycle" "$OUT"
[ "$(jget "$OUT" "any('increment' in f['msg'] for f in d['errors'] if f['code']=='C1')")" = "True" ] \
  && ok "check: C1 flags a dependency in a LATER increment" || bad "C1-forward" "$OUT"

# --- C14: the per-increment task cap ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
base = dict(d['tasks'][0])
for n in (3, 4, 5):
    t = dict(base); t['id'] = f'T{n}'
    t['acceptance'] = [{'id': 'AC-1', 'criterion': 'the store writes the named field',
                        'tests': [{'level': 'unit', 'target': f'store/zones_test.go:TestFill{n}'}]}]
    d['tasks'].append(t)
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C14)" = "True" ] && ok "check: C14 flags an increment past limits.tasks_per_increment" || bad "C14" "$OUT"
[ "$(jget "$OUT" "any('tasks_per_increment' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "check: C14 names the cap it read from config" || bad "C14 msg" "$OUT"

# --- C15: every task traces to a declared increment ---
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['increment'] = 7
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C15)" = "True" ] && ok "check: C15 flags an increment the slice does not declare" || bad "C15" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
del d['tasks'][0]['increment']
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C15)" = "True" ] && ok "check: C15 flags a task with no increment" || bad "C15-none" "$OUT"

# --- C17: the file is cumulative — earlier increments' entries are never rewritten ---
# each increment's TL APPENDS; a reused id or a block out of file order is a rewrite
write_sprint; wf sprint materialize >/dev/null
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C17)" = "False" ] \
  && ok "check: C17 passes a cumulative file whose increments are in order" || bad "C17-clean" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
dup = dict(d['tasks'][0]); dup['increment'] = 2
d['tasks'].append(dup)
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C17)" = "True" ] && [ "$RC" -eq 1 ] \
  && ok "check: C17 flags a task id reused across increments" || bad "C17-dup" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('T1' in f['msg'] for f in d['errors'] if f['code']=='C17')")" = "True" ] \
  && ok "check: C17 names the reused id" || bad "C17-dup id" "$OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'] = [d['tasks'][1], d['tasks'][0]]
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C17)" = "True" ] \
  && ok "check: C17 flags an earlier increment's task positioned after a later one" || bad "C17-order" "$OUT"
[ "$(jget "$OUT" "any('T1' in f['msg'] for f in d['errors'] if f['code']=='C17')")" = "True" ] \
  && ok "check: C17 names the out-of-order task" || bad "C17-order id" "$OUT"

# an append one indent level too deep lands the whole task block inside the previous
# task's grounding list — YAML takes it silently, so the check must not
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][0]['grounding'].append({'id': 'T3', 'increment': 2, 'story': 'swallowed'})
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C17)" = "True" ] \
  && ok "check: C17 flags a task block swallowed into a mis-indented append" || bad "C17-swallow" "$OUT"
[ "$(jget "$OUT" "any('indent' in f['msg'] for f in d['errors'] if f['code']=='C17')")" = "True" ] \
  && ok "check: C17 names the indentation as the cause" || bad "C17-swallow msg" "$OUT"

# the one grounding mapping the schema does allow stays legal
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['grounding'].append({'dependency_commits': {'T1': 'abc123'}})
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C17)" = "False" ] \
  && ok "check: C17 leaves a dependency_commits grounding entry alone" || bad "C17-depcommits" "$OUT"

# --- A2: a slice scenario with no e2e task ---
# while the sprint is only partly decomposed it is a warning; once every declared
# increment has tasks it is an error.
write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'] = [t for t in d['tasks'] if t['id'] != 'T2']
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" A2 warnings)" = "True" ] && [ "$RC" -eq 0 ] \
  && ok "check: A2 warns while later increments are undecomposed" || bad "A2-warn" "rc=$RC $OUT"

write_sprint; wf sprint materialize >/dev/null
edit_sprint <<'PY'
d['tasks'][1]['system_tests'] = []
d['tasks'][1]['boundaries'] = 'Out of scope: bulk patch.'
d['tasks'][1]['acceptance'] = [{'id': 'AC-1', 'criterion': 'the route is mounted',
  'tests': [{'level': 'unit', 'target': 'handlers/zones_test.go:TestMountsPatch'}]}]
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" A2)" = "True" ] && [ "$RC" -eq 1 ] \
  && ok "check: A2 errors once every increment is decomposed" || bad "A2-err" "rc=$RC $OUT"

# --- A0: no slice on disk ---
write_slice; write_sprint; wf sprint materialize >/dev/null; rm -f "$SLICE"
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" A0 warnings)" = "True" ] && ok "check: A0 warns when the slice is absent" || bad "A0" "$OUT"
[ "$RC" -eq 0 ] && ok "check: a missing slice still exits 0 when the contracts are clean" || bad "A0 exit" "rc=$RC $OUT"

# --strict promotes warnings to failure
OUT="$(wf sprint check --strict --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: --strict fails when only warnings are present" || bad "strict exit" "rc=$RC $OUT"

# ---------------------------------------------------------------------------
# wf sprint prune — drop ONE increment's tasks from the cumulative file
# ---------------------------------------------------------------------------
# A slice re-cut invalidates the increment it re-cuts and nothing else: every other
# increment's entries are the sprint's merge record and survive byte-for-byte.

STATE="$PROJ/.wf/transient/pipeline-state.yaml"
EXPECT="$PROJ/expected-sprint.yaml"
write_prune_sprint() { cat > "$SPRINT" <<'YAML'
# sprint — cumulative across the sprint's increments; each Tech Lead appends.
sprint_id: sprint-20260731-zones
tasks:
  - id: T1
    increment: 1
    depends_on: []
    covers: [CAP-24]
    story: |
      Give the zone store a patch path — today Patch() ignores its argument, so nothing
      downstream can update a stored zone. This task adds the partial-update write.
    acceptance:
      - id: AC-1
        criterion: "When a patch names a field, the store writes only that field."
        tests:
          - {level: unit, target: "store/zones_test.go:TestPatchWritesNamedField"}
    boundaries: |
      Out of scope: the HTTP handler. Read-only: handlers/zones.go.
    grounding:
      - "store/zones.go:Patch — the stub this task replaces"

  - id: T2
    increment: 2
    depends_on: [T1]
    covers: [CAP-24]
    story: |
      Mount the patch on the HTTP surface and prove it end to end — the handler package
      already mounts the read routes, so this adds the PATCH route.
    boundaries: |
      Out of scope: bulk patch. Read-only: store/zones.go.
    grounding:
      - "handlers/zones.go:Mount — the current mount point"
    system_tests: [SYS-TC-1]
YAML
}
# the file with increment 2's block cut out — what a prune of increment 2 must leave
keep_through_t1() { "$PYTHON" - "$SPRINT" "$EXPECT" <<'PY'
import sys
src, dest = sys.argv[1], sys.argv[2]
open(dest, 'w').write(open(src).read().split("  - id: T2")[0])
PY
}

write_prune_sprint; rm -f "$STATE"; keep_through_t1
OUT="$(wf sprint prune --increment 2 --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "prune: exits 0" || bad "prune exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "prune: verdict pass" || bad "prune verdict" "$OUT"
[ "$(jget "$OUT" "d['pruned']")" = "['T2']" ] && ok "prune: reports the task ids it removed" || bad "prune ids" "$OUT"
cmp -s "$SPRINT" "$EXPECT" \
  && ok "prune: every other increment's entry survives byte-for-byte" || bad "prune bytes" "$(diff "$EXPECT" "$SPRINT")"
[ "$(yget "$SPRINT" "[t['id'] for t in d['tasks']]")" = "['T1']" ] \
  && ok "prune: the re-cut increment's task is gone" || bad "prune left" "$(cat "$SPRINT")"
head -1 "$SPRINT" | grep -q "^# sprint — cumulative" \
  && ok "prune: the file's comments and unicode survive (L-106)" || bad "prune comments" "$(head -1 "$SPRINT")"

# the pruned tasks' run state goes with them — the state keys by id sprint-wide, so a
# re-cut id that kept its entry would come back pre-completed
write_prune_sprint
cat > "$STATE" <<'YAML'
task_states:
  T1: {status: building, attempt_counter: 1}
  T2: {status: approved, build_commit: bbb1111, attempt_counter: 2}
YAML
OUT="$(wf sprint prune --increment 2 --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "prune: exits 0 with a live run state" || bad "prune state exit" "rc=$RC $OUT"
[ "$(yget "$STATE" "sorted(d['task_states'])")" = "['T1']" ] \
  && ok "prune: the re-cut task's run state is dropped with its contract" || bad "prune state" "$(cat "$STATE")"
[ "$(yget "$STATE" "d['task_states']['T1']['attempt_counter']")" = "1" ] \
  && ok "prune: a surviving task's run state is untouched" || bad "prune state keep" "$(cat "$STATE")"
[ "$(jget "$OUT" "d['task_states_pruned']")" = "['T2']" ] \
  && ok "prune: reports the run-state entries it dropped" || bad "prune state report" "$OUT"

# a merged increment is a fact: refuse, and mutate nothing
write_prune_sprint
cat > "$STATE" <<'YAML'
task_states:
  T1: {status: completed, build_commit: abc1234, merge_commit: def5678}
  T2: {status: building}
YAML
BEFORE="$(cat "$SPRINT")"; BEFORE_STATE="$(cat "$STATE")"
OUT="$(wf sprint prune --increment 1 --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "prune: a merged increment → exit 1" || bad "prune merged exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "fail" ] && ok "prune: a merged increment verdict fail" || bad "prune merged verdict" "$OUT"
[ "$(jget "$OUT" "any('T1' in e for e in d['errors'])")" = "True" ] \
  && ok "prune: the refusal names the merged task" || bad "prune merged msg" "$OUT"
[ "$BEFORE" = "$(cat "$SPRINT")" ] && ok "prune: a refusal leaves the sprint untouched" || bad "prune merged sprint" "changed"
[ "$BEFORE_STATE" = "$(cat "$STATE")" ] && ok "prune: a refusal leaves the run state untouched" || bad "prune merged state" "changed"

# completed with no merge commit recorded is the same fact
write_prune_sprint
cat > "$STATE" <<'YAML'
task_states:
  T2: {status: completed, build_commit: bbb1111}
YAML
OUT="$(wf sprint prune --increment 2 --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "prune: a completed task refuses the prune too" || bad "prune completed" "rc=$RC $OUT"

# an increment with no tasks: a clean no-op, and no rewrite (L-106)
write_prune_sprint; rm -f "$STATE"
BEFORE_MTIME="$(stat -c %Y "$SPRINT")"
sleep 1
OUT="$(wf sprint prune --increment 3 --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT" "d['pruned']")" = "[]" ] \
  && ok "prune: an increment with no tasks is a clean no-op" || bad "prune noop" "rc=$RC $OUT"
[ "$BEFORE_MTIME" = "$(stat -c %Y "$SPRINT")" ] \
  && ok "prune: a no-op does not rewrite the file (L-106)" || bad "prune noop write" "mtime moved"

# draining every increment leaves an empty-but-valid file, header intact
write_prune_sprint; rm -f "$STATE"
wf sprint prune --increment 1 >/dev/null; wf sprint prune --increment 2 >/dev/null
[ "$(yget "$SPRINT" "d.get('tasks') or []")" = "[]" ] \
  && ok "prune: draining every task leaves an empty-but-valid sprint file" || bad "prune empty" "$(cat "$SPRINT")"
[ "$(yget "$SPRINT" "d['sprint_id']")" = "sprint-20260731-zones" ] \
  && ok "prune: the sprint header survives an emptied task list" || bad "prune header" "$(cat "$SPRINT")"

# no sprint file at all (a re-cut before any Tech Lead wrote contracts) is a no-op
rm -f "$SPRINT"
OUT="$(wf sprint prune --increment 1 --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT" "d['pruned']")" = "[]" ] \
  && ok "prune: no sprint file on disk is a clean no-op" || bad "prune nofile" "rc=$RC $OUT"

echo ""
echo "  sprint: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
