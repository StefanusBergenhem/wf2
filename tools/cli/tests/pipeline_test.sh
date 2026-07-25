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
[ "$(jget "$N1" "sorted(d['dispatch'][0])")" = "['task_id', 'worktree']" ] \
    && ok "next: dispatch entry carries exactly task_id + worktree" \
    || bad "dispatch entry keys" "$N1"

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
[ "$(jget "$NIF" "[e['task_id'] for e in d['in_flight']]")" = "['T1']" ] \
  && ok "next: T1 building is in_flight" || bad "in_flight" "$NIF"
[ "$(jget "$NIF" "d['in_flight'][0]['status']")" = "building" ] \
  && ok "next: in_flight carries the task status" || bad "in_flight status" "$NIF"
# the dispatch that started it, and how long ago — a never-spawned agent's tell (L-053)
wf "$PROJ_IF" pipeline dispatch --agent wf-build --task T1 --attempt 1 >/dev/null
NIF="$(wf "$PROJ_IF" pipeline next --format json)"
[ "$(jget "$NIF" "d['in_flight'][0]['agent']")" = "wf-build" ] \
  && ok "next: in_flight names the dispatched agent" || bad "in_flight agent" "$NIF"
[ "$(jget "$NIF" "d['in_flight'][0]['dispatched_at'] is not None and d['in_flight'][0]['since_s'] >= 0")" = "True" ] \
  && ok "next: in_flight carries the dispatch age" || bad "in_flight since_s" "$NIF"
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

# current-phase resolves sprint_branch from git when the stored value is null (L-020)
PROJ_GB="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_GB" <<'YAML'
current_phase: running_stage
sprint_branch: null
YAML
( cd "$PROJ_GB" && git init -q && git config user.email t@t.t && git config user.name t \
  && git commit -q --allow-empty -m init && git checkout -q -b sprint/demo ) 2>/dev/null
CPGB="$(wf "$PROJ_GB" pipeline current-phase --format json)"
[ "$(jget "$CPGB" "d['sprint_branch']")" = "sprint/demo" ] \
  && ok "current-phase: sprint_branch falls back to the git branch when stored null" || bad "L-020 git fallback" "$CPGB"

# dispatch maps agent → task state, records pass_index
DSPC="$(wf "$PROJ_M" pipeline dispatch --agent wf-build --task T1 --attempt 1 --format json)"
[ "$(jget "$DSPC" "d.get('ok') is True and d.get('event')=='dispatch' and d.get('status')=='building'")" = "True" ] \
    && ok "dispatch emits a success confirmation (L-059)" || bad "dispatch confirm" "$DSPC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T1 --format json)" "d['state']")" = "building" ] \
    && ok "dispatch wf-build → building" || bad "dispatch build state" ""
wf "$PROJ_M" pipeline dispatch --agent wf-security-review --task T1 --attempt 1 --pass 1 >/dev/null
TSR="$(wf "$PROJ_M" pipeline task-state T1 --format json)"
[ "$(jget "$TSR" "d['state']")" = "reviewing" ] && ok "dispatch a review pass → reviewing" || bad "dispatch review state" "$TSR"
[ "$(jget "$TSR" "d['pass_index']")" = "1" ] && ok "dispatch records pass_index" || bad "pass_index" "$TSR"

# complete-task → completed + commits
CMPC="$(wf "$PROJ_M" pipeline complete-task T1 --commit abc123 --merge def456 --format json)"
[ "$(jget "$CMPC" "d.get('ok') is True and d.get('event')=='task_completed' and d.get('status')=='completed'")" = "True" ] \
    && ok "complete-task emits a success confirmation (L-059)" || bad "complete confirm" "$CMPC"
TSC="$(wf "$PROJ_M" pipeline task-state T1 --format json)"
[ "$(jget "$TSC" "d['state']")" = "completed" ] && ok "complete-task → completed" || bad "complete" "$TSC"
[ "$(jget "$TSC" "d['build_commit']")" = "abc123" ] && ok "complete-task records build_commit" || bad "build_commit" "$TSC"

# approve-task → approved (passed all passes, awaiting the end_of_stage batch merge)
PROJ_AP="$(echo "$DIAMOND" | new_proj)"
APPC="$(wf "$PROJ_AP" pipeline approve-task T1 --commit cab00d --format json)"
[ "$(jget "$APPC" "d.get('ok') is True and d.get('event')=='task_approved' and d.get('status')=='approved'")" = "True" ] \
    && ok "approve-task emits a success confirmation (L-059)" || bad "approve confirm" "$APPC"
TSAP="$(wf "$PROJ_AP" pipeline task-state T1 --format json)"
[ "$(jget "$TSAP" "d['state']")" = "approved" ] && ok "approve-task → approved" || bad "approve" "$TSAP"
[ "$(jget "$TSAP" "d['build_commit']")" = "cab00d" ] && ok "approve-task records build_commit" || bad "approve build_commit" "$TSAP"

# reject-task → building, attempt++, pass_index reset to 0 (N-pass restart at build)
wf "$PROJ_M" pipeline dispatch --agent wf-review --task T2 --attempt 1 --pass 1 >/dev/null
RJC="$(wf "$PROJ_M" pipeline reject-task T2 --feedback /tmp/fb.yaml --format json)"
[ "$(jget "$RJC" "d.get('ok') is True and d.get('event')=='task_rejected'")" = "True" ] \
    && ok "reject-task emits a success confirmation (L-059)" || bad "reject confirm" "$RJC"
TSJ="$(wf "$PROJ_M" pipeline task-state T2 --format json)"
[ "$(jget "$TSJ" "d['state']")" = "building" ] && ok "reject-task → building" || bad "reject state" "$TSJ"
[ "$(jget "$TSJ" "d['attempt_counter']")" = "1" ] && ok "reject-task bumps attempt_counter" || bad "reject attempt" "$TSJ"
[ "$(jget "$TSJ" "d['pass_index']")" = "0" ] && ok "reject-task resets pass_index to 0" || bad "reject pass_index" "$TSJ"

# block-task → blocked
BLC="$(wf "$PROJ_M" pipeline block-task T3 --reason "manual" --format json)"
[ "$(jget "$BLC" "d.get('ok') is True and d.get('event')=='task_blocked' and d.get('status')=='blocked'")" = "True" ] \
    && ok "block-task emits a success confirmation (L-059)" || bad "block confirm" "$BLC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T3 --format json)" "d['state']")" = "blocked" ] \
    && ok "block-task → blocked" || bad "block" ""

# record-design-issue → design_issue + surfaces as unresolved
RDC="$(wf "$PROJ_M" pipeline record-design-issue DI-1 --task T4 --severity high --fix_kind contract_amendment --format json)"
[ "$(jget "$RDC" "d.get('ok') is True and d.get('event')=='design_issue_recorded' and d.get('di_id')=='DI-1'")" = "True" ] \
    && ok "record-design-issue emits a success confirmation (L-059)" || bad "record-DI confirm" "$RDC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T4 --format json)" "d['state']")" = "design_issue" ] \
    && ok "record-design-issue → design_issue" || bad "DI state" ""
UDI="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDI" "d['count']")" = "1" ] && ok "unresolved-design-issues counts the open DI" || bad "DI count" "$UDI"

# resolve-design-issue flips the entry to resolved; it drops out of unresolved
RSC="$(wf "$PROJ_M" pipeline resolve-design-issue DI-1 --format json)"
[ "$(jget "$RSC" "d.get('ok') is True and d.get('event')=='design_issue_resolved' and d.get('di_id')=='DI-1'")" = "True" ] \
    && ok "resolve-design-issue emits a success confirmation (L-059)" || bad "resolve-DI confirm" "$RSC"
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

# A stage-boundary DI is task-less: record-design-issue with no --task records the DI and
# parks NO phantom task, so a later fix-mode read never mistakes it for a real sprint task.
wf "$PROJ_M" pipeline record-design-issue DI-STAGE --severity medium --fix_kind spec_amendment >/dev/null
UDS="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDS" "any(i['di_id']=='DI-STAGE' for i in d['issues'])")" = "True" ] \
    && ok "record-design-issue records a task-less (stage-boundary) DI" || bad "task-less DI record" "$UDS"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state DI-STAGE --format json)" "d['state']")" = "pending" ] \
    && ok "task-less DI parks no phantom task" || bad "task-less DI phantom park" \
    "$(wf "$PROJ_M" pipeline task-state DI-STAGE --format json)"
wf "$PROJ_M" pipeline resolve-design-issue DI-STAGE >/dev/null
UDS2="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDS2" "any(i['di_id']=='DI-STAGE' for i in d['issues'])")" = "False" ] \
    && ok "task-less DI resolves cleanly" || bad "task-less DI resolve" "$UDS2"

# Build/review/stage-repair raise a BARE issue — no --fix_kind. wf-spec-fix classifies it
# and writes the kind back to the host artifact; record-design-issue must not require it.
wf "$PROJ_M" pipeline record-design-issue DI-BARE --task T4 --severity high >/dev/null 2>&1 \
    && ok "record-design-issue accepts a bare issue (no --fix_kind)" || bad "bare DI record" "requires --fix_kind"
UDB="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDB" "any(i['di_id']=='DI-BARE' for i in d['issues'])")" = "True" ] \
    && ok "a bare issue surfaces as unresolved" || bad "bare DI unresolved" "$UDB"

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
  design_issues: ".wf/transient/design-issues.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
YAML
printf 'sprint_id: sprint-xyz\ntasks: []\n' > "$PROJ_A/.wf/transient/sprint.yaml"
printf '# slice body\n'                     > "$PROJ_A/.wf/transient/design-slice.md"
printf '# backlog body\n'                   > "$PROJ_A/.wf/design-backlog.md"
printf 'issues:\n- id: DI-T1\n  status: resolved\n' > "$PROJ_A/.wf/transient/design-issues.yaml"
printf 'current_phase: running_stage\n'     > "$PROJ_A/.wf/transient/pipeline-state.yaml"
CA="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_A/.wf/config.yaml" --format json)"
[ "$(jget "$CA" "d['sprint_id']")" = "sprint-xyz" ] && ok "complete-sprint(archive): reports sprint_id" || bad "archive sprint_id" "$CA"
[ ! -f "$PROJ_A/.wf/transient/sprint.yaml" ] && ok "complete-sprint(archive): drains the sprint" || bad "archive drain sprint" "still present"
[ ! -f "$PROJ_A/.wf/transient/design-slice.md" ] && ok "complete-sprint(archive): drains the slice" || bad "archive drain slice" "still present"
[ ! -f "$PROJ_A/.wf/transient/design-issues.yaml" ] && ok "complete-sprint(archive): drains the design-issues file" || bad "archive drain design_issues" "still present"
[ -f "$PROJ_A/.wf/design-backlog.md" ] && ok "complete-sprint(archive): leaves the backlog (snapshot only)" || bad "archive backlog kept" "backlog gone"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__sprint.yaml >/dev/null 2>&1 && ok "complete-sprint(archive): sprint snapshot under <archive>/<sprint_id>/" || bad "archive sprint snap" "$(ls -R "$PROJ_A/.wf/archive" 2>&1)"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__design-slice.md >/dev/null 2>&1 && ok "complete-sprint(archive): slice snapshot present" || bad "archive slice snap" "$(ls "$PROJ_A/.wf/archive/sprint-xyz" 2>&1)"
ls "$PROJ_A/.wf/archive/sprint-xyz/"*__design-issues.yaml >/dev/null 2>&1 && ok "complete-sprint(archive): design-issues snapshot present" || bad "archive design_issues snap" "$(ls "$PROJ_A/.wf/archive/sprint-xyz" 2>&1)"
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

# ── complete-sprint: the close-time drain (backlog trim, learnings, drain report) ──

yget() { "$PYTHON" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"; }

PROJ_D="$(mktemp -d)"; mkdir -p "$PROJ_D/.wf/transient" "$PROJ_D/tests"
cat > "$PROJ_D/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_slice: ".wf/transient/design-slice.md"
  design_backlog: ".wf/design-backlog.md"
  learnings: ".wf/LEARNINGS.yaml"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
  tests: ["tests"]
YAML
cat > "$PROJ_D/.wf/transient/sprint.yaml" <<'YAML'
sprint_id: sprint-drain
tasks:
- id: T1
  covers: [REQ-1, REQ-2]
  serves: [CAP-3]
- id: T2
  system_tests:
  - id: SYS-TC-1
    description: bad X rejected end-to-end
    covers: [CAP-3]
  serves: [CAP-3]
- id: T3
  covers: [REQ-3]
  serves: [L-2]
- id: T4
  covers: [REQ-4]
  serves: [L-5]
YAML
cat > "$PROJ_D/.wf/transient/pipeline-state.yaml" <<'YAML'
current_phase: end_of_sprint
task_states:
  T1: {status: completed}
  T2: {status: completed}
  T3: {status: escalated}
  T4: {status: completed}
YAML
cat > "$PROJ_D/.wf/design-backlog.md" <<'MD'
# Design backlog

## Widget validation — serves CAP-3 / L-2 / L-7

**Narrative:** validation flows API -> core -> store.

**Component requirements:**
- **REQ-1** — the widget validates X  *(owner: core · CAP-3)*
- **REQ-2** — the API rejects bad X  *(owner: api · CAP-3 · proof: inspection — lint
  rule in .golangci.yml)*

**System test cases:**
- **SYS-TC-1** — bad X rejected end-to-end  *(covers CAP-3)*

## Perf hardening — serves L-2 / L-5

**Component requirements:**
- **REQ-3** — the store batches writes  *(owner: store · L-2)*
- **REQ-4** — the store caps fan-out  *(owner: store · L-5)*
MD
cat > "$PROJ_D/.wf/LEARNINGS.yaml" <<'YAML'
version: 1
learnings:
- id: L-2
  observation: batching
- id: L-7
  observation: validation gap
YAML
cat > "$PROJ_D/.wf/transient/design-slice.md" <<'MD'
# slice

## Supersedes

- **SYS-TC-9** — replaced end-to-end · successor **SYS-TC-1**
- **REQ-9** — replaced · successor **REQ-1**
MD
printf '// [SYS-TC:SYS-TC-9] old scenario\nfunc TestOld(t *testing.T) {}\n' > "$PROJ_D/tests/old_test.go"

CD="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D/.wf/config.yaml" --format json)"
BL="$PROJ_D/.wf/design-backlog.md"

# backlog trim: the fully-shipped design block is gone; the partial one keeps its unshipped id
grep -q "Widget validation" "$BL" && bad "drain: emptied design removed" "block survived" \
    || ok "drain: fully-shipped design block removed from the backlog"
grep -q "REQ-3" "$BL" && ok "drain: unshipped REQ-3 kept in the backlog" || bad "drain REQ-3 kept" "gone"
grep -q "REQ-4" "$BL" && bad "drain: shipped REQ-4 trimmed" "still present" \
    || ok "drain: shipped REQ-4 trimmed from the partial design"
grep -q "Perf hardening" "$BL" && ok "drain: partial design block survives" || bad "drain partial block" "gone"

# learnings: L-7 (last server emptied) drains; L-2 (still served by the partial design) stays
LRN="$PROJ_D/.wf/LEARNINGS.yaml"
[ "$(yget "$LRN" "[e['id'] for e in d['learnings']]")" = "['L-2']" ] \
    && ok "drain: L-7 drained, L-2 kept (still served)" || bad "drain learnings" "$(cat "$LRN")"

# drain report: capability candidate, shipped scenario, partial ship, survivors
RPT="$PROJ_D/.wf/transient/drain-report.yaml"
[ -f "$RPT" ] && ok "drain: report written" || bad "drain report" "missing"
[ "$(yget "$RPT" "d['reports'][0]['sprint_id']")" = "sprint-drain" ] \
    && ok "report: names the sprint" || bad "report sprint_id" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['emptied_designs']")" = "['Widget validation']" ] \
    && ok "report: emptied design listed" || bad "report emptied" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['proof_gate_candidates'][0]['capability']")" = "CAP-3" ] \
    && ok "report: CAP-3 is a proof-gate candidate" || bad "report candidate" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['proof_gate_candidates'][0]['shipped_scenarios'][0]['id']")" = "SYS-TC-1" ] \
    && ok "report: candidate carries its shipped scenario" || bad "report scenario" "$(cat "$RPT")"
[ "$(yget "$RPT" "sorted(d['reports'][0]['partially_shipped'][0]['remaining'])")" = "['REQ-3']" ] \
    && ok "report: partial design's remaining ids listed" || bad "report partial" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['learnings_drained']")" = "['L-7']" ] \
    && ok "report: drained learning listed" || bad "report learnings" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['superseded_survivors'][0]['id']")" = "SYS-TC-9" ] \
    && ok "report: surviving superseded SYS-TC tag listed" || bad "report survivors" "$(cat "$RPT")"

# archive: the backlog + learnings snapshots hold the PRE-trim state
grep -q "Widget validation" "$PROJ_D/.wf/archive/sprint-drain/"*__design-backlog.md 2>/dev/null \
    && ok "archive: backlog snapshot is pre-trim" || bad "archive pre-trim backlog" "$(ls "$PROJ_D/.wf/archive/sprint-drain" 2>&1)"
ls "$PROJ_D/.wf/archive/sprint-drain/"*__LEARNINGS.yaml >/dev/null 2>&1 \
    && ok "archive: learnings snapshot present" || bad "archive learnings snap" "$(ls "$PROJ_D/.wf/archive/sprint-drain" 2>&1)"

# the emitted summary carries the drain
[ "$(jget "$CD" "d['drain']['emptied_designs']")" = "['Widget validation']" ] \
    && ok "emit: drain summary in the command output" || bad "emit drain" "$CD"

# no-archive project: the trim still runs (draining is not archiving)
PROJ_D2="$(mktemp -d)"; mkdir -p "$PROJ_D2/.wf/transient"
cat > "$PROJ_D2/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_backlog: ".wf/design-backlog.md"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
YAML
printf 'sprint_id: s2\ntasks:\n- id: T1\n  covers: [REQ-8]\n  serves: [L-1]\n' > "$PROJ_D2/.wf/transient/sprint.yaml"
printf 'task_states:\n  T1: {status: completed}\n' > "$PROJ_D2/.wf/transient/pipeline-state.yaml"
printf '# Design backlog\n\n## Small fix — serves L-1\n\n**Component requirements:**\n- **REQ-8** — x  *(owner: core · L-1)*\n' > "$PROJ_D2/.wf/design-backlog.md"
"$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D2/.wf/config.yaml" --format json >/dev/null
grep -q "Small fix" "$PROJ_D2/.wf/design-backlog.md" && bad "no-archive trim" "block survived" \
    || ok "drain: trim runs without an archive configured"
[ -f "$PROJ_D2/.wf/transient/drain-report.yaml" ] && ok "drain: report written without an archive" || bad "no-archive report" "missing"

# ── summary ──
echo ""
echo "  pipeline brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
