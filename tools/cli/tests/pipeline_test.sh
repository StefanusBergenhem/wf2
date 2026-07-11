#!/usr/bin/env bash
#
# Tests for the wf pipeline brain — stage computation + the staged frontier query.
# Run: bash tools/cli/tests/pipeline_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$(cd "$HERE/.." && pwd)"          # tools/cli
ROOT="$(cd "$CLI/../.." && pwd)"        # repo root
WF="$CLI/wf"

# Prefer the wf venv python (has PyYAML); fall back to system python3.
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; echo "         $2"; }

# jget <json-string> <python-expr over `d`> -> prints the value
jget() { "$PYTHON" -c 'import sys,json; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }

# Make a fresh tmp project with a config and the given sprint YAML on stdin.
new_proj() {
    local proj; proj="$(mktemp -d)"
    mkdir -p "$proj/.wf/transient"
    cat > "$proj/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/sprint.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  pipeline_history: ".wf/transient/pipeline-history.yaml"
parallel:
  max_concurrent_tasks: 4
YAML
    cat > "$proj/.wf/sprint.yaml"
    echo "$proj"
}

wf() { local proj="$1"; shift; "$PYTHON" "$WF" "$@" --config "$proj/.wf/config.yaml"; }

# Seed pipeline_state task_states / stages from a YAML body on stdin.
seed_state() { cat > "$1/.wf/transient/pipeline-state.yaml"; }

# Diamond DAG used across cases: stages [[T1,T2],[T3],[T4]]
DIAMOND='tasks:
  - {id: T1, depends_on: []}
  - {id: T2, depends_on: []}
  - {id: T3, depends_on: [T1, T2]}
  - {id: T4, depends_on: [T3]}'

# ── compute-stages ───────────────────────────────────────────────────────────

PROJ="$(echo "$DIAMOND" | new_proj)"
OUT="$(wf "$PROJ" pipeline compute-stages --format json)"
[ "$(jget "$OUT" "d['stages']")" = "[['T1', 'T2'], ['T3'], ['T4']]" ] \
    && ok "compute-stages layers the diamond into 3 stages" \
    || bad "compute-stages layering" "$OUT"
[ "$(jget "$OUT" "d['total']")" = "3" ] && ok "compute-stages total=3" || bad "total" "$OUT"
[ "$(jget "$OUT" "d['current']")" = "1" ] && ok "compute-stages current=1" || bad "current" "$OUT"
[ "$(jget "$OUT" "d['recomputed']")" = "True" ] && ok "compute-stages recomputed=True" || bad "recomputed" "$OUT"

# idempotent: a second run preserves the plan
OUT2="$(wf "$PROJ" pipeline compute-stages --format json)"
[ "$(jget "$OUT2" "d['recomputed']")" = "False" ] \
    && ok "compute-stages is idempotent (recomputed=False on re-run)" || bad "idempotent" "$OUT2"

# cycle → non-zero exit, no plan
PROJ_C="$(printf 'tasks:\n  - {id: A, depends_on: [B]}\n  - {id: B, depends_on: [A]}\n' | new_proj)"
if wf "$PROJ_C" pipeline compute-stages --format json >/dev/null 2>&1; then
    bad "cycle should fail" "exited 0"
else
    ok "compute-stages HALTs (non-zero) on a dependency cycle"
fi

# a pre-completed task drops out of the layering and frees its dependents
PROJ_D="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_D" <<'YAML'
current_phase: preparing
task_states:
  T1: {status: completed}
YAML
OUTD="$(wf "$PROJ_D" pipeline compute-stages --format json)"
[ "$(jget "$OUTD" "d['stages']")" = "[['T2'], ['T3'], ['T4']]" ] \
    && ok "compute-stages excludes a completed task, deps still satisfied" || bad "completed-exclusion" "$OUTD"

# a blocked task and its dependents are unplaceable → recorded as blocked
PROJ_B="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_B" <<'YAML'
task_states:
  T1: {status: blocked}
YAML
OUTB="$(wf "$PROJ_B" pipeline compute-stages --format json)"
[ "$(jget "$OUTB" "d['stages']")" = "[['T2']]" ] \
    && ok "compute-stages: blocked T1 leaves only T2 placeable" || bad "blocked layering" "$OUTB"
[ "$(jget "$OUTB" "sorted(d['blocked'])")" = "['T3', 'T4']" ] \
    && ok "compute-stages: T3,T4 transitively blocked by T1" || bad "blocked propagation" "$OUTB"

# ── next (frontier) ──────────────────────────────────────────────────────────

# fresh stage 1 dispatches both independent roots
PROJ1="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ1" pipeline compute-stages >/dev/null
N1="$(wf "$PROJ1" pipeline next --format json)"
[ "$(jget "$N1" "[e['task_id'] for e in d['dispatch']]")" = "['T1', 'T2']" ] \
    && ok "next: stage 1 dispatches T1,T2" || bad "next dispatch" "$N1"
[ "$(jget "$N1" "d['stage']['index']")" = "1" ] && ok "next: stage.index=1" || bad "stage index" "$N1"
[ "$(jget "$N1" "d['terminal']['stage_done']")" = "False" ] && ok "next: stage not done with pending work" || bad "stage_done" "$N1"
[ "$(jget "$N1" "d['dispatch'][0]['worktree']")" = ".wf/transient/worktrees/sprint-T1" ] \
    && ok "next: dispatch carries the computed worktree path" || bad "worktree" "$N1"
[ "$(jget "$N1" "'mode' in d['dispatch'][0]")" = "False" ] \
    && ok "next: dispatch entry carries no mode field (wf-build derives its mode)" \
    || bad "dispatch mode field" "$N1"

# cap=1 → one dispatched, the other ready
PROJ_CAP="$(echo "$DIAMOND" | new_proj)"
"$PYTHON" - "$PROJ_CAP/.wf/config.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]; d=yaml.safe_load(open(p)); d['parallel']['max_concurrent_tasks']=1
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_CAP" pipeline compute-stages >/dev/null
NCAP="$(wf "$PROJ_CAP" pipeline next --format json)"
[ "$(jget "$NCAP" "[e['task_id'] for e in d['dispatch']]")" = "['T1']" ] \
    && ok "next: cap=1 dispatches only T1" || bad "cap dispatch" "$NCAP"
[ "$(jget "$NCAP" "d['ready']")" = "['T2']" ] && ok "next: cap=1 leaves T2 ready" || bad "cap ready" "$NCAP"

# an in-flight task occupies a slot and is not re-dispatched
PROJ_IF="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_IF" pipeline compute-stages >/dev/null
seed_state "$PROJ_IF" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: building}
YAML
NIF="$(wf "$PROJ_IF" pipeline next --format json)"
[ "$(jget "$NIF" "d['in_flight']")" = "['T1']" ] && ok "next: T1 building is in_flight" || bad "in_flight" "$NIF"
[ "$(jget "$NIF" "[e['task_id'] for e in d['dispatch']]")" = "['T2']" ] \
    && ok "next: only T2 dispatched while T1 in-flight" || bad "in_flight dispatch" "$NIF"

# stage settled when all stage tasks completed (but sprint not done — more stages)
PROJ_SD="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_SD" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: completed}
  T2: {status: completed}
YAML
NSD="$(wf "$PROJ_SD" pipeline next --format json)"
[ "$(jget "$NSD" "d['terminal']['stage_done']")" = "True" ] && ok "next: stage_done when stage tasks completed" || bad "stage_done true" "$NSD"
[ "$(jget "$NSD" "d['terminal']['sprint_done']")" = "False" ] && ok "next: sprint not done mid-sprint" || bad "sprint_done false" "$NSD"

# a parked design-issue task does NOT hold the stage open
PROJ_DI="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_DI" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: completed}
  T2: {status: design_issue}
YAML
NDI="$(wf "$PROJ_DI" pipeline next --format json)"
[ "$(jget "$NDI" "d['repairing']")" = "['T2']" ] && ok "next: design_issue task surfaces as repairing" || bad "repairing" "$NDI"
[ "$(jget "$NDI" "d['terminal']['stage_done']")" = "True" ] && ok "next: parked DI does not hold the stage open" || bad "parked stage_done" "$NDI"

# an approved task (awaiting batch merge) is settled — surfaced, not re-dispatched, stage settles
PROJ_APN="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_APN" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: approved}
  T2: {status: completed}
YAML
NAPN="$(wf "$PROJ_APN" pipeline next --format json)"
[ "$(jget "$NAPN" "d['approved']")" = "['T1']" ] && ok "next: approved task surfaces under approved[]" || bad "approved list" "$NAPN"
[ "$(jget "$NAPN" "[e['task_id'] for e in d['dispatch']]")" = "[]" ] && ok "next: approved task is not re-dispatched" || bad "approved dispatch" "$NAPN"
[ "$(jget "$NAPN" "d['terminal']['stage_done']")" = "True" ] && ok "next: approved task does not hold the stage open" || bad "approved stage_done" "$NAPN"

# last stage complete → sprint_done
PROJ_FIN="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_FIN" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 3, total: 3}
task_states:
  T4: {status: completed}
YAML
NFIN="$(wf "$PROJ_FIN" pipeline next --format json)"
[ "$(jget "$NFIN" "d['terminal']['sprint_done']")" = "True" ] && ok "next: sprint_done on last stage complete" || bad "sprint_done true" "$NFIN"

# next before compute-stages → halt
PROJ_NC="$(echo "$DIAMOND" | new_proj)"
NNC="$(wf "$PROJ_NC" pipeline next --format json)"
[ "$(jget "$NNC" "d['terminal']['halt'] is not None")" = "True" ] \
    && ok "next: halts when stages not computed" || bad "uncomputed halt" "$NNC"

# ── run-state mutations ──────────────────────────────────────────────────────

PROJ_M="$(echo "$DIAMOND" | new_proj)"

# transition writes phase + history
wf "$PROJ_M" pipeline transition --to preparing --reason kickoff >/dev/null
CP="$(wf "$PROJ_M" pipeline current-phase --format json)"
[ "$(jget "$CP" "d['phase']")" = "preparing" ] && ok "transition sets current_phase" || bad "transition" "$CP"

# dispatch maps agent → task state, records pass_index
wf "$PROJ_M" pipeline dispatch --agent wf-build --task T1 --attempt 1 >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T1 --format json)" "d['state']")" = "building" ] \
    && ok "dispatch wf-build → building" || bad "dispatch build state" ""
wf "$PROJ_M" pipeline dispatch --agent wf-security-review --task T1 --attempt 1 --pass 1 >/dev/null
TSR="$(wf "$PROJ_M" pipeline task-state T1 --format json)"
[ "$(jget "$TSR" "d['state']")" = "reviewing" ] && ok "dispatch a review pass → reviewing" || bad "dispatch review state" "$TSR"
[ "$(jget "$TSR" "d['pass_index']")" = "1" ] && ok "dispatch records pass_index" || bad "pass_index" "$TSR"

# complete-task → completed + commits
wf "$PROJ_M" pipeline complete-task T1 --commit abc123 --merge def456 >/dev/null
TSC="$(wf "$PROJ_M" pipeline task-state T1 --format json)"
[ "$(jget "$TSC" "d['state']")" = "completed" ] && ok "complete-task → completed" || bad "complete" "$TSC"
[ "$(jget "$TSC" "d['build_commit']")" = "abc123" ] && ok "complete-task records build_commit" || bad "build_commit" "$TSC"

# approve-task → approved (passed all passes, awaiting the end_of_stage batch merge)
PROJ_AP="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_AP" pipeline approve-task T1 --commit cab00d >/dev/null
TSAP="$(wf "$PROJ_AP" pipeline task-state T1 --format json)"
[ "$(jget "$TSAP" "d['state']")" = "approved" ] && ok "approve-task → approved" || bad "approve" "$TSAP"
[ "$(jget "$TSAP" "d['build_commit']")" = "cab00d" ] && ok "approve-task records build_commit" || bad "approve build_commit" "$TSAP"

# reject-task → building, attempt++, pass_index reset to 0 (N-pass restart at build)
wf "$PROJ_M" pipeline dispatch --agent wf-review --task T2 --attempt 1 --pass 1 >/dev/null
wf "$PROJ_M" pipeline reject-task T2 --feedback /tmp/fb.yaml >/dev/null
TSJ="$(wf "$PROJ_M" pipeline task-state T2 --format json)"
[ "$(jget "$TSJ" "d['state']")" = "building" ] && ok "reject-task → building" || bad "reject state" "$TSJ"
[ "$(jget "$TSJ" "d['attempt_counter']")" = "1" ] && ok "reject-task bumps attempt_counter" || bad "reject attempt" "$TSJ"
[ "$(jget "$TSJ" "d['pass_index']")" = "0" ] && ok "reject-task resets pass_index to 0" || bad "reject pass_index" "$TSJ"

# block-task → blocked
wf "$PROJ_M" pipeline block-task T3 --reason "manual" >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T3 --format json)" "d['state']")" = "blocked" ] \
    && ok "block-task → blocked" || bad "block" ""

# record-design-issue → design_issue + surfaces as unresolved
wf "$PROJ_M" pipeline record-design-issue DI-1 --task T4 --severity high --fix_kind contract_amendment >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T4 --format json)" "d['state']")" = "design_issue" ] \
    && ok "record-design-issue → design_issue" || bad "DI state" ""
UDI="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDI" "d['count']")" = "1" ] && ok "unresolved-design-issues counts the open DI" || bad "DI count" "$UDI"

# resolve-design-issue flips the entry to resolved; it drops out of unresolved
wf "$PROJ_M" pipeline resolve-design-issue DI-1 >/dev/null
UDR="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDR" "d['count']")" = "0" ] && ok "resolve-design-issue drops the DI from unresolved" || bad "DI resolve" "$UDR"
HDR="$(wf "$PROJ_M" pipeline history-tail 5 --format json)"
[ "$(jget "$HDR" "any(e.get('event')=='design_issue_resolved' and e.get('di_id')=='DI-1' for e in d)")" = "True" ] \
    && ok "resolve-design-issue appends a design_issue_resolved history event" || bad "DI resolve history" "$HDR"
if wf "$PROJ_M" pipeline resolve-design-issue DI-nope >/dev/null 2>&1; then
    bad "resolve-design-issue unknown id should fail" "exited 0"
else
    ok "resolve-design-issue errors on an unknown id"
fi
# resolve-design-issue un-parks the implicated task (design_issue → pending) so the
# scheduler can place it again — e.g. behind a component_defect follow-up task.
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T4 --format json)" "d['state']")" = "pending" ] \
    && ok "resolve-design-issue resets the parked task to pending" || bad "DI resolve unpark" \
    "$(wf "$PROJ_M" pipeline task-state T4 --format json)"
# ...but never clobbers a task that has already moved on (e.g. re-dispatched → building)
wf "$PROJ_M" pipeline record-design-issue DI-2 --task T5 --severity low --fix_kind contract_amendment >/dev/null
wf "$PROJ_M" pipeline dispatch --agent wf-build --task T5 --attempt 1 >/dev/null
wf "$PROJ_M" pipeline resolve-design-issue DI-2 >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T5 --format json)" "d['state']")" = "building" ] \
    && ok "resolve-design-issue leaves a non-parked task status alone" || bad "DI resolve non-parked" \
    "$(wf "$PROJ_M" pipeline task-state T5 --format json)"

# scope-amendment bumps the count
wf "$PROJ_M" pipeline scope-amendment T2 --added "a.go,b.go" >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline scope-amendment-count T2 --format json)" "d['value']")" = "1" ] \
    && ok "scope-amendment bumps count" || bad "scope count" ""

# reclaim-stale flips an orphan slot back to pending
PROJ_R="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_R" <<'YAML'
task_states:
  T1: {status: reviewing}
  T2: {status: completed}
YAML
RC="$(wf "$PROJ_R" pipeline reclaim-stale --format json)"
[ "$(jget "$RC" "[r['task_id'] for r in d['reclaimed']]")" = "['T1']" ] \
    && ok "reclaim-stale reclaims the in-flight orphan only" || bad "reclaim" "$RC"
[ "$(jget "$(wf "$PROJ_R" pipeline task-state T1 --format json)" "d['state']")" = "pending" ] \
    && ok "reclaim-stale resets orphan to pending" || bad "reclaim state" ""

# ── staged-model mutations ───────────────────────────────────────────────────

# advance-stage walks current; no-op on the last stage
PROJ_A="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_A" pipeline compute-stages >/dev/null
A1="$(wf "$PROJ_A" pipeline advance-stage --format json)"
[ "$(jget "$A1" "d['current']")" = "2" ] && ok "advance-stage → stage 2" || bad "advance 2" "$A1"
wf "$PROJ_A" pipeline advance-stage >/dev/null   # → 3 (last)
A3="$(wf "$PROJ_A" pipeline advance-stage --format json)"
[ "$(jget "$A3" "d['advanced']")" = "False" ] && ok "advance-stage is a no-op past the last stage" || bad "advance last" "$A3"

# propagate-blocks: an escalated dep dooms its dependents
PROJ_P="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_P" <<'YAML'
task_states:
  T1: {status: escalated}
YAML
PB="$(wf "$PROJ_P" pipeline propagate-blocks --format json)"
[ "$(jget "$PB" "sorted(d['blocked'])")" = "['T3', 'T4']" ] \
    && ok "propagate-blocks dooms T3,T4 behind escalated T1" || bad "propagate" "$PB"

# stage timing
PROJ_TM="$(echo "$DIAMOND" | new_proj)"
SS="$(wf "$PROJ_TM" pipeline stage-start --stage 1 --format json)"
[ -n "$(jget "$SS" "d['started_at']")" ] && ok "stage-start records started_at" || bad "stage-start" "$SS"
SE="$(wf "$PROJ_TM" pipeline stage-end --stage 1 --format json)"
[ "$(jget "$SE" "'duration_seconds' in d['timing']")" = "True" ] && ok "stage-end records duration_seconds" || bad "stage-end" "$SE"

# stage-summary derives lists from task_states
PROJ_SS="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_SS" <<'YAML'
stages: {definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: completed}
  T2: {status: escalated}
YAML
SUM="$(wf "$PROJ_SS" pipeline stage-summary --stage 1 --format json)"
[ "$(jget "$SUM" "d['completed']")" = "['T1']" ] && ok "stage-summary derives completed" || bad "summary completed" "$SUM"
[ "$(jget "$SUM" "d['escalated']")" = "['T2']" ] && ok "stage-summary derives escalated" || bad "summary escalated" "$SUM"

# ── sprint lifecycle ─────────────────────────────────────────────────────────

# complete-sprint resets to idle and clears the sprint slot (no archive configured)
PROJ_CS="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_CS" pipeline transition --to running_stage >/dev/null
wf "$PROJ_CS" pipeline complete-sprint >/dev/null
[ "$(jget "$(wf "$PROJ_CS" pipeline current-phase --format json)" "d['phase']")" = "idle" ] \
    && ok "complete-sprint resets phase to idle" || bad "complete-sprint phase" ""
[ ! -f "$PROJ_CS/.wf/sprint.yaml" ] && ok "complete-sprint clears the sprint slot" || bad "complete-sprint sprint" "still present"

# complete-sprint with paths.archive set: drains sprint+slice into the archive, snapshots the backlog
PROJ_A="$(mktemp -d)"; mkdir -p "$PROJ_A/.wf/transient"
cat > "$PROJ_A/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_slice: ".wf/transient/design-slice.md"
  design_backlog: ".wf/design-backlog.md"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
YAML
printf 'sprint_id: sprint-xyz\ntasks: []\n' > "$PROJ_A/.wf/transient/sprint.yaml"
printf '# slice body\n'                     > "$PROJ_A/.wf/transient/design-slice.md"
printf '# backlog body\n'                   > "$PROJ_A/.wf/design-backlog.md"
printf 'current_phase: running_stage\n'     > "$PROJ_A/.wf/transient/pipeline-state.yaml"
CA="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_A/.wf/config.yaml" --format json)"
[ "$(jget "$CA" "d['sprint_id']")" = "sprint-xyz" ] && ok "complete-sprint(archive): reports sprint_id" || bad "archive sprint_id" "$CA"
[ ! -f "$PROJ_A/.wf/transient/sprint.yaml" ] && ok "complete-sprint(archive): drains the sprint" || bad "archive drain sprint" "still present"
[ ! -f "$PROJ_A/.wf/transient/design-slice.md" ] && ok "complete-sprint(archive): drains the slice" || bad "archive drain slice" "still present"
[ -f "$PROJ_A/.wf/design-backlog.md" ] && ok "complete-sprint(archive): leaves the backlog (snapshot only)" || bad "archive backlog kept" "backlog gone"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__sprint.yaml >/dev/null 2>&1 && ok "complete-sprint(archive): sprint snapshot under <archive>/<sprint_id>/" || bad "archive sprint snap" "$(ls -R "$PROJ_A/.wf/archive" 2>&1)"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__design-slice.md >/dev/null 2>&1 && ok "complete-sprint(archive): slice snapshot present" || bad "archive slice snap" "$(ls "$PROJ_A/.wf/archive/sprint-xyz" 2>&1)"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__design-backlog.md >/dev/null 2>&1 && ok "complete-sprint(archive): backlog snapshot present" || bad "archive backlog snap" "$(ls "$PROJ_A/.wf/archive/sprint-xyz" 2>&1)"
[ "$(jget "$(wf "$PROJ_A" pipeline current-phase --format json)" "d['phase']")" = "idle" ] && ok "complete-sprint(archive): resets phase to idle" || bad "archive phase idle" "$CA"

# archive-history spills the overflow past the cap
PROJ_H="$(echo "$DIAMOND" | new_proj)"
"$PYTHON" - "$PROJ_H/.wf/transient/pipeline-state.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]
d={"current_phase":"running_stage","history":[{"ts":"t","event":f"e{i}"} for i in range(10)]}
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_H" pipeline archive-history --cap 3 >/dev/null
HT="$(wf "$PROJ_H" pipeline history-tail 100 --format json)"
# live history kept at cap (3) + the archival event appended = 4
[ "$(jget "$HT" "len(d)")" = "4" ] && ok "archive-history keeps cap + the archival marker live" || bad "archive-history live" "$HT"
[ -f "$PROJ_H/.wf/transient/pipeline-history.yaml" ] && ok "archive-history writes the spill file" || bad "archive-history spill" "missing"

# ── summary ──
echo ""
echo "  pipeline brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
