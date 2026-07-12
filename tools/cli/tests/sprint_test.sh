#!/usr/bin/env bash
#
# Tests for `wf sprint task` (task-contract extractor) and `wf sprint check`
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
    component: "core"
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
# wf sprint check — the analyze gate
# ---------------------------------------------------------------------------
# Each test rewrites $PROJ/.wf/sprint.yaml (and, where a Family-A check needs it,
# design-slice.md), then runs `wf sprint check`. `has` reports whether a finding
# code appears among errors (or warnings, arg 3 = warnings).
SPRINT="$PROJ/.wf/sprint.yaml"; SLICE="$PROJ/.wf/design-slice.md"
has() { jget "$1" "any(f['code']=='$2' for f in d['${3:-errors}'])"; }

write_clean_slice() { cat > "$SLICE" <<'MD'
# Design-slice — widget

**Serves:** CAP-1

## Component requirements

- **REQ-1** — the widget does X  *(owner: core · CAP-1)*
- **REQ-2** — the widget rejects Y  *(owner: core · CAP-1)*

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
    component: core
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
      - id: REQ-1.AC-2
        check: "given bad X, returns error"
      - id: REQ-2.AC-1
        check: "given Y, rejects"
    testing_mandate:
      unit_tests:
        - target: "core/widget.go:Widget"
          tests:
            - description: "X -> ok [positive]"
              covers: REQ-1.AC-1
            - description: "bad X -> error [negative]"
              covers: REQ-1.AC-2
            - description: "Y -> rejected [negative]"
              covers: REQ-2.AC-1
      integration_tests: []
      system_tests: []
  - id: T2
    component: e2e
    depends_on: [T1]
    covers: []
    requirements: []
    serves: [CAP-1]
    files_to_touch: ["e2e/widget_test.go"]
    acceptance_criteria: []
    testing_mandate:
      unit_tests: []
      integration_tests: []
      system_tests:
        - id: SYS-TC-1
          description: "end-to-end widget flow"
          covers: [CAP-1]
YAML
}

# clean sprint + slice -> pass, zero errors, exit 0
write_clean_slice; write_clean_sprint
OUT="$(wf sprint check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "check: clean sprint exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "check: clean sprint verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "len(d['errors'])")" = "0" ] && ok "check: clean sprint has no errors" || bad "clean errors" "$OUT"

# B3 — an AC referenced by no test is the silent hole (drop the REQ-2.AC-1 test)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
u=d['tasks'][0]['testing_mandate']['unit_tests'][0]
u['tests']=[t for t in u['tests'] if t['covers']!='REQ-2.AC-1']
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

# A1 — a slice requirement covered by no task (drop REQ-2 from the task's covers+ACs)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['covers']=['REQ-1']
t['requirements']=[r for r in t['requirements'] if r['id']=='REQ-1']
t['acceptance_criteria']=[ac for ac in t['acceptance_criteria'] if ac['id'].startswith('REQ-1.')]
u=t['testing_mandate']['unit_tests'][0]
u['tests']=[x for x in u['tests'] if x['covers'].startswith('REQ-1.')]
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

# C2 — a unit target file outside files_to_touch
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['other.go']  # target core/widget.go no longer in scope
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C2)" = "True" ] && ok "check: C2 flags a unit target outside files_to_touch" || bad "C2" "$OUT"

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
d['tasks'][1]['acceptance_criteria']=[{'id':'REQ-1.AC-1','check':'dup'}]
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
[ "$RC" -eq 1 ] && ok "check: C10 overlap -> exit 1" || bad "C10 exit" "rc=$RC $OUT"
[ "$(has "$OUT" C10)" = "True" ] && ok "check: C10 flags overlapping files_to_touch with no edge" || bad "C10" "$OUT"
[ "$(jget "$OUT" "any('core/widget.go' in f['msg'] for f in d['errors'])")" = "True" ] && ok "check: C10 names the shared file" || bad "C10 name" "$OUT"

# C10 — the same overlap is fine when a dependency edge orders the two tasks
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][1]['files_to_touch']=['e2e/widget_test.go','core/widget.go']  # overlaps T1, but T2 depends_on T1
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C10)" = "False" ] && ok "check: C10 accepts overlap when an edge orders the tasks" || bad "C10-edge" "$OUT"

# C3 — a unit-test mandate with no plausible test file in files_to_touch
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['core/widget.go']  # mandate stays, test-file home gone
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3)" = "True" ] && ok "check: C3 flags a test mandate with no test-file home" || bad "C3" "$OUT"

# C3 — an integration-test mandate alone also needs a test-file home
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['files_to_touch']=['core/widget.go']
t['testing_mandate']['unit_tests']=[]
t['testing_mandate']['integration_tests']=[
  {'description':'real DB round-trip -> persisted','covers':'REQ-1.AC-1'},
  {'description':'bad row -> error','covers':'REQ-1.AC-2'},
  {'description':'Y -> rejected','covers':'REQ-2.AC-1'},
]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3)" = "True" ] && ok "check: C3 flags an integration mandate with no test-file home" || bad "C3-integ" "$OUT"

# C3 — a test directory counts as a test-file home (heuristic is language-agnostic)
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
d['tasks'][0]['files_to_touch']=['core/widget.go','__tests__/widget.js']
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3)" = "False" ] && ok "check: C3 accepts a test-directory file as the home" || bad "C3-dir" "$OUT"

# C3 — no unit/integration mandate -> no test-file home required
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
t['files_to_touch']=['core/widget.go']
t['testing_mandate']['unit_tests']=[]
t['testing_mandate']['integration_tests']=[]
open(p,'w').write(yaml.safe_dump(d, sort_keys=False))
PY
OUT="$(wf sprint check --format json)"
[ "$(has "$OUT" C3)" = "False" ] && ok "check: C3 stays quiet when no unit/integration tests are mandated" || bad "C3-none" "$OUT"

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
  'no external seam, e.g. no DB — integration_tests empty by design (v1.2)',
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

# B3 — an AC with verified_by (gate-verified, not test-provable) is not a silent hole
write_clean_sprint
"$PYTHON" - "$SPRINT" <<'PY'
import sys, yaml
p=sys.argv[1]; d=yaml.safe_load(open(p))
t=d['tasks'][0]
for ac in t['acceptance_criteria']:
    if ac['id']=='REQ-2.AC-1':
        ac['verified_by']='make preflight-codegen'
u=t['testing_mandate']['unit_tests'][0]
u['tests']=[x for x in u['tests'] if x['covers']!='REQ-2.AC-1']
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
t=d['tasks'][0]
for ac in t['acceptance_criteria']:
    if ac['id']=='REQ-2.AC-1':
        ac['verified_by']='  '
u=t['testing_mandate']['unit_tests'][0]
u['tests']=[x for x in u['tests'] if x['covers']!='REQ-2.AC-1']
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
