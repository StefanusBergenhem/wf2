#!/usr/bin/env bash
#
# Tests for `wf sprint task` (task-contract extractor), `wf sprint materialize`
# (inline the slice's verbatim fields into the sprint), and `wf sprint check`
# (the analyze gate: mechanical consistency/coverage over the sprint DAG).
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

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf/transient"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/sprint.yaml"
  design_slice: ".wf/design-slice.md"
YAML
cat > "$PROJ/.wf/sprint.yaml" <<'YAML'
tasks:
  - id: T1
    title: "First task"
    files_to_touch: ["a.go"]
  - id: T2
    title: "Second task"
    depends_on: [T1]
YAML
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

# ---------------------------------------------------------------------------
# wf sprint task — the task-contract extractor
# ---------------------------------------------------------------------------

# emit a single task's contract to stdout
OUT="$(wf sprint task T1 --format json)"
[ "$(jget "$OUT" "d['id']")" = "T1" ] && ok "sprint task emits the requested task" || bad "emit id" "$OUT"
[ "$(jget "$OUT" "d['files_to_touch']")" = "['a.go']" ] && ok "sprint task carries the full contract" || bad "emit contract" "$OUT"

# --write drops the contract at the given path (the orchestrator's current_task)
DEST="$PROJ/.wf/transient/current-task.yaml"
WR="$(wf sprint task T2 --write "$DEST" --format json)"
[ "$(jget "$WR" "d['written']")" = "$DEST" ] && ok "sprint task --write reports the path" || bad "write report" "$WR"
[ -f "$DEST" ] && ok "sprint task --write creates the file" || bad "write file" "missing"
grep -q "Second task" "$DEST" && ok "written contract holds the task body" || bad "write body" "$(cat "$DEST")"

# unknown task → non-zero exit
if wf sprint task NOPE >/dev/null 2>&1; then bad "unknown task should fail" "exited 0"; else ok "sprint task: unknown id → non-zero exit"; fi

# ---------------------------------------------------------------------------
# wf sprint materialize — inline the slice's verbatim fields into the sprint
# ---------------------------------------------------------------------------
# The TL authors THIN fields (covers ids, interface_contract_ref, system_tests
# ids); materialize inlines the statements, per-requirement drivers, the serves
# union, the interface-contract shapes, and the SYS-TC scenario — verbatim from
# the slice, mechanically.
SPRINT="$PROJ/.wf/sprint.yaml"; SLICE="$PROJ/.wf/design-slice.md"

write_mat_slice() { cat > "$SLICE" <<'MD'
# Design-slice — widget

**Serves:** CAP-1

## Component requirements

- **REQ-1** — the widget does X  *(owner: core · CAP-1)*
- **REQ-2** — the widget rejects Y, and this statement wraps
  across lines  *(owner: core · CAP-2 · ADR-13, extends REQ-9)*
- **REQ-3** — the tooling gate holds  *(owner: tooling · L-2 · D-4; drain-by-inspection)*

## System test cases

- **SYS-TC-1:** end-to-end widget flow
  **Covers:** CAP-1, CAP-2
  - **Given** a widget
  - **When** driven
  - **Then** it works

## Interface contracts

- **Widget port (core)** — serves REQ-1; bound by ADR-13

      type Widget interface {
          Do(x string) error
      }

- **Widget store (repo)** — serves REQ-2; bound by ADR-13

      type WidgetStore interface {
          Put(w Widget) error
      }
MD
}

write_thin_sprint() { cat > "$SPRINT" <<'YAML'
sprint_id: sprint-20260717-widget
tasks:
  - id: T1
    title: "Build the widget"
    depends_on: []
    covers: [REQ-1, REQ-2, REQ-3]
    files_to_touch: ["core/widget.go", "core/widget_test.go"]
    acceptance_criteria:
      - id: REQ-1.AC-1
        check: "given X, returns ok"
        tests:
          - level: unit
            target: "core/widget.go:Widget"
    interface_contract_ref: "Widget port (core)"
  - id: T2
    title: "System test: widget flow"
    depends_on: [T1]
    covers: []
    files_to_touch: ["e2e/widget_test.go"]
    acceptance_criteria: []
    system_tests:
      - id: SYS-TC-1
YAML
}

# a clean thin sprint materializes: exit 0 + summary
write_mat_slice; write_thin_sprint
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: clean thin sprint exits 0" || bad "mat exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['tasks']")" = "2" ] && ok "materialize: summary counts tasks" || bad "mat tasks" "$OUT"
[ "$(jget "$OUT" "d['requirements']")" = "3" ] && ok "materialize: summary counts requirements" || bad "mat reqs" "$OUT"

# requirements[] carries the verbatim statement + per-requirement driver
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][0]['statement']")" = "the widget does X" ] \
  && ok "materialize: statement inlined verbatim" || bad "mat stmt" "$(yget "$SPRINT" "d['tasks'][0]['requirements']")"
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][0]['serves']")" = "CAP-1" ] \
  && ok "materialize: per-requirement driver read off the slice" || bad "mat serves" "$(yget "$SPRINT" "d['tasks'][0]['requirements']")"

# a wrapped statement is joined to one line
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][1]['statement']")" = "the widget rejects Y, and this statement wraps across lines" ] \
  && ok "materialize: wrapped slice statement joined" || bad "mat wrap" "$(yget "$SPRINT" "d['tasks'][0]['requirements'][1]")"

# the driver is the FIRST id token after the owner — annotations (ADR-13, D-4) are not drivers
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][1]['serves']")" = "CAP-2" ] \
  && ok "materialize: first-driver rule skips trailing ADR annotation" || bad "mat drv2" "$(yget "$SPRINT" "d['tasks'][0]['requirements'][1]")"
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][2]['serves']")" = "L-2" ] \
  && ok "materialize: first-driver rule picks L-2 over D-4" || bad "mat drv3" "$(yget "$SPRINT" "d['tasks'][0]['requirements'][2]")"

# task serves = union of the requirements' drivers, in covers order
[ "$(yget "$SPRINT" "d['tasks'][0]['serves']")" = "['CAP-1', 'CAP-2', 'L-2']" ] \
  && ok "materialize: task serves is the drivers' union" || bad "mat union" "$(yget "$SPRINT" "d['tasks'][0]['serves']")"

# interface_contract_ref → the slice's contract block, verbatim, ref retained
yget "$SPRINT" "d['tasks'][0]['interface_contract']" | grep -q "type Widget interface" \
  && ok "materialize: interface contract inlined from ref" || bad "mat ic" "$(yget "$SPRINT" "d['tasks'][0]")"
[ "$(yget "$SPRINT" "d['tasks'][0]['interface_contract_ref']")" = "Widget port (core)" ] \
  && ok "materialize: interface_contract_ref retained (idempotence anchor)" || bad "mat icref" "$(yget "$SPRINT" "d['tasks'][0]")"

# e2e task: SYS-TC description + covers + serves from the slice case
yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "end-to-end widget flow" \
  && ok "materialize: SYS-TC description carries the case title" || bad "mat tc title" "$(yget "$SPRINT" "d['tasks'][1]")"
yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['description']" | grep -q "Given a widget" \
  && ok "materialize: SYS-TC description carries the scenario" || bad "mat tc gwt" "$(yget "$SPRINT" "d['tasks'][1]")"
[ "$(yget "$SPRINT" "d['tasks'][1]['system_tests'][0]['covers']")" = "['CAP-1', 'CAP-2']" ] \
  && ok "materialize: SYS-TC covers read off the slice" || bad "mat tc covers" "$(yget "$SPRINT" "d['tasks'][1]")"
[ "$(yget "$SPRINT" "d['tasks'][1]['serves']")" = "['CAP-1', 'CAP-2']" ] \
  && ok "materialize: e2e serves = the case's covered capabilities" || bad "mat tc serves" "$(yget "$SPRINT" "d['tasks'][1]")"

# idempotent: a second run leaves the file byte-identical
CP1="$(cat "$SPRINT")"
wf sprint materialize --format json >/dev/null
[ "$CP1" = "$(cat "$SPRINT")" ] && ok "materialize: idempotent (second run is a no-op)" || bad "mat idem" "file changed"

# refresh: a hand-drifted statement is restored from the slice
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['requirements'][0]['statement']='drifted paraphrase'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
wf sprint materialize --format json >/dev/null
[ "$(yget "$SPRINT" "d['tasks'][0]['requirements'][0]['statement']")" = "the widget does X" ] \
  && ok "materialize: re-run restores the verbatim statement" || bad "mat refresh" "$(yget "$SPRINT" "d['tasks'][0]['requirements'][0]")"

# a covers id absent from the slice → exit 1, nothing written
write_thin_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['covers']=['REQ-1','REQ-99']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
BEFORE="$(cat "$SPRINT")"
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: unknown REQ id → exit 1" || bad "mat unk exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('REQ-99' in e for e in d['errors'])")" = "True" ] && ok "materialize: error names the unknown id" || bad "mat unk msg" "$OUT"
[ "$BEFORE" = "$(cat "$SPRINT")" ] && ok "materialize: errors leave the sprint untouched" || bad "mat unk atomic" "file changed"

# an unknown interface_contract_ref → exit 1
write_thin_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['interface_contract_ref']='No such contract'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: unknown interface_contract_ref → exit 1" || bad "mat icunk" "rc=$RC $OUT"
[ "$(jget "$OUT" "any('Widget port (core)' in e for e in d['errors'])")" = "True" ] \
  && ok "materialize: unknown ref error lists the valid contract names" || bad "mat icunk names" "$OUT"

# a list-valued ref inlines every named contract
write_thin_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['interface_contract_ref']=['Widget port (core)','Widget store (repo)']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
wf sprint materialize --format json >/dev/null
IC="$(yget "$SPRINT" "d['tasks'][0]['interface_contract']")"
echo "$IC" | grep -q "type Widget interface" && echo "$IC" | grep -q "type WidgetStore interface" \
  && ok "materialize: list ref inlines every named contract" || bad "mat iclist" "$IC"

# a fix-mode follow-up covering a requirement OUTSIDE the slice keeps its
# hand-carried entry (statement copied from the origin task) instead of erroring
write_thin_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'].append({
  'id':'T3','title':'Fix merged defect','depends_on':[],
  'covers':['REQ-77'],
  'requirements':[{'id':'REQ-77','statement':'shipped behaviour the merged code violates','serves':'CAP-9'}],
  'files_to_touch':['core/fix.go','core/fix_test.go'],
  'acceptance_criteria':[{'id':'REQ-77.AC-1','check':'x','tests':[{'level':'unit','target':'core/fix.go:F'}]}],
})
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "materialize: off-slice carried requirement → exit 0" || bad "mat carry exit" "rc=$RC $OUT"
[ "$(yget "$SPRINT" "d['tasks'][2]['requirements'][0]['statement']")" = "shipped behaviour the merged code violates" ] \
  && ok "materialize: carried entry survives untouched" || bad "mat carry keep" "$(yget "$SPRINT" "d['tasks'][2]")"
[ "$(yget "$SPRINT" "d['tasks'][2]['serves']")" = "['CAP-9']" ] \
  && ok "materialize: carried entry's driver joins the serves union" || bad "mat carry serves" "$(yget "$SPRINT" "d['tasks'][2]")"

# an unknown SYS-TC id → exit 1
write_thin_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['system_tests']=[{'id':'SYS-TC-9'}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint materialize --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "materialize: unknown SYS-TC id → exit 1" || bad "mat tcunk" "rc=$RC $OUT"

# a missing slice → hard error
write_thin_sprint; rm -f "$SLICE"
if wf sprint materialize >/dev/null 2>&1; then bad "materialize without slice should fail" "exited 0"; else ok "materialize: missing slice → non-zero exit"; fi

# ---------------------------------------------------------------------------
# wf sprint check — the analyze gate
# ---------------------------------------------------------------------------
# Each test rewrites $PROJ/.wf/sprint.yaml (and, where a Family-A check needs it,
# design-slice.md), then runs `wf sprint check`. `has` reports whether a finding
# code appears among errors (or warnings, arg 3 = warnings).
has() { jget "$1" "any(f['code']=='$2' for f in d['${3:-errors}'])"; }

write_clean_slice() { cat > "$SLICE" <<'MD'
# Design-slice — widget

**Serves:** CAP-1

## Component requirements

- **REQ-1** — the widget does X  *(owner: core · CAP-1)*
- **REQ-2** — the widget rejects Y  *(owner: core · CAP-2)*

## System test cases

- **SYS-TC-1:** end-to-end widget flow
  **Covers:** CAP-1
  - **Given** a widget
  - **When** driven
  - **Then** it works
MD
}

# A structurally clean sprint that satisfies every error-severity check.
write_clean_sprint() { cat > "$SPRINT" <<'YAML'
sprint_id: sprint-20260708-widget
tasks:
  - id: T1
    depends_on: []
    covers: [REQ-1, REQ-2]
    requirements:
      - id: REQ-1
        statement: "the widget does X"
        serves: CAP-1
      - id: REQ-2
        statement: "the widget rejects Y"
        serves: CAP-2
    serves: [CAP-1, CAP-2]
    files_to_touch: ["core/widget.go", "core/widget_test.go"]
    acceptance_criteria:
      - id: REQ-1.AC-1
        check: "given X, returns ok"
        tests:
          - level: unit
            target: "core/widget.go:Widget"
      - id: REQ-1.AC-2
        check: "given bad X, returns error"
        tests:
          - level: unit
            target: "core/widget.go:Widget"
      - id: REQ-2.AC-1
        check: "given Y, rejects"
        tests:
          - level: unit
            target: "core/widget.go:Widget"
  - id: T2
    depends_on: [T1]
    covers: []
    requirements: []
    serves: [CAP-1]
    files_to_touch: ["e2e/widget_test.go"]
    acceptance_criteria: []
    system_tests:
      - id: SYS-TC-1
        description: "end-to-end widget flow — Given a widget; When driven; Then it works"
        covers: [CAP-1]
YAML
}

# clean sprint + slice -> pass, zero errors, exit 0
write_clean_slice; write_clean_sprint
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: clean sprint exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "check: clean sprint verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "len(d['errors'])")" = "0" ] && ok "check: clean sprint has no errors" || bad "clean errors" "$OUT"

# B3 — an AC with no tests and no verified_by is the silent hole
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
for ac in d['tasks'][0]['acceptance_criteria']:
    if ac['id']=='REQ-2.AC-1':
        ac['tests']=[]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: errors -> exit 1" || bad "fail exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "fail" ] && ok "check: B3 verdict fail" || bad "B3 verdict" "$OUT"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 flags an AC with no test" || bad "B3" "$OUT"

# B2 — a covered REQ with no AC at all
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['acceptance_criteria']=[ac for ac in t['acceptance_criteria'] if not ac['id'].startswith('REQ-2.')]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B2)" = "True" ] && ok "check: B2 flags a REQ with no acceptance criterion" || bad "B2" "$OUT"

# B6 — an interface_contract_ref the materializer has not inlined yet
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['interface_contract_ref']='Widget port (core)'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: B6 ref without contract -> exit 1" || bad "B6 exit" "rc=$RC $OUT"
[ "$(has "$OUT" B6)" = "True" ] && ok "check: B6 flags an unmaterialized interface_contract_ref" || bad "B6" "$OUT"
[ "$(jget "$OUT" "any('materialize' in f['msg'] for f in d['errors'])")" = "True" ] && ok "check: B6 names the materialize step" || bad "B6 hint" "$OUT"

# B6 — a system_tests entry with no description (materializer not run)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['system_tests']=[{'id':'SYS-TC-1'}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B6)" = "True" ] && ok "check: B6 flags an undescribed system_tests entry" || bad "B6-tc" "$OUT"

# B7 — a test entry with an unknown level
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['acceptance_criteria'][0]['tests']=[{'level':'e2e'}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B7)" = "True" ] && ok "check: B7 flags an unknown test level" || bad "B7" "$OUT"

# A1 — a slice requirement covered by no task (drop REQ-2 from the task's covers+ACs)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['covers']=['REQ-1']
t['requirements']=[r for r in t['requirements'] if r['id']=='REQ-1']
t['acceptance_criteria']=[ac for ac in t['acceptance_criteria'] if ac['id'].startswith('REQ-1.')]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" A1)" = "True" ] && ok "check: A1 flags a dropped slice requirement" || bad "A1" "$OUT"

# A2 — a slice SYS-TC carried by no e2e task
write_clean_slice; write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks']=[t for t in d['tasks'] if t['id']!='T2']  # remove the e2e task
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" A2)" = "True" ] && ok "check: A2 flags a SYS-TC with no e2e task" || bad "A2" "$OUT"

# C1 — a dependency cycle
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['depends_on']=['T2']  # T1<->T2 cycle
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C1)" = "True" ] && ok "check: C1 flags a dependency cycle" || bad "C1" "$OUT"

# C1 — a depends_on referencing an unknown task
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['depends_on']=['T99']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C1)" = "True" ] && ok "check: C1 flags a dangling depends_on" || bad "C1-dangling" "$OUT"

# C2 — a unit target file outside files_to_touch (planning-quality hint: files_to_touch
# is the ADVISORY expected write set, so a missing declaration warns, never fails)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['other.go']  # target core/widget.go not declared
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C2 warnings)" = "True" ] && ok "check: C2 warns on a unit target outside files_to_touch" || bad "C2" "$OUT"
[ "$(has "$OUT" C2)" = "False" ] && ok "check: C2 is a warning, not an error" || bad "C2-sev" "$OUT"
[ "$RC" -eq 0 ] && ok "check: C2 alone still exits 0" || bad "C2 exit" "rc=$RC $OUT"

# C2 — a unit test entry with no target at all
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['acceptance_criteria'][0]['tests']=[{'level':'unit'}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C2 warnings)" = "True" ] && ok "check: C2 warns on a unit test with no target" || bad "C2-notarget" "$OUT"

# C4 — an e2e task that also carries REQ covers
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['covers']=['REQ-1']  # e2e task must not cover a REQ
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C4)" = "True" ] && ok "check: C4 flags an e2e task carrying REQ covers" || bad "C4" "$OUT"

# C4 — a SYS-TC covering a REQ instead of a CAP
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['system_tests'][0]['covers']=['REQ-1']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C4)" = "True" ] && ok "check: C4 flags a SYS-TC covering a REQ" || bad "C4-req" "$OUT"

# C6 — a task with no serves
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
del d['tasks'][0]['serves']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 flags a task with no serves" || bad "C6" "$OUT"

# C6 — a legacy scalar serves is still accepted
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['serves']='CAP-1'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C6)" = "False" ] && ok "check: C6 accepts a scalar serves (legacy)" || bad "C6-scalar" "$OUT"

# C6 — an empty serves list is as absent as a missing one
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['serves']=[]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C6)" = "True" ] && ok "check: C6 flags an empty serves list" || bad "C6-empty" "$OUT"

# C7 — an AC claimed by two tasks
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
# give T2 a duplicate claim on REQ-1.AC-1
d['tasks'][1]['acceptance_criteria']=[{'id':'REQ-1.AC-1','check':'dup','tests':[{'level':'integration','seam':'dup'}]}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C7)" = "True" ] && ok "check: C7 flags an AC claimed by two tasks" || bad "C7" "$OUT"

# C10 — two tasks whose files_to_touch overlap with no dependency edge between them
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['depends_on']=[]                                   # drop the T2->T1 edge
d['tasks'][1]['files_to_touch']=['e2e/widget_test.go','core/widget.go']  # now overlaps T1
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: C10 overlap alone -> exit 0 (warning)" || bad "C10 exit" "rc=$RC $OUT"
[ "$(has "$OUT" C10 warnings)" = "True" ] && ok "check: C10 warns on overlapping files_to_touch with no edge" || bad "C10" "$OUT"
[ "$(has "$OUT" C10)" = "False" ] && ok "check: C10 is a warning, not an error" || bad "C10-sev" "$OUT"
[ "$(jget "$OUT" "any('core/widget.go' in f['msg'] for f in d['warnings'])")" = "True" ] && ok "check: C10 names the shared file" || bad "C10 name" "$OUT"
[ "$(jget "$OUT" "any('merge-conflict' in f['msg'] for f in d['warnings'] if f['code']=='C10')")" = "True" ] \
    && ok "check: C10 names the choice — add an edge or accept the merge-conflict risk" || bad "C10 msg" "$OUT"

# C10 — the same overlap is fine when a dependency edge orders the two tasks
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['files_to_touch']=['e2e/widget_test.go','core/widget.go']  # overlaps T1, but T2 depends_on T1
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C10 warnings)" = "False" ] && ok "check: C10 accepts overlap when an edge orders the tasks" || bad "C10-edge" "$OUT"

# C3 — mandated unit tests with no plausible test file in files_to_touch
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['core/widget.go']  # AC tests stay, test-file home gone
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C3 warnings)" = "True" ] && ok "check: C3 warns on AC tests with no declared test-file home" || bad "C3" "$OUT"
[ "$(has "$OUT" C3)" = "False" ] && ok "check: C3 is a warning, not an error" || bad "C3-sev" "$OUT"
[ "$RC" -eq 0 ] && ok "check: C3 alone still exits 0" || bad "C3 exit" "rc=$RC $OUT"

# C3 — integration-level tests alone also need a test-file home
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['files_to_touch']=['core/widget.go']
for ac in t['acceptance_criteria']:
    ac['tests']=[{'level':'integration','seam':'real DB'}]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3 warnings)" = "True" ] && ok "check: C3 warns on integration tests with no test-file home" || bad "C3-integ" "$OUT"

# C3 — a test directory counts as a test-file home (heuristic is language-agnostic)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['core/widget.go','__tests__/widget.js']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3 warnings)" = "False" ] && ok "check: C3 accepts a test-directory file as the home" || bad "C3-dir" "$OUT"

# C3 — every AC gate-verified -> no tests -> no test-file home required
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['files_to_touch']=['core/widget.go']
for ac in t['acceptance_criteria']:
    ac['tests']=[]
    ac['verified_by']='make preflight-codegen'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C3 warnings)" = "False" ] && ok "check: C3 stays quiet when no AC mandates a test" || bad "C3-none" "$OUT"
[ "$RC" -eq 0 ] && ok "check: gate-verified-only task exits 0" || bad "C3-none exit" "rc=$RC $OUT"

# C9 — implementation_notes naming a file absent from files_to_touch warns
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=['also wire the factory in core/build_doors.go per ADR-3']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" C9 warnings)" = "True" ] && ok "check: C9 warns on a noted file outside files_to_touch" || bad "C9" "$OUT"
[ "$(has "$OUT" C9)" = "False" ] && ok "check: C9 is a warning, not an error" || bad "C9-sev" "$OUT"
[ "$RC" -eq 0 ] && ok "check: C9 alone still exits 0" || bad "C9 exit" "rc=$RC $OUT"

# C9 — in-scope mentions and prose tokens do not warn
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=[
  'follow the retry pattern in `core/widget.go`; respect ADR-012',
  'no external seam, e.g. no DB — no integration tests by design (v1.2)',
]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C9 warnings)" = "False" ] && ok "check: C9 ignores in-scope files and prose tokens" || bad "C9-neg" "$OUT"

# C9 — a bare filename whose full path is in files_to_touch is in scope
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=['drop the flattening in widget.go before wiring']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C9 warnings)" = "False" ] && ok "check: C9 matches a noted basename against full files_to_touch paths" || bad "C9-base" "$OUT"

# C9 — qualified-symbol references (upper-case-led final segment) are not misread as paths
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=[
  'the key is a uuid.UUID; dbpool.Tx wraps it, d.ID is the id, ElementRepository.List returns them']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C9 warnings)" = "False" ] && ok "check: C9 ignores qualified-symbol refs (uuid.UUID, d.ID, .List)" || bad "C9-symbol" "$OUT"

# C9 — a read-only: marked reference pointer is not a write target and does not warn
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=['mirror the shape at read-only:core/requirement.go:227']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C9 warnings)" = "False" ] && ok "check: C9 skips a read-only: marked pointer" || bad "C9-ro" "$OUT"
# control — the same pointer WITHOUT the marker still warns (out of files_to_touch)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['implementation_notes']=['mirror the shape at core/requirement.go:227']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C9 warnings)" = "True" ] && ok "check: C9 still warns on an unmarked out-of-scope pointer" || bad "C9-ro-control" "$OUT"

# B3 — an AC with verified_by (gate-verified, not test-provable) is not a silent hole
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
for ac in d['tasks'][0]['acceptance_criteria']:
    if ac['id']=='REQ-2.AC-1':
        ac['tests']=[]
        ac['verified_by']='make preflight-codegen'
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"; RC=$?
[ "$(has "$OUT" B3)" = "False" ] && ok "check: B3 honors a gate-verified AC (verified_by)" || bad "B3-gate" "$OUT"
[ "$RC" -eq 0 ] && ok "check: verified_by AC with no test still exits 0" || bad "B3-gate exit" "rc=$RC $OUT"

# B3 — an empty verified_by does not exempt the AC
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
for ac in d['tasks'][0]['acceptance_criteria']:
    if ac['id']=='REQ-2.AC-1':
        ac['tests']=[]
        ac['verified_by']='  '
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" B3)" = "True" ] && ok "check: B3 rejects a blank verified_by" || bad "B3-blank" "$OUT"

# C8 — a requirement entry with no serves (per-requirement driver mapping)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
del d['tasks'][0]['requirements'][0]['serves']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C8)" = "True" ] && ok "check: C8 flags a requirement with no serves" || bad "C8" "$OUT"

# C8 — a requirement driver missing from the task's serves union
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['requirements'][1]['serves']='CAP-3'  # driver not in serves: [CAP-1, CAP-2]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C8)" = "True" ] && ok "check: C8 flags a requirement driver missing from task serves" || bad "C8-union" "$OUT"

# C8 — a task serves entry no requirement declares (the 'primary driver' fudge, reversed)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['serves']=['CAP-1','CAP-2','CAP-9']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C8)" = "True" ] && ok "check: C8 flags a task serves no requirement declares" || bad "C8-orphan" "$OUT"

# C8 — an e2e task (no requirements) carries serves without C8 noise (clean fixture proves
# the pass side; this asserts the check is scoped to tasks WITH requirements)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['serves']=['CAP-1']  # e2e: requirements [] — serves is the SYS-TC's capability
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C8)" = "False" ] && ok "check: C8 skips tasks with no requirements (e2e)" || bad "C8-e2e" "$OUT"

# A3 — an UNCONFIRMED assumption in the slice blocks the gate
write_clean_slice; write_clean_sprint
cat >> "$SLICE" <<'MD'

## Assumptions requiring confirmation

- **A-1 · CONFIRMED** — CAP-1 read as widget-per-user, not widget-per-team.
- **A-2 · UNCONFIRMED** — "driven" read as manual trigger only.
MD
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: A3 unconfirmed assumption -> exit 1" || bad "A3 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A3)" = "True" ] && ok "check: A3 flags the UNCONFIRMED assumption" || bad "A3" "$OUT"
[ "$(jget "$OUT" "any('A-2' in f['msg'] for f in d['errors'])")" = "True" ] && ok "check: A3 names the assumption id" || bad "A3 id" "$OUT"

# A3 — all-CONFIRMED assumptions pass clean
write_clean_slice; write_clean_sprint
cat >> "$SLICE" <<'MD'

## Assumptions requiring confirmation

- **A-1 · CONFIRMED** — CAP-1 read as widget-per-user, not widget-per-team.
MD
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: A3 confirmed-only assumptions exit 0" || bad "A3 clean" "rc=$RC $OUT"

# slice absent -> A0 warning, intra-sprint checks still run, no errors -> exit 0
write_clean_sprint; rm -f "$SLICE"
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: missing slice still exits 0 when intra-sprint is clean" || bad "no-slice exit" "rc=$RC $OUT"
[ "$(has "$OUT" A0 warnings)" = "True" ] && ok "check: A0 warns when the slice is absent" || bad "A0" "$OUT"

# --strict promotes warnings to failure (B5 single-AC warn is present: REQ-2 has one AC)
write_clean_slice; write_clean_sprint
OUT="$(wf sprint check --strict --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "check: --strict fails when only warnings are present" || bad "strict exit" "rc=$RC $OUT"

echo ""
echo "  sprint: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
