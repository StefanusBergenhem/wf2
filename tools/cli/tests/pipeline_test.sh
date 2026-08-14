#!/usr/bin/env bash
#
# Tests for the wf pipeline brain — stage loading, the frontier query, stage timing,
# the PR-body accumulator, the completion gate's set-difference, and the close-time drain.
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
yget() { "$PYTHON" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"; }

# Make a fresh tmp project with a config and the given stage YAML on stdin.
new_proj() {
    local proj; proj="$(mktemp -d)"
    mkdir -p "$proj/.wf/transient"
    cat > "$proj/.wf/config.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
  pr_body: ".wf/transient/pr-body.md"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  pipeline_history: ".wf/transient/pipeline-history.yaml"
parallel:
  worktree_base: ".wf/transient/worktrees"
driver:
  max_parallel: 4
YAML
    cat > "$proj/.wf/transient/stage.yaml"
    echo "$proj"
}

wf() { local proj="$1"; shift; "$PYTHON" "$WF" "$@" --config "$proj/.wf/config.yaml"; }

# Seed pipeline_state task_states / stage from a YAML body on stdin.
seed_state() { cat > "$1/.wf/transient/pipeline-state.yaml"; }

# One cut. A stage IS the set of tasks with no dependency between them, so there is no
# depends_on, no layering, and no ordering to respect.
STAGE='stage: 7
serves: [CAP-3, L-2]
goal: "reject bad X at the edge"
checkpoint: "bad X is rejected end-to-end — observed by the SYS-TC-1 e2e"
decisions:
  - "Assumption — the zone table is authoritative"
  - "Deferral — bulk path left for later (→ L-142)"
tasks:
  - {id: S7-T1, covers: [CAP-3]}
  - {id: S7-T2, covers: [CAP-3]}
  - {id: S7-T3, covers: [L-2]}'

# ── load-stage ───────────────────────────────────────────────────────────────

PROJ="$(echo "$STAGE" | new_proj)"
OUT="$(wf "$PROJ" pipeline load-stage --format json)"
[ "$(jget "$OUT" "d['stage']")" = "7" ] \
    && ok "load-stage takes the stage id from the artifact's stage: key" || bad "load-stage id" "$OUT"
[ "$(jget "$OUT" "d['tasks']")" = "['S7-T1', 'S7-T2', 'S7-T3']" ] \
    && ok "load-stage records the stage's task list" || bad "load-stage tasks" "$OUT"
[ "$(jget "$OUT" "d['count']")" = "3" ] && ok "load-stage counts the tasks" || bad "load-stage count" "$OUT"
ST="$PROJ/.wf/transient/pipeline-state.yaml"
[ "$(yget "$ST" "d['stage']['id']")" = "7" ] \
    && ok "load-stage persists the stage id in the run state" || bad "load-stage persisted id" "$(cat "$ST")"
[ "$(yget "$ST" "d['stage']['tasks']")" = "['S7-T1', 'S7-T2', 'S7-T3']" ] \
    && ok "load-stage persists the task list" || bad "load-stage persisted tasks" "$(cat "$ST")"
[ "$(yget "$ST" "d['task_states']['S7-T3']['covers']")" = "['L-2']" ] \
    && ok "load-stage records what each task covers (the stage file dies at its merge)" \
    || bad "load-stage covers" "$(cat "$ST")"
[ "$(yget "$ST" "d['serves']")" = "['CAP-3', 'L-2']" ] \
    && ok "load-stage accumulates the stage's serves header" || bad "load-stage serves" "$(cat "$ST")"

# idempotent: a resumed run re-loads the same file and keeps its in-flight work
wf "$PROJ" pipeline dispatch --agent wf-build --task S7-T1 --attempt 1 >/dev/null
wf "$PROJ" pipeline load-stage >/dev/null
[ "$(jget "$(wf "$PROJ" pipeline task-state S7-T1 --format json)" "d['state']")" = "building" ] \
    && ok "load-stage is idempotent — a re-load keeps a task's live state" || bad "load-stage idempotent" ""
[ "$(yget "$ST" "sum(1 for e in d['history'] if e.get('event')=='stage_loaded')")" = "1" ] \
    && ok "load-stage: an unchanged re-load appends no second history event" || bad "load-stage history" "$(cat "$ST")"

# the NEXT stage is a fresh file — nothing of the last one is dropped or renumbered
wf "$PROJ" pipeline complete-task S7-T1 --commit abc --merge def >/dev/null
cat > "$PROJ/.wf/transient/stage.yaml" <<'YAML'
stage: 8
serves: [CAP-3, L-7]
tasks:
  - {id: S8-T1, covers: [L-7]}
YAML
OUT8="$(wf "$PROJ" pipeline load-stage --format json)"
[ "$(jget "$OUT8" "d['stage']")" = "8" ] && ok "load-stage: the next cut is a fresh stage id" || bad "load-stage next id" "$OUT8"
[ "$(jget "$OUT8" "d['tasks']")" = "['S8-T1']" ] && ok "load-stage: only this stage's tasks are the frontier" || bad "load-stage next tasks" "$OUT8"
[ "$(jget "$(wf "$PROJ" pipeline task-state S7-T1 --format json)" "d['state']")" = "completed" ] \
    && ok "load-stage: the previous stage's merge record survives the next cut" || bad "load-stage merge record" ""
[ "$(yget "$ST" "d['serves']")" = "['CAP-3', 'L-2', 'L-7']" ] \
    && ok "load-stage: serves accumulate across the sprint's stages" || bad "load-stage serves accum" "$(cat "$ST")"

# a stage artifact with no id, or no tasks, is a mechanical failure
PROJ_NOID="$(printf 'tasks:\n  - {id: S1-T1}\n' | new_proj)"
if wf "$PROJ_NOID" pipeline load-stage >/dev/null 2>&1; then
    bad "load-stage: a stage with no id should fail" "exited 0"
else
    ok "load-stage: a stage declaring no id → non-zero exit"
fi
PROJ_NOT="$(printf 'stage: 3\ntasks: []\n' | new_proj)"
if wf "$PROJ_NOT" pipeline load-stage >/dev/null 2>&1; then
    bad "load-stage: a stage with no tasks should fail" "exited 0"
else
    ok "load-stage: a stage declaring no tasks → non-zero exit"
fi

# ── next (frontier) ──────────────────────────────────────────────────────────

PROJ1="$(echo "$STAGE" | new_proj)"
wf "$PROJ1" pipeline load-stage >/dev/null
N1="$(wf "$PROJ1" pipeline next --format json)"
[ "$(jget "$N1" "[e['task_id'] for e in d['dispatch']]")" = "['S7-T1', 'S7-T2', 'S7-T3']" ] \
    && ok "next: the whole stage dispatches at once — its tasks are independent" || bad "next dispatch" "$N1"
[ "$(jget "$N1" "d['stage']['id']")" = "7" ] && ok "next: names the loaded stage" || bad "next stage id" "$N1"
[ "$(jget "$N1" "d['stage']['tasks']")" = "['S7-T1', 'S7-T2', 'S7-T3']" ] \
    && ok "next: carries the stage's task list" || bad "next stage tasks" "$N1"
[ "$(jget "$N1" "d['terminal']['stage_done']")" = "False" ] && ok "next: stage not done with pending work" || bad "stage_done" "$N1"
[ "$(jget "$N1" "sorted(d['terminal'])")" = "['halt', 'stage_done']" ] \
    && ok "next: the frontier has exactly one terminal flag — no increment, no sprint" || bad "terminal keys" "$N1"
[ "$(jget "$N1" "d['dispatch'][0]['worktree']")" = ".wf/transient/worktrees/sprint-S7-T1" ] \
    && ok "next: dispatch carries the computed worktree path" || bad "worktree" "$N1"
[ "$(jget "$N1" "sorted(d['dispatch'][0])")" = "['task_id', 'worktree']" ] \
    && ok "next: dispatch entry carries exactly task_id + worktree" || bad "dispatch entry keys" "$N1"

# driver.max_parallel is the only thing serialising a stage
PROJ_CAP="$(echo "$STAGE" | new_proj)"
"$PYTHON" - "$PROJ_CAP/.wf/config.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]; d=yaml.safe_load(open(p)); d['driver']['max_parallel']=2
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_CAP" pipeline load-stage >/dev/null
NCAP="$(wf "$PROJ_CAP" pipeline next --format json)"
[ "$(jget "$NCAP" "[e['task_id'] for e in d['dispatch']]")" = "['S7-T1', 'S7-T2']" ] \
    && ok "next: driver.max_parallel caps the dispatch" || bad "cap dispatch" "$NCAP"
[ "$(jget "$NCAP" "d['ready']")" = "['S7-T3']" ] && ok "next: the excess queues as ready" || bad "cap ready" "$NCAP"

# an in-flight task occupies a slot and is not re-dispatched
PROJ_IF="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_IF" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: building}
YAML
NIF="$(wf "$PROJ_IF" pipeline next --format json)"
[ "$(jget "$NIF" "[e['task_id'] for e in d['in_flight']]")" = "['S7-T1']" ] \
  && ok "next: a building task is in_flight" || bad "in_flight" "$NIF"
[ "$(jget "$NIF" "d['in_flight'][0]['status']")" = "building" ] \
  && ok "next: in_flight carries the task status" || bad "in_flight status" "$NIF"
# the dispatch that started it, and how long ago — a never-spawned agent's tell (L-053)
wf "$PROJ_IF" pipeline dispatch --agent wf-build --task S7-T1 --attempt 1 >/dev/null
NIF="$(wf "$PROJ_IF" pipeline next --format json)"
[ "$(jget "$NIF" "d['in_flight'][0]['agent']")" = "wf-build" ] \
  && ok "next: in_flight names the dispatched agent" || bad "in_flight agent" "$NIF"
[ "$(jget "$NIF" "d['in_flight'][0]['dispatched_at'] is not None and d['in_flight'][0]['since_s'] >= 0")" = "True" ] \
  && ok "next: in_flight carries the dispatch age" || bad "in_flight since_s" "$NIF"
[ "$(jget "$NIF" "[e['task_id'] for e in d['dispatch']]")" = "['S7-T2', 'S7-T3']" ] \
    && ok "next: an in-flight task is not re-dispatched" || bad "in_flight dispatch" "$NIF"

# every task settled → stage_done
PROJ_SD="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_SD" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: completed}
  S7-T2: {status: completed}
  S7-T3: {status: completed}
YAML
NSD="$(wf "$PROJ_SD" pipeline next --format json)"
[ "$(jget "$NSD" "d['terminal']['stage_done']")" = "True" ] \
    && ok "next: stage_done when every task settled" || bad "stage_done true" "$NSD"

# a blocked task dooms NOTHING: the stage closes with what merged and the blocked work
# re-enters at the next cut
PROJ_BL="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_BL" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: completed}
  S7-T2: {status: blocked}
  S7-T3: {status: completed}
YAML
NBL="$(wf "$PROJ_BL" pipeline next --format json)"
[ "$(jget "$NBL" "d['blocked']")" = "['S7-T2']" ] && ok "next: a blocked task surfaces under blocked[]" || bad "blocked list" "$NBL"
[ "$(jget "$NBL" "d['terminal']['stage_done']")" = "True" ] \
    && ok "next: a blocked task does not hold the stage open" || bad "blocked stage_done" "$NBL"
[ "$(jget "$NBL" "[e['task_id'] for e in d['dispatch']]")" = "[]" ] \
    && ok "next: a blocked task is not re-dispatched" || bad "blocked dispatch" "$NBL"

# a parked design-issue task and an escalated one likewise do not hold the stage open
PROJ_DI="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_DI" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: completed}
  S7-T2: {status: design_issue}
  S7-T3: {status: escalated}
YAML
NDI="$(wf "$PROJ_DI" pipeline next --format json)"
[ "$(jget "$NDI" "d['repairing']")" = "['S7-T2']" ] && ok "next: design_issue task surfaces as repairing" || bad "repairing" "$NDI"
[ "$(jget "$NDI" "d['escalated']")" = "['S7-T3']" ] && ok "next: escalated task surfaces under escalated[]" || bad "escalated" "$NDI"
[ "$(jget "$NDI" "d['terminal']['stage_done']")" = "True" ] && ok "next: parked/escalated do not hold the stage open" || bad "parked stage_done" "$NDI"

# an approved task (awaiting the batch merge) is settled — surfaced, not re-dispatched
PROJ_APN="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_APN" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: approved}
  S7-T2: {status: completed}
  S7-T3: {status: completed}
YAML
NAPN="$(wf "$PROJ_APN" pipeline next --format json)"
[ "$(jget "$NAPN" "d['approved']")" = "['S7-T1']" ] && ok "next: approved task surfaces under approved[]" || bad "approved list" "$NAPN"
[ "$(jget "$NAPN" "[e['task_id'] for e in d['dispatch']]")" = "[]" ] && ok "next: approved task is not re-dispatched" || bad "approved dispatch" "$NAPN"
[ "$(jget "$NAPN" "d['terminal']['stage_done']")" = "True" ] && ok "next: approved task does not hold the stage open" || bad "approved stage_done" "$NAPN"

# next before load-stage → halt
PROJ_NC="$(echo "$STAGE" | new_proj)"
NNC="$(wf "$PROJ_NC" pipeline next --format json)"
[ "$(jget "$NNC" "d['terminal']['halt'] is not None")" = "True" ] \
    && ok "next: halts when no stage is loaded" || bad "unloaded halt" "$NNC"
[ "$(jget "$NNC" "d['terminal']['stage_done']")" = "False" ] \
    && ok "next: a halt frontier is never stage_done" || bad "halt stage_done" "$NNC"

# ── run-state mutations ──────────────────────────────────────────────────────

PROJ_M="$(echo "$STAGE" | new_proj)"

# transition writes phase + history
wf "$PROJ_M" pipeline transition --to designing --reason kickoff >/dev/null
CP="$(wf "$PROJ_M" pipeline current-phase --format json)"
[ "$(jget "$CP" "d['phase']")" = "designing" ] && ok "transition sets current_phase" || bad "transition" "$CP"
[ "$(jget "$CP" "d['stage'] is None")" = "True" ] \
    && ok "current-phase reports no stage before load-stage" || bad "current-phase stage" "$CP"
wf "$PROJ_M" pipeline load-stage >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline current-phase --format json)" "d['stage']")" = "7" ] \
    && ok "current-phase reports the loaded stage id" || bad "current-phase stage id" ""

# --sprint-id records whose sprint this run state belongs to, so `next` names the
# driver's sprint in the worktree path
PROJ_SID="$(echo "$STAGE" | new_proj)"
wf "$PROJ_SID" pipeline transition --to sprint_start --sprint-id s7 >/dev/null
[ "$(yget "$PROJ_SID/.wf/transient/pipeline-state.yaml" "d['sprint_id']")" = "s7" ] \
    && ok "transition --sprint-id records the sprint id in the run state" \
    || bad "transition sprint_id" "$(cat "$PROJ_SID/.wf/transient/pipeline-state.yaml")"
wf "$PROJ_SID" pipeline load-stage >/dev/null
NSID="$(wf "$PROJ_SID" pipeline next --format json)"
[ "$(jget "$NSID" "d['dispatch'][0]['worktree']")" = ".wf/transient/worktrees/s7-S7-T1" ] \
    && ok "next: the recorded sprint id names the worktree" || bad "next sprint_id" "$NSID"

# a later transition without the flag keeps the recorded id
wf "$PROJ_SID" pipeline transition --to stage_run >/dev/null
[ "$(yget "$PROJ_SID/.wf/transient/pipeline-state.yaml" "d['sprint_id']")" = "s7" ] \
    && ok "transition without --sprint-id leaves the recorded id alone" \
    || bad "transition sprint_id kept" "$(cat "$PROJ_SID/.wf/transient/pipeline-state.yaml")"

# current-phase resolves sprint_branch from git when the stored value is null (L-020)
PROJ_GB="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_GB" <<'YAML'
current_phase: stage_run
sprint_branch: null
YAML
( cd "$PROJ_GB" && git init -q && git config user.email t@t.t && git config user.name t \
  && git commit -q --allow-empty -m init && git checkout -q -b sprint/demo ) 2>/dev/null
CPGB="$(wf "$PROJ_GB" pipeline current-phase --format json)"
[ "$(jget "$CPGB" "d['sprint_branch']")" = "sprint/demo" ] \
  && ok "current-phase: sprint_branch falls back to the git branch when stored null" || bad "L-020 git fallback" "$CPGB"

# dispatch maps agent → task state, records pass_index
DSPC="$(wf "$PROJ_M" pipeline dispatch --agent wf-build --task S7-T1 --attempt 1 --format json)"
[ "$(jget "$DSPC" "d.get('ok') is True and d.get('event')=='dispatch' and d.get('status')=='building'")" = "True" ] \
    && ok "dispatch emits a success confirmation (L-059)" || bad "dispatch confirm" "$DSPC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T1 --format json)" "d['state']")" = "building" ] \
    && ok "dispatch wf-build → building" || bad "dispatch build state" ""
wf "$PROJ_M" pipeline dispatch --agent wf-security-review --task S7-T1 --attempt 1 --pass 1 >/dev/null
TSR="$(wf "$PROJ_M" pipeline task-state S7-T1 --format json)"
[ "$(jget "$TSR" "d['state']")" = "reviewing" ] && ok "dispatch a review pass → reviewing" || bad "dispatch review state" "$TSR"
[ "$(jget "$TSR" "d['pass_index']")" = "1" ] && ok "dispatch records pass_index" || bad "pass_index" "$TSR"

# complete-task → completed + commits
CMPC="$(wf "$PROJ_M" pipeline complete-task S7-T1 --commit abc123 --merge def456 --format json)"
[ "$(jget "$CMPC" "d.get('ok') is True and d.get('event')=='task_completed' and d.get('status')=='completed'")" = "True" ] \
    && ok "complete-task emits a success confirmation (L-059)" || bad "complete confirm" "$CMPC"
TSC="$(wf "$PROJ_M" pipeline task-state S7-T1 --format json)"
[ "$(jget "$TSC" "d['state']")" = "completed" ] && ok "complete-task → completed" || bad "complete" "$TSC"
[ "$(jget "$TSC" "d['build_commit']")" = "abc123" ] && ok "complete-task records build_commit" || bad "build_commit" "$TSC"

# approve-task → approved (passed all passes, awaiting the batch merge)
PROJ_AP="$(echo "$STAGE" | new_proj)"
APPC="$(wf "$PROJ_AP" pipeline approve-task S7-T1 --commit cab00d --format json)"
[ "$(jget "$APPC" "d.get('ok') is True and d.get('event')=='task_approved' and d.get('status')=='approved'")" = "True" ] \
    && ok "approve-task emits a success confirmation (L-059)" || bad "approve confirm" "$APPC"
TSAP="$(wf "$PROJ_AP" pipeline task-state S7-T1 --format json)"
[ "$(jget "$TSAP" "d['state']")" = "approved" ] && ok "approve-task → approved" || bad "approve" "$TSAP"
[ "$(jget "$TSAP" "d['build_commit']")" = "cab00d" ] && ok "approve-task records build_commit" || bad "approve build_commit" "$TSAP"

# reject-task → building, attempt++, pass_index reset to 0 (N-pass restart at build)
wf "$PROJ_M" pipeline dispatch --agent wf-review --task S7-T2 --attempt 1 --pass 1 >/dev/null
RJC="$(wf "$PROJ_M" pipeline reject-task S7-T2 --feedback /tmp/fb.yaml --format json)"
[ "$(jget "$RJC" "d.get('ok') is True and d.get('event')=='task_rejected'")" = "True" ] \
    && ok "reject-task emits a success confirmation (L-059)" || bad "reject confirm" "$RJC"
TSJ="$(wf "$PROJ_M" pipeline task-state S7-T2 --format json)"
[ "$(jget "$TSJ" "d['state']")" = "building" ] && ok "reject-task → building" || bad "reject state" "$TSJ"
[ "$(jget "$TSJ" "d['attempt_counter']")" = "1" ] && ok "reject-task bumps attempt_counter" || bad "reject attempt" "$TSJ"
[ "$(jget "$TSJ" "d['pass_index']")" = "0" ] && ok "reject-task resets pass_index to 0" || bad "reject pass_index" "$TSJ"

# retry-task → building, attempt++ — a build that returned no artifact at all spends an
# attempt and goes back in; nothing about the task is decided yet, so it is not blocked
RTC="$(wf "$PROJ_M" pipeline retry-task S7-T2 --reason "build returned escalate_no_artifacts" --format json)"
[ "$(jget "$RTC" "d.get('ok') is True and d.get('event')=='task_retried'")" = "True" ] \
    && ok "retry-task emits a success confirmation (L-059)" || bad "retry confirm" "$RTC"
TSR="$(wf "$PROJ_M" pipeline task-state S7-T2 --format json)"
[ "$(jget "$TSR" "d['state']")" = "building" ] && ok "retry-task → building" || bad "retry state" "$TSR"
[ "$(jget "$TSR" "d['attempt_counter']")" = "2" ] && ok "retry-task bumps attempt_counter" || bad "retry attempt" "$TSR"

# block-task → blocked, terminal for that task and nothing else. The reason survives:
# the driver reads it back into the design issue that carries the work to the next cut.
BLC="$(wf "$PROJ_M" pipeline block-task S7-T3 --reason "review rejected it every attempt" --format json)"
[ "$(jget "$BLC" "d.get('ok') is True and d.get('event')=='task_blocked' and d.get('status')=='blocked'")" = "True" ] \
    && ok "block-task emits a success confirmation (L-059)" || bad "block confirm" "$BLC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T3 --format json)" "d['state']")" = "blocked" ] \
    && ok "block-task → blocked" || bad "block" ""
[ "$(yget "$PROJ_M/.wf/transient/pipeline-state.yaml" "d['blocked_tasks']['S7-T3']['reason']")" = "review rejected it every attempt" ] \
    && ok "block-task keeps the reason" || bad "block reason" "$(cat "$PROJ_M/.wf/transient/pipeline-state.yaml")"
# ...and nothing else moves: a block dooms no sibling, so there is no propagation to undo
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T1 --format json)" "d['state']")" = "completed" ] \
    && ok "block-task dooms no sibling task" || bad "block blast radius" ""

# record-design-issue → design_issue + surfaces as unresolved
RDC="$(wf "$PROJ_M" pipeline record-design-issue DI-1 --task S7-T2 --severity high --fix_kind component_defect --format json)"
[ "$(jget "$RDC" "d.get('ok') is True and d.get('event')=='design_issue_recorded' and d.get('di_id')=='DI-1'")" = "True" ] \
    && ok "record-design-issue emits a success confirmation (L-059)" || bad "record-DI confirm" "$RDC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T2 --format json)" "d['state']")" = "design_issue" ] \
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
# resolve-design-issue un-parks the implicated task (design_issue → pending)
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T2 --format json)" "d['state']")" = "pending" ] \
    && ok "resolve-design-issue resets the parked task to pending" || bad "DI resolve unpark" \
    "$(wf "$PROJ_M" pipeline task-state S7-T2 --format json)"
# ...but never clobbers a task that has already moved on (e.g. re-dispatched → building)
wf "$PROJ_M" pipeline record-design-issue DI-2 --task S7-T3 --severity low --fix_kind no_change >/dev/null
wf "$PROJ_M" pipeline dispatch --agent wf-build --task S7-T3 --attempt 1 >/dev/null
wf "$PROJ_M" pipeline resolve-design-issue DI-2 >/dev/null
[ "$(jget "$(wf "$PROJ_M" pipeline task-state S7-T3 --format json)" "d['state']")" = "building" ] \
    && ok "resolve-design-issue leaves a non-parked task status alone" || bad "DI resolve non-parked" \
    "$(wf "$PROJ_M" pipeline task-state S7-T3 --format json)"

# A boundary DI is task-less: record-design-issue with no --task records the DI and
# parks NO phantom task.
wf "$PROJ_M" pipeline record-design-issue DI-STAGE --severity medium --fix_kind no_change >/dev/null
UDS="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDS" "any(i['di_id']=='DI-STAGE' for i in d['issues'])")" = "True" ] \
    && ok "record-design-issue records a task-less (boundary) DI" || bad "task-less DI record" "$UDS"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state DI-STAGE --format json)" "d['state']")" = "pending" ] \
    && ok "task-less DI parks no phantom task" || bad "task-less DI phantom park" \
    "$(wf "$PROJ_M" pipeline task-state DI-STAGE --format json)"
wf "$PROJ_M" pipeline resolve-design-issue DI-STAGE >/dev/null
UDS2="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDS2" "any(i['di_id']=='DI-STAGE' for i in d['issues'])")" = "False" ] \
    && ok "task-less DI resolves cleanly" || bad "task-less DI resolve" "$UDS2"

# Build/review/stage-repair raise a BARE issue — no --fix_kind.
wf "$PROJ_M" pipeline record-design-issue DI-BARE --task S7-T2 --severity high >/dev/null 2>&1 \
    && ok "record-design-issue accepts a bare issue (no --fix_kind)" || bad "bare DI record" "requires --fix_kind"
UDB="$(wf "$PROJ_M" pipeline unresolved-design-issues --format json)"
[ "$(jget "$UDB" "any(i['di_id']=='DI-BARE' for i in d['issues'])")" = "True" ] \
    && ok "a bare issue surfaces as unresolved" || bad "bare DI unresolved" "$UDB"

# reclaim-stale flips an orphan slot back to pending
PROJ_R="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_R" <<'YAML'
task_states:
  S7-T1: {status: reviewing}
  S7-T2: {status: completed}
YAML
RC="$(wf "$PROJ_R" pipeline reclaim-stale --format json)"
[ "$(jget "$RC" "[r['task_id'] for r in d['reclaimed']]")" = "['S7-T1']" ] \
    && ok "reclaim-stale reclaims the in-flight orphan only" || bad "reclaim" "$RC"
[ "$(jget "$(wf "$PROJ_R" pipeline task-state S7-T1 --format json)" "d['state']")" = "pending" ] \
    && ok "reclaim-stale resets orphan to pending" || bad "reclaim state" ""

# ── stage timing + summary, keyed on the monotonic id ────────────────────────

PROJ_TM="$(echo "$STAGE" | new_proj)"
wf "$PROJ_TM" pipeline load-stage >/dev/null
SS="$(wf "$PROJ_TM" pipeline stage-start --format json)"
[ "$(jget "$SS" "d['stage']")" = "7" ] && ok "stage-start keys on the loaded stage id" || bad "stage-start id" "$SS"
[ -n "$(jget "$SS" "d['started_at']")" ] && ok "stage-start records started_at" || bad "stage-start" "$SS"
SS2="$(wf "$PROJ_TM" pipeline stage-start --format json)"
[ "$(jget "$SS2" "d['started_at']")" = "$(jget "$SS" "d['started_at']")" ] \
    && ok "stage-start is idempotent — a resumed stage keeps its origin" || bad "stage-start idempotent" "$SS2"
SE="$(wf "$PROJ_TM" pipeline stage-end --format json)"
[ "$(jget "$SE" "'duration_seconds' in d['timing']")" = "True" ] && ok "stage-end records duration_seconds" || bad "stage-end" "$SE"
[ "$(yget "$PROJ_TM/.wf/transient/pipeline-state.yaml" "sorted(d['stage_summaries'])")" = "[7]" ] \
    && ok "stage timing is stored under the monotonic stage id" || bad "timing key" "$(cat "$PROJ_TM/.wf/transient/pipeline-state.yaml")"

# The s1 bug, now structurally impossible: sub-layer numbers restarted every increment
# while a summary was keyed by that number alone, so the next increment's stage 1
# inherited the previous one's started_at and reported a 28-hour stage. A repo-lifetime
# monotonic id cannot collide, so a new stage always gets a fresh origin.
"$PYTHON" - "$PROJ_TM/.wf/transient/pipeline-state.yaml" <<'PY'
import sys, yaml
p = sys.argv[1]; d = yaml.safe_load(open(p))
d["stage_summaries"][7]["timing"]["started_at"] = "2020-01-01T00:00:00Z"
open(p, "w").write(yaml.safe_dump(d, sort_keys=False))
PY
cat > "$PROJ_TM/.wf/transient/stage.yaml" <<'YAML'
stage: 8
serves: [CAP-3]
tasks:
  - {id: S8-T1, covers: [CAP-3]}
YAML
wf "$PROJ_TM" pipeline load-stage >/dev/null
XS="$(wf "$PROJ_TM" pipeline stage-start --format json)"
[ "$(jget "$XS" "d['stage']")" = "8" ] && ok "stage-start follows the newly loaded stage" || bad "stage-start next id" "$XS"
[ "$(jget "$XS" "d['started_at'].startswith('2020')")" = "False" ] \
    && ok "stage-start: a new stage never inherits the previous stage's origin" || bad "inherited origin" "$XS"
[ "$(yget "$PROJ_TM/.wf/transient/pipeline-state.yaml" "d['stage_summaries'][7]['timing']['started_at']")" = "2020-01-01T00:00:00Z" ] \
    && ok "stage timing: the earlier stage's own record is untouched" || bad "earlier timing" "$(cat "$PROJ_TM/.wf/transient/pipeline-state.yaml")"

# stage-start/end/summary need a loaded stage — there is no number to guess at
PROJ_NS="$(echo "$STAGE" | new_proj)"
if wf "$PROJ_NS" pipeline stage-start >/dev/null 2>&1; then
    bad "stage-start with no stage loaded should fail" "exited 0"
else
    ok "stage-start: no stage loaded → non-zero exit"
fi

# stage-summary derives lists from task_states
PROJ_SS="$(echo "$STAGE" | new_proj)"
seed_state "$PROJ_SS" <<'YAML'
stage: {id: 7, tasks: [S7-T1, S7-T2, S7-T3]}
task_states:
  S7-T1: {status: completed, merge_commit: deadbee}
  S7-T2: {status: escalated}
  S7-T3: {status: blocked}
YAML
SUM="$(wf "$PROJ_SS" pipeline stage-summary --format json)"
[ "$(jget "$SUM" "d['stage']")" = "7" ] && ok "stage-summary names the loaded stage" || bad "summary stage" "$SUM"
[ "$(jget "$SUM" "d['completed']")" = "['S7-T1']" ] && ok "stage-summary derives completed" || bad "summary completed" "$SUM"
[ "$(jget "$SUM" "d['escalated']")" = "['S7-T2']" ] && ok "stage-summary derives escalated" || bad "summary escalated" "$SUM"
[ "$(jget "$SUM" "d['blocked']")" = "['S7-T3']" ] && ok "stage-summary derives blocked" || bad "summary blocked" "$SUM"
[ "$(jget "$SUM" "d['merged'][0]['merge_commit']")" = "deadbee" ] && ok "stage-summary carries the merge commits" || bad "summary merged" "$SUM"

# ── append-pr-body: the PR accumulates one block per merged stage ─────────────

PROJ_PR="$(echo "$STAGE" | new_proj)"
AP="$(wf "$PROJ_PR" pipeline append-pr-body --format json)"
PRB="$PROJ_PR/.wf/transient/pr-body.md"
[ "$(jget "$AP" "d['appended']")" = "True" ] && ok "append-pr-body appends the stage's block" || bad "pr-body appended" "$AP"
[ "$(jget "$AP" "d['stage']")" = "7" ] && ok "append-pr-body names the stage" || bad "pr-body stage" "$AP"
grep -q "^## Stage 7$" "$PRB" && ok "append-pr-body heads the block with the stage id" || bad "pr-body heading" "$(cat "$PRB")"
grep -q "CAP-3, L-2" "$PRB" && ok "append-pr-body carries the serves header" || bad "pr-body serves" "$(cat "$PRB")"
grep -q "bad X is rejected end-to-end" "$PRB" && ok "append-pr-body carries the checkpoint" || bad "pr-body checkpoint" "$(cat "$PRB")"
grep -q "the zone table is authoritative" "$PRB" && ok "append-pr-body carries the decision log" || bad "pr-body decisions" "$(cat "$PRB")"

# idempotent: a re-run at the same stage merge never duplicates the block (L-106)
BEFORE="$(cat "$PRB")"
AP2="$(wf "$PROJ_PR" pipeline append-pr-body --format json)"
[ "$(jget "$AP2" "d['appended']")" = "False" ] && ok "append-pr-body: the same stage appends nothing twice" || bad "pr-body idempotent" "$AP2"
[ "$BEFORE" = "$(cat "$PRB")" ] && ok "append-pr-body: an idempotent re-run does not rewrite the file (L-106)" || bad "pr-body rewrite" "$(cat "$PRB")"

# a PR batching several stages concatenates their blocks
cat > "$PROJ_PR/.wf/transient/stage.yaml" <<'YAML'
stage: 8
serves: [L-7]
checkpoint: "the importer skips bad rows"
decisions: ["Deferral — authz on the import endpoint (→ L-143)"]
tasks:
  - {id: S8-T1, covers: [L-7]}
YAML
wf "$PROJ_PR" pipeline append-pr-body >/dev/null
[ "$(grep -c "^## Stage " "$PRB")" = "2" ] \
    && ok "append-pr-body: a PR batching several stages concatenates them" || bad "pr-body concat" "$(cat "$PRB")"
grep -q "^## Stage 7$" "$PRB" && ok "append-pr-body: the earlier stage's block survives" || bad "pr-body earlier block" "$(cat "$PRB")"

# ── capability-complete: the mechanical trigger for adequacy gate 2 ───────────
# Set-difference each entry's authored scenario set against the ids the TEST TREE
# provably carries. Shipped-ness is never stored — it is read off the [SYS-TC:] tags.

PROJ_CC="$(mktemp -d)"; mkdir -p "$PROJ_CC/.wf/transient" "$PROJ_CC/tests"
cat > "$PROJ_CC/.wf/config.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  tests: ["tests"]
YAML
cat > "$PROJ_CC/.wf/CAPABILITIES.yaml" <<'YAML'
version: 1
capabilities:
- id: CAP-3
  statement: "Operators can reject bad X."
  system_tests:
  - {id: SYS-TC-1, title: bad X rejected at the edge}
  - {id: SYS-TC-2, title: bad X rejected in bulk}
- id: CAP-4
  statement: "Operators can audit a rejection."
  system_tests:
  - {id: SYS-TC-2, title: bad X rejected in bulk}
- id: CAP-5
  statement: "Not taken up yet — no scenario set."
YAML
cat > "$PROJ_CC/.wf/LEARNINGS.yaml" <<'YAML'
version: 1
learnings:
- id: L-2
  observation: end-to-end enough to carry a scenario
  system_tests:
  - {id: SYS-TC-3, title: the retry path is observable}
- id: L-9
  observation: proved by its task's own criteria
YAML
printf '// [SYS-TC:SYS-TC-2] bad X rejected in bulk\nfunc TestBulk(t *testing.T) {}\n' > "$PROJ_CC/tests/bulk_test.go"

CC="$("$PYTHON" "$WF" pipeline capability-complete --config "$PROJ_CC/.wf/config.yaml" --format json)"
[ "$(jget "$CC" "[e['id'] for e in d['complete']]")" = "['CAP-4']" ] \
    && ok "capability-complete: only a fully shipped set is complete" || bad "cap-complete complete" "$CC"
[ "$(jget "$CC" "[e['id'] for e in d['pending']]")" = "['CAP-3', 'L-2']" ] \
    && ok "capability-complete: an unshipped scenario keeps the entry pending" || bad "cap-complete pending" "$CC"
[ "$(jget "$CC" "[e['missing'] for e in d['pending'] if e['id']=='CAP-3'][0]")" = "['SYS-TC-1']" ] \
    && ok "capability-complete: a pending entry names the ids still to ship" || bad "cap-complete missing" "$CC"
[ "$(jget "$CC" "[e['kind'] for e in d['pending'] if e['id']=='L-2'][0]")" = "learning" ] \
    && ok "capability-complete: a learning's set is set-differenced the same way" || bad "cap-complete learning" "$CC"
[ "$(jget "$CC" "any(e['id'] in ('CAP-5','L-9') for e in d['complete']+d['pending'])")" = "False" ] \
    && ok "capability-complete: an entry carrying no set is not a candidate" || bad "cap-complete no set" "$CC"
[ "$(jget "$CC" "d['shipped']")" = "['SYS-TC-2']" ] \
    && ok "capability-complete: shipped derives from the tags, never from stored state" || bad "cap-complete shipped" "$CC"

# a cross-cutting scenario is duplicated under both entries with the SAME id — shipping
# it once completes both
printf '// [SYS-TC:SYS-TC-1] bad X rejected at the edge\nfunc TestEdge(t *testing.T) {}\n' > "$PROJ_CC/tests/edge_test.go"
CC2="$("$PYTHON" "$WF" pipeline capability-complete --config "$PROJ_CC/.wf/config.yaml" --format json)"
[ "$(jget "$CC2" "[e['id'] for e in d['complete']]")" = "['CAP-3', 'CAP-4']" ] \
    && ok "capability-complete: one tag satisfies every entry the scenario is nested under" || bad "cap-complete cross-cut" "$CC2"

# ── complete-sprint: the PR-packaging close ──────────────────────────────────

PROJ_CS="$(echo "$STAGE" | new_proj)"
wf "$PROJ_CS" pipeline transition --to closeout >/dev/null
wf "$PROJ_CS" pipeline complete-sprint >/dev/null
[ "$(jget "$(wf "$PROJ_CS" pipeline current-phase --format json)" "d['phase']")" = "idle" ] \
    && ok "complete-sprint resets phase to idle" || bad "complete-sprint phase" ""
[ "$(jget "$(wf "$PROJ_CS" pipeline current-phase --format json)" "d['stage'] is None")" = "True" ] \
    && ok "complete-sprint clears the loaded stage" || bad "complete-sprint stage" ""

# ── complete-sprint: the close-time drain ────────────────────────────────────
# What the sprint's stages served (accumulated at every load-stage, since each stage
# artifact dies at its own merge) plus the merge record. A learning drains when every
# task covering it merged.

PROJ_D2="$(mktemp -d)"; mkdir -p "$PROJ_D2/.wf/transient" "$PROJ_D2/tests"
cat > "$PROJ_D2/.wf/config.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  capabilities: ".wf/CAPABILITIES.yaml"
  design_issues: ".wf/transient/design-issues.yaml"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
  tests: ["tests"]
YAML
cat > "$PROJ_D2/.wf/LEARNINGS.yaml" <<'YAML'
# .wf/LEARNINGS.yaml — project-code learnings (committed).
# The header comment MUST survive a drain (L-106).
version: 1
learnings:
- id: L-2
  observation: batching — keep the — dash
- id: L-5
  observation: fan-out cap
- id: L-9
  observation: untouched by this sprint
YAML
cat > "$PROJ_D2/.wf/CAPABILITIES.yaml" <<'YAML'
version: 1
capabilities:
- id: CAP-3
  statement: "Operators can reject bad X."
YAML
printf 'issues:\n- id: DI-S7-T2\n  status: open\n' > "$PROJ_D2/.wf/transient/design-issues.yaml"
printf '// [SYS-TC:SYS-TC-9] old scenario\nfunc TestOld(t *testing.T) {}\n' > "$PROJ_D2/tests/old_test.go"

# stage 7 merges: it served CAP-3 and L-2 ... and is then archived and deleted
cat > "$PROJ_D2/.wf/transient/stage.yaml" <<'YAML'
stage: 7
serves: [CAP-3, L-2]
tasks:
- {id: S7-T1, covers: [CAP-3]}
- {id: S7-T2, covers: [L-2]}
YAML
"$PYTHON" "$WF" pipeline transition --to sprint_start --sprint-id sprint-drain --config "$PROJ_D2/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline load-stage --config "$PROJ_D2/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline complete-task S7-T1 --commit a1 --merge m1 --config "$PROJ_D2/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline block-task S7-T2 --reason "review rejected it every attempt" --config "$PROJ_D2/.wf/config.yaml" >/dev/null
rm "$PROJ_D2/.wf/transient/stage.yaml"

# stage 8 is the cut still on disk when the PR ships
cat > "$PROJ_D2/.wf/transient/stage.yaml" <<'YAML'
stage: 8
serves: [L-5, L-8]
supersessions:
- what: the old rejection path — proven by tests/old_test.go (SYS-TC-9)
  ruling: human-ruled
tasks:
- {id: S8-T1, covers: [L-5]}
YAML
"$PYTHON" "$WF" pipeline load-stage --config "$PROJ_D2/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline complete-task S8-T1 --commit a2 --merge m2 --config "$PROJ_D2/.wf/config.yaml" >/dev/null

CD="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D2/.wf/config.yaml" --format json)"
LRN="$PROJ_D2/.wf/LEARNINGS.yaml"
RPT="$PROJ_D2/.wf/transient/drain-report.yaml"

# L-5's only task merged → drains. L-2's task blocked → stays. L-9 was never served.
[ "$(yget "$LRN" "[e['id'] for e in d['learnings']]")" = "['L-2', 'L-9']" ] \
    && ok "drain: L-5 drained (its task merged), L-2 and L-9 kept" || bad "drain learnings" "$(cat "$LRN")"
grep -q "header comment MUST survive" "$LRN" \
    && ok "drain: the learnings file's comments survive the rewrite (L-106)" || bad "drain comments" "$(cat "$LRN")"
grep -q "batching — keep the — dash" "$LRN" \
    && ok "drain: unicode survives the rewrite (L-106)" || bad "drain unicode" "$(cat "$LRN")"

# capabilities never drain on the merge record — only an adequacy verdict drains one
[ "$(yget "$PROJ_D2/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-3']" ] \
    && ok "drain: a served capability is NOT drained by the merge record" || bad "drain caps" "$(cat "$PROJ_D2/.wf/CAPABILITIES.yaml")"

# drain report: what was served across the PR's stages, what drained, what stayed
[ -f "$RPT" ] && ok "drain: report written" || bad "drain report" "missing"
[ "$(yget "$RPT" "d['reports'][0]['sprint_id']")" = "sprint-drain" ] \
    && ok "report: names the sprint" || bad "report sprint_id" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['served']")" = "['CAP-3', 'L-2', 'L-5', 'L-8']" ] \
    && ok "report: serves accumulated across every stage the PR carried" || bad "report served" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['merged_tasks']")" = "['S7-T1', 'S8-T1']" ] \
    && ok "report: the merge record spans a deleted stage's tasks too" || bad "report merged" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['learnings_drained']")" = "['L-5']" ] \
    && ok "report: drained learning listed" || bad "report learnings" "$(cat "$RPT")"
[ "$(yget "$RPT" "[e['id'] for e in d['reports'][0]['learnings_retained']]")" = "['L-2', 'L-8']" ] \
    && ok "report: a retained learning is listed" || bad "report retained" "$(cat "$RPT")"
[ "$(yget "$RPT" "'S7-T2' in [e for e in d['reports'][0]['learnings_retained'] if e['id']=='L-2'][0]['reason']")" = "True" ] \
    && ok "report: the retained reason names the unmerged task" || bad "report retained reason" "$(cat "$RPT")"
[ "$(yget "$RPT" "[e for e in d['reports'][0]['learnings_retained'] if e['id']=='L-8'][0]['reason']")" = "no task covered it" ] \
    && ok "report: a served id no task covered says so" || bad "report uncovered" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['superseded_survivors'][0]['id']")" = "SYS-TC-9" ] \
    && ok "report: surviving superseded SYS-TC tag listed" || bad "report survivors" "$(cat "$RPT")"

# archive: the learnings snapshot holds the PRE-drain state
grep -q "L-5" "$PROJ_D2/.wf/archive/sprint-drain/"*__LEARNINGS.yaml 2>/dev/null \
    && ok "archive: learnings snapshot is pre-drain" || bad "archive pre-drain learnings" "$(ls "$PROJ_D2/.wf/archive/sprint-drain" 2>&1)"
ls "$PROJ_D2/.wf/archive/sprint-drain/"*__CAPABILITIES.yaml >/dev/null 2>&1 \
    && ok "archive: capabilities snapshot present" || bad "archive caps snap" "$(ls "$PROJ_D2/.wf/archive/sprint-drain" 2>&1)"
[ -f "$PROJ_D2/.wf/CAPABILITIES.yaml" ] && ok "complete-sprint leaves the capabilities file (snapshot only)" || bad "archive caps kept" "gone"
# an issue still OPEN at the PR boundary is the channel a blocked task re-enters through
[ -f "$PROJ_D2/.wf/transient/design-issues.yaml" ] \
    && ok "complete-sprint keeps the design-issues file — an open issue outlives the PR" || bad "design issues drained" "gone"
ls "$PROJ_D2/.wf/archive/sprint-drain/"*__design-issues.yaml >/dev/null 2>&1 \
    && ok "archive: the design issues are snapshotted" || bad "archive DI snap" "$(ls "$PROJ_D2/.wf/archive/sprint-drain" 2>&1)"

# the emitted summary carries the drain
[ "$(jget "$CD" "d['drain']['learnings_drained']")" = "['L-5']" ] \
    && ok "emit: drain summary in the command output" || bad "emit drain" "$CD"

# no-archive project: the drain still runs (draining is not archiving)
PROJ_D3="$(mktemp -d)"; mkdir -p "$PROJ_D3/.wf/transient"
cat > "$PROJ_D3/.wf/config.yaml" <<'YAML'
version: 1
paths:
  stage: ".wf/transient/stage.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
YAML
printf 'stage: 1\nserves: [L-1]\ntasks:\n- {id: S1-T1, covers: [L-1]}\n' > "$PROJ_D3/.wf/transient/stage.yaml"
printf 'version: 1\nlearnings:\n- id: L-1\n  observation: x\n' > "$PROJ_D3/.wf/LEARNINGS.yaml"
"$PYTHON" "$WF" pipeline load-stage --config "$PROJ_D3/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline complete-task S1-T1 --commit a --merge m --config "$PROJ_D3/.wf/config.yaml" >/dev/null
"$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D3/.wf/config.yaml" --format json >/dev/null
grep -q "L-1" "$PROJ_D3/.wf/LEARNINGS.yaml" && bad "no-archive drain" "L-1 survived" \
    || ok "drain: the learnings drain runs without an archive configured"
[ -f "$PROJ_D3/.wf/transient/drain-report.yaml" ] && ok "drain: report written without an archive" || bad "no-archive report" "missing"

# archive-history spills the overflow past the cap
PROJ_H="$(echo "$STAGE" | new_proj)"
"$PYTHON" - "$PROJ_H/.wf/transient/pipeline-state.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]
d={"current_phase":"stage_run","history":[{"ts":"t","event":f"e{i}"} for i in range(10)]}
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_H" pipeline archive-history --cap 3 >/dev/null
HT="$(wf "$PROJ_H" pipeline history-tail 100 --format json)"
# live history kept at cap (3) + the archival event appended = 4
[ "$(jget "$HT" "len(d)")" = "4" ] && ok "archive-history keeps cap + the archival marker live" || bad "archive-history live" "$HT"
[ -f "$PROJ_H/.wf/transient/pipeline-history.yaml" ] && ok "archive-history writes the spill file" || bad "archive-history spill" "missing"

# ── drain-capability: the adequacy verdict is read from disk, never from prose ──

mk_cap_proj() {  # → project dir with CAP-3 open and a drill-cache
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient/drill-cache"
    cat > "$p/.wf/config.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  drill_cache: ".wf/transient/drill-cache"
  archive: ".wf/archive"
YAML
    cat > "$p/.wf/CAPABILITIES.yaml" <<'YAML'
# CAPABILITIES — the durable why (committed). This comment must survive.
version: 1
capabilities:
- id: CAP-3
  statement: "Operators can reject bad X — with an em dash."
  notes: ""
- id: CAP-4
  statement: "Operators can list zones."
YAML
    echo "$p"
}

P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Date:** now\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json)"; RCD=$?
[ "$RCD" -eq 0 ] && [ "$(jget "$DC" "d['drained']")" = "True" ] \
    && ok "drain-capability: an adequate digest drains the capability" || bad "drain-cap adequate" "rc=$RCD $DC"
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-4']" ] \
    && ok "drain-capability: only the reviewed capability is removed" || bad "drain-cap removal" "$(cat "$P/.wf/CAPABILITIES.yaml")"
grep -q "This comment must survive" "$P/.wf/CAPABILITIES.yaml" \
    && ok "drain-capability: the capabilities file's comments survive (L-106)" || bad "drain-cap comments" "$(cat "$P/.wf/CAPABILITIES.yaml")"
ls "$P/.wf/archive/capabilities/"*__CAPABILITIES.yaml >/dev/null 2>&1 \
    && ok "drain-capability: snapshots the pre-drain file into the archive" || bad "drain-cap archive" "$(ls -R "$P/.wf/archive" 2>&1)"
[ "$(jget "$DC" "'drill-cache' in d['digest']")" = "True" ] \
    && ok "drain-capability: resolves the newest digest from paths.drill_cache" || bad "drain-cap digest" "$DC"

# A digest predating the full-promise/proposed-set naming matches no glob, so the
# capability sits undrained on a verdict that IS on disk. Refusing it is right — an
# unstamped digest cannot be assumed to answer the whole promise — but the refusal has to
# name the file, or the operator has no way to know --verdict is the move.
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json 2>&1)"; RCD=$?
[ "$RCD" -ne 0 ] \
    && ok "drain-capability: an unstamped digest does not drain on its own" || bad "drain-cap unstamped" "rc=$RCD $DC"
case "$DC" in *adequacy-CAP-3-20260731T090000Z.md*)
    ok "drain-capability: the refusal names the unstamped digest it declined" ;;
  *) bad "drain-cap unstamped name" "$DC" ;; esac
case "$DC" in *--verdict*)
    ok "drain-capability: the refusal names the flag that resolves it" ;;
  *) bad "drain-cap unstamped flag" "$DC" ;; esac

# an inadequate verdict mutates nothing and exits 1
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — inadequate\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json)"; RCD=$?
[ "$RCD" -eq 1 ] && [ "$(jget "$DC" "d['drained']")" = "False" ] \
    && ok "drain-capability: an inadequate digest exits 1" || bad "drain-cap inadequate" "rc=$RCD $DC"
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-3', 'CAP-4']" ] \
    && ok "drain-capability: an inadequate verdict mutates nothing" || bad "drain-cap inadequate mutation" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# the newest digest wins — a stale inadequate one does not veto a later adequate one
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — inadequate\n' > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260701T090000Z.md"
printf '# Adequacy: CAP-3 — adequate\n'   > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json)"
[ "$(jget "$DC" "d['drained']")" = "True" ] \
    && ok "drain-capability: the newest digest is the verdict" || bad "drain-cap newest" "$DC"

# only the FULL-PROMISE question drains: a proposed-set digest judges a scenario set at
# authoring time, months before anything ships, so it can never say the promise is proven
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Question:** proposed-set\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-proposed-set-20260731T090000Z.md"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: a proposed-set digest should not drain" "exited 0"
else
    ok "drain-capability: a proposed-set digest alone → non-zero exit"
fi
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-3', 'CAP-4']" ] \
    && ok "drain-capability: a proposed-set digest mutates nothing" || bad "drain-cap proposed mutation" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# a NEWER proposed-set digest never shadows the full-promise verdict
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260701T090000Z.md"
printf '# Adequacy: CAP-3 — inadequate\n**Question:** proposed-set\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-proposed-set-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json)"; RCD=$?
[ "$RCD" -eq 0 ] && [ "$(jget "$DC" "d['drained']")" = "True" ] \
    && ok "drain-capability: a newer proposed-set digest does not shadow the full-promise one" \
    || bad "drain-cap shadow" "rc=$RCD $DC"
[ "$(jget "$DC" "'full-promise' in d['digest']")" = "True" ] \
    && ok "drain-capability: the digest read is the full-promise one" || bad "drain-cap shadow digest" "$DC"

# an explicit --verdict pointing at a proposed-set digest is refused too
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Question:** proposed-set\n' > "$P/claim.md"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --verdict "$P/claim.md" \
        --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: --verdict on a proposed-set digest should fail" "exited 0"
else
    ok "drain-capability: --verdict on a proposed-set digest → non-zero exit"
fi

# a digest reviewing a DIFFERENT capability is a mechanical failure
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-4 — adequate\n' > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: mismatched digest should fail" "exited 0"
else
    ok "drain-capability: a digest reviewing another capability → non-zero exit"
fi

# no digest at all → mechanical failure, never a silent pass
P="$(mk_cap_proj)"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: no digest should fail" "exited 0"
else
    ok "drain-capability: no adequacy digest → non-zero exit"
fi

# an explicit --verdict path wins over the drill-cache scan
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n' > "$P/verdict.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --verdict "$P/verdict.md" --config "$P/.wf/config.yaml" --format json)"
[ "$(jget "$DC" "d['drained']")" = "True" ] && [ "$(jget "$DC" "d['digest']")" = "$P/verdict.md" ] \
    && ok "drain-capability: --verdict names the digest to read" || bad "drain-cap explicit" "$DC"

# a capability that is not open cannot be drained twice
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --verdict "$P/verdict.md" --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: draining a closed capability should fail" "exited 0"
else
    ok "drain-capability: a capability already drained → non-zero exit"
fi

# ── append-residuals: an inadequate verdict feeds the next design ─────────────

mk_digest() {  # <project> <cap> <verdict> <stamp> [extra body]
    local p="$1" cap="$2" verdict="$3" stamp="$4"
    local f="$p/.wf/transient/drill-cache/adequacy-$cap-full-promise-$stamp.md"
    cat > "$f" <<MD
# Adequacy: $cap — $verdict
**Question:** full-promise

## Falsifying paths → coverage

- backend/zones.go:88 → SYS-TC-1 — covered
- backend/bulk.go:12 → RESIDUAL: bulk rejection never observed · a scenario patching two zones
- backend/import.go:4 → RESIDUAL: imported rows skip the rule · a scenario importing a bad row

## Prune-worthy scenarios
- none.
MD
    echo "$f"
}

P="$(mk_cap_proj)"
DG="$(mk_digest "$P" CAP-3 inadequate 20260731T090000Z)"
AR="$("$PYTHON" "$WF" pipeline append-residuals CAP-3 --digest "$DG" --config "$P/.wf/config.yaml" --format json)"; RCA=$?
[ "$RCA" -eq 0 ] && [ "$(jget "$AR" "d['appended']")" = "True" ] \
    && ok "append-residuals: an inadequate digest appends to the capability" || bad "append-residuals" "rc=$RCA $AR"
[ "$(jget "$AR" "d['residuals']")" = "2" ] \
    && ok "append-residuals: counts the residual lines it carried over" || bad "append-residuals count" "$AR"
grep -q "\[adequacy 20260731T090000Z\]" "$P/.wf/CAPABILITIES.yaml" \
    && ok "append-residuals: the appended block is stamped with the digest" || bad "append-residuals stamp" "$(cat "$P/.wf/CAPABILITIES.yaml")"
grep -q "bulk rejection never observed" "$P/.wf/CAPABILITIES.yaml" \
    && ok "append-residuals: the residual text lands in the capability" || bad "append-residuals text" "$(cat "$P/.wf/CAPABILITIES.yaml")"
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "'import.go' in d['capabilities'][0]['notes']")" = "True" ] \
    && ok "append-residuals: the notes field still parses as YAML" || bad "append-residuals yaml" "$(cat "$P/.wf/CAPABILITIES.yaml")"
grep -q "This comment must survive" "$P/.wf/CAPABILITIES.yaml" \
    && ok "append-residuals: the file's comments survive (L-106)" || bad "append-residuals comments" "$(cat "$P/.wf/CAPABILITIES.yaml")"
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "'notes' in d['capabilities'][1]")" = "False" ] \
    && ok "append-residuals: no other capability is touched" || bad "append-residuals blast radius" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# idempotent per digest: the same review never lands twice
BEFORE="$(cat "$P/.wf/CAPABILITIES.yaml")"
AR2="$("$PYTHON" "$WF" pipeline append-residuals CAP-3 --digest "$DG" --config "$P/.wf/config.yaml" --format json)"
[ "$(jget "$AR2" "d['appended']")" = "False" ] \
    && ok "append-residuals: re-running the same digest appends nothing" || bad "append-residuals idempotent" "$AR2"
[ "$BEFORE" = "$(cat "$P/.wf/CAPABILITIES.yaml")" ] \
    && ok "append-residuals: an idempotent re-run does not rewrite the file (L-106)" || bad "append-residuals rewrite" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# a second, later review appends alongside the first
DG2="$(mk_digest "$P" CAP-3 inadequate 20260801T090000Z)"
"$PYTHON" "$WF" pipeline append-residuals CAP-3 --digest "$DG2" --config "$P/.wf/config.yaml" >/dev/null
[ "$(grep -c "\[adequacy " "$P/.wf/CAPABILITIES.yaml")" = "2" ] \
    && ok "append-residuals: a later review appends alongside the earlier one" || bad "append-residuals second" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# a capability with no notes: field at all gets one
P="$(mk_cap_proj)"
DG="$(mk_digest "$P" CAP-4 inadequate 20260731T090000Z)"
"$PYTHON" "$WF" pipeline append-residuals CAP-4 --digest "$DG" --config "$P/.wf/config.yaml" >/dev/null
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "'bulk.go' in d['capabilities'][1]['notes']")" = "True" ] \
    && ok "append-residuals: a capability with no notes field gets one" || bad "append-residuals new notes" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# an existing notes block keeps its own indentation and its sibling fields
P="$(mk_cap_proj)"
cat > "$P/.wf/CAPABILITIES.yaml" <<'YAML'
version: 1
capabilities:
- id: CAP-3
  notes: |
      an existing block, indented its own way
  statement: "Operators can reject bad X."
- id: CAP-4
  statement: "Operators can list zones."
YAML
DG="$(mk_digest "$P" CAP-3 inadequate 20260731T090000Z)"
"$PYTHON" "$WF" pipeline append-residuals CAP-3 --digest "$DG" --config "$P/.wf/config.yaml" >/dev/null
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "d['capabilities'][0]['statement']")" = "Operators can reject bad X." ] \
    && ok "append-residuals: a field below the notes block survives" || bad "append-residuals sibling" "$(cat "$P/.wf/CAPABILITIES.yaml")"
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "d['capabilities'][0]['notes'].splitlines()[0]")" = "an existing block, indented its own way" ] \
    && ok "append-residuals: the existing note text is unchanged" || bad "append-residuals existing note" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# an adequate digest carries no residuals — nothing is appended
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Question:** full-promise\n\n## Falsifying paths → coverage\n\n- backend/zones.go:88 → SYS-TC-1 — covered\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md"
AR="$("$PYTHON" "$WF" pipeline append-residuals CAP-3 --digest "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260731T090000Z.md" --config "$P/.wf/config.yaml" --format json)"
[ "$(jget "$AR" "d['appended']")" = "False" ] \
    && ok "append-residuals: a digest with no residuals appends nothing" || bad "append-residuals adequate" "$AR"

# the digest must review the capability it is being appended to
P="$(mk_cap_proj)"
DG="$(mk_digest "$P" CAP-3 inadequate 20260731T090000Z)"
if "$PYTHON" "$WF" pipeline append-residuals CAP-4 --digest "$DG" --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "append-residuals: a mismatched digest should fail" "exited 0"
else
    ok "append-residuals: a digest reviewing another capability → non-zero exit"
fi

# a capability that is not open cannot take residuals
if "$PYTHON" "$WF" pipeline append-residuals CAP-9 --digest "$DG" --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "append-residuals: an unknown capability should fail" "exited 0"
else
    ok "append-residuals: a capability not in the file → non-zero exit"
fi

# with no --digest it reads the newest full-promise digest, like drain-capability
P="$(mk_cap_proj)"
mk_digest "$P" CAP-3 inadequate 20260701T090000Z >/dev/null
mk_digest "$P" CAP-3 inadequate 20260731T090000Z >/dev/null
AR="$("$PYTHON" "$WF" pipeline append-residuals CAP-3 --config "$P/.wf/config.yaml" --format json)"
[ "$(jget "$AR" "'20260731T090000Z' in d['digest']")" = "True" ] \
    && ok "append-residuals: falls back to the newest full-promise digest" || bad "append-residuals newest" "$AR"

# ── module structure ──
#
# pipeline and drain must not reach into each other. The cycle they used to form
# (pipeline imports drain at module scope; drain imports pipeline back from inside six
# functions) forced every shared helper to be read through a private name across the
# seam, so a rename in either file broke the other silently.
HITS="$(grep -n 'import pipeline\|pipeline\._' "$CLI/drain.py" || true)"
[ -z "$HITS" ] && ok "drain reaches into no pipeline internals" \
    || bad "drain reaches into no pipeline internals" "$HITS"

HITS="$(grep -n 'import drain\|drain\._' "$CLI/pipeline.py" || true)"
[ -z "$HITS" ] && ok "pipeline reaches into no drain internals" \
    || bad "pipeline reaches into no drain internals" "$HITS"

# Both must import cleanly on their own — a cycle broken by lazy imports still
# deadlocks whichever module is imported first by a third party.
for mod in pipeline drain runstate; do
    OUT="$("$PYTHON" -c "import sys; sys.path.insert(0, sys.argv[1]); import $mod" \
        "$CLI" 2>&1)"
    [ -z "$OUT" ] && ok "$mod imports standalone" || bad "$mod imports standalone" "$OUT"
done

# ── summary ──
echo ""
echo "  pipeline brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
