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
      - id: REQ-2
        statement: "the widget rejects Y"
    serves: [CAP-1, CAP-2]
    files_to_touch: ["core/widget.go"]
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
