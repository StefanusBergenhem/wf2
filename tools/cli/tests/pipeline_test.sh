#!/usr/bin/env bash
#
# Tests for the wf pipeline brain — increment scoping, sub-layer computation, the
# frontier query, and the close-time drain.
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
  worktree_base: ".wf/transient/worktrees"
driver:
  max_parallel: 4
YAML
    cat > "$proj/.wf/sprint.yaml"
    echo "$proj"
}

wf() { local proj="$1"; shift; "$PYTHON" "$WF" "$@" --config "$proj/.wf/config.yaml"; }

# Seed pipeline_state task_states / stages from a YAML body on stdin.
seed_state() { cat > "$1/.wf/transient/pipeline-state.yaml"; }

# Diamond DAG inside increment 1: sub-layers [[T1,T2],[T3],[T4]]. T5 belongs to
# increment 2 and depends on T4 — a backward cross-increment edge, which is legal.
DIAMOND='tasks:
  - {id: T1, increment: 1, depends_on: []}
  - {id: T2, increment: 1, depends_on: []}
  - {id: T3, increment: 1, depends_on: [T1, T2]}
  - {id: T4, increment: 1, depends_on: [T3]}
  - {id: T5, increment: 2, depends_on: [T4]}'

# ── compute-stages: one increment at a time ──────────────────────────────────

PROJ="$(echo "$DIAMOND" | new_proj)"
OUT="$(wf "$PROJ" pipeline compute-stages --increment 1 --format json)"
[ "$(jget "$OUT" "d['stages']")" = "[['T1', 'T2'], ['T3'], ['T4']]" ] \
    && ok "compute-stages layers increment 1 into 3 sub-layers" \
    || bad "compute-stages layering" "$OUT"
[ "$(jget "$OUT" "d['increment']")" = "1" ] && ok "compute-stages records the increment" || bad "increment" "$OUT"
[ "$(jget "$OUT" "d['total']")" = "3" ] && ok "compute-stages total=3" || bad "total" "$OUT"
[ "$(jget "$OUT" "d['current']")" = "1" ] && ok "compute-stages current=1" || bad "current" "$OUT"
[ "$(jget "$OUT" "d['recomputed']")" = "True" ] && ok "compute-stages recomputed=True" || bad "recomputed" "$OUT"

# increment 2's task never enters increment 1's plan (the TL is dispatched per increment)
[ "$(jget "$OUT" "'T5' in str(d['stages'])")" = "False" ] \
    && ok "compute-stages leaves a later increment's task out of the plan" || bad "cross-increment leak" "$OUT"

# idempotent for the SAME increment
OUT2="$(wf "$PROJ" pipeline compute-stages --increment 1 --format json)"
[ "$(jget "$OUT2" "d['recomputed']")" = "False" ] \
    && ok "compute-stages is idempotent for the same increment" || bad "idempotent" "$OUT2"

# a DIFFERENT increment always recomputes — its dependency in increment 1 is unmerged,
# so the task cannot be placed
OUT3="$(wf "$PROJ" pipeline compute-stages --increment 2 --format json)"
[ "$(jget "$OUT3" "d['recomputed']")" = "True" ] \
    && ok "compute-stages recomputes when a new increment is named" || bad "increment switch" "$OUT3"
[ "$(jget "$OUT3" "d['blocked']")" = "['T5']" ] \
    && ok "compute-stages blocks a task whose earlier-increment dep never merged" || bad "unmerged cross-dep" "$OUT3"

# with the earlier increment merged, increment 2 layers cleanly
PROJ_M2="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_M2" <<'YAML'
task_states:
  T4: {status: completed}
YAML
OUTM2="$(wf "$PROJ_M2" pipeline compute-stages --increment 2 --format json)"
[ "$(jget "$OUTM2" "d['stages']")" = "[['T5']]" ] \
    && ok "compute-stages: a merged earlier-increment dep is satisfied" || bad "merged cross-dep" "$OUTM2"

# an increment no task declares is a mechanical failure
if wf "$PROJ" pipeline compute-stages --increment 9 >/dev/null 2>&1; then
    bad "unknown increment should fail" "exited 0"
else
    ok "compute-stages: an increment no task declares → non-zero exit"
fi

# cycle → non-zero exit, no plan
PROJ_C="$(printf 'tasks:\n  - {id: A, increment: 1, depends_on: [B]}\n  - {id: B, increment: 1, depends_on: [A]}\n' | new_proj)"
if wf "$PROJ_C" pipeline compute-stages --increment 1 --format json >/dev/null 2>&1; then
    bad "cycle should fail" "exited 0"
else
    ok "compute-stages HALTs (non-zero) on a dependency cycle"
fi

# a pre-completed task drops out of the layering and frees its dependents
PROJ_D="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_D" <<'YAML'
current_phase: designing
task_states:
  T1: {status: completed}
YAML
OUTD="$(wf "$PROJ_D" pipeline compute-stages --increment 1 --format json)"
[ "$(jget "$OUTD" "d['stages']")" = "[['T2'], ['T3'], ['T4']]" ] \
    && ok "compute-stages excludes a completed task, deps still satisfied" || bad "completed-exclusion" "$OUTD"

# a blocked task and its dependents are unplaceable → recorded as blocked
PROJ_B="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_B" <<'YAML'
task_states:
  T1: {status: blocked}
YAML
OUTB="$(wf "$PROJ_B" pipeline compute-stages --increment 1 --format json)"
[ "$(jget "$OUTB" "d['stages']")" = "[['T2']]" ] \
    && ok "compute-stages: blocked T1 leaves only T2 placeable" || bad "blocked layering" "$OUTB"
[ "$(jget "$OUTB" "sorted(d['blocked'])")" = "['T3', 'T4']" ] \
    && ok "compute-stages: T3,T4 transitively blocked by T1" || bad "blocked propagation" "$OUTB"

# ── increments (the driver's position in the sprint) ─────────────────────────

PROJ_I="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_I" <<'YAML'
task_states:
  T1: {status: completed}
  T2: {status: completed}
YAML
INC="$(wf "$PROJ_I" pipeline increments --format json)"
[ "$(jget "$INC" "[i['increment'] for i in d['increments']]")" = "[1, 2]" ] \
    && ok "increments lists the declared increments in build order" || bad "increments list" "$INC"
[ "$(jget "$INC" "d['increments'][0]['tasks']")" = "4" ] \
    && ok "increments counts each increment's tasks" || bad "increments count" "$INC"
[ "$(jget "$INC" "d['increments'][0]['completed']")" = "2" ] \
    && ok "increments counts what has merged" || bad "increments merged" "$INC"
[ "$(jget "$INC" "d['next']")" = "1" ] && ok "increments names the next unfinished increment" || bad "increments next" "$INC"
[ "$(jget "$INC" "d['current'] is None")" = "True" ] \
    && ok "increments reports no current increment before compute-stages" || bad "increments current" "$INC"
wf "$PROJ_I" pipeline compute-stages --increment 1 >/dev/null
[ "$(jget "$(wf "$PROJ_I" pipeline increments --format json)" "d['current']")" = "1" ] \
    && ok "increments reports the increment the stage plan belongs to" || bad "increments current set" ""

# every task in an increment merged → done
PROJ_ID="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_ID" <<'YAML'
task_states:
  T1: {status: completed}
  T2: {status: completed}
  T3: {status: completed}
  T4: {status: completed}
YAML
INCD="$(wf "$PROJ_ID" pipeline increments --format json)"
[ "$(jget "$INCD" "d['increments'][0]['done']")" = "True" ] && ok "increments marks a fully merged increment done" || bad "increments done" "$INCD"
[ "$(jget "$INCD" "d['next']")" = "2" ] && ok "increments points at the next increment" || bad "increments next2" "$INCD"

# ── next (frontier) ──────────────────────────────────────────────────────────

PROJ1="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ1" pipeline compute-stages --increment 1 >/dev/null
N1="$(wf "$PROJ1" pipeline next --format json)"
[ "$(jget "$N1" "[e['task_id'] for e in d['dispatch']]")" = "['T1', 'T2']" ] \
    && ok "next: sub-layer 1 dispatches T1,T2" || bad "next dispatch" "$N1"
[ "$(jget "$N1" "d['increment']")" = "1" ] && ok "next: reports the increment" || bad "next increment" "$N1"
[ "$(jget "$N1" "d['stage']['index']")" = "1" ] && ok "next: stage.index=1" || bad "stage index" "$N1"
[ "$(jget "$N1" "d['terminal']['stage_done']")" = "False" ] && ok "next: sub-layer not done with pending work" || bad "stage_done" "$N1"
[ "$(jget "$N1" "d['dispatch'][0]['worktree']")" = ".wf/transient/worktrees/sprint-T1" ] \
    && ok "next: dispatch carries the computed worktree path" || bad "worktree" "$N1"
[ "$(jget "$N1" "sorted(d['dispatch'][0])")" = "['task_id', 'worktree']" ] \
    && ok "next: dispatch entry carries exactly task_id + worktree" \
    || bad "dispatch entry keys" "$N1"

# driver.max_parallel is the concurrency cap
PROJ_CAP="$(echo "$DIAMOND" | new_proj)"
"$PYTHON" - "$PROJ_CAP/.wf/config.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]; d=yaml.safe_load(open(p)); d['driver']['max_parallel']=1
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_CAP" pipeline compute-stages --increment 1 >/dev/null
NCAP="$(wf "$PROJ_CAP" pipeline next --format json)"
[ "$(jget "$NCAP" "[e['task_id'] for e in d['dispatch']]")" = "['T1']" ] \
    && ok "next: driver.max_parallel=1 dispatches only T1" || bad "cap dispatch" "$NCAP"
[ "$(jget "$NCAP" "d['ready']")" = "['T2']" ] && ok "next: cap=1 leaves T2 ready" || bad "cap ready" "$NCAP"

# an in-flight task occupies a slot and is not re-dispatched
PROJ_IF="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_IF" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
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

# sub-layer settled, increment not done (more sub-layers)
PROJ_SD="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_SD" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: completed}
  T2: {status: completed}
YAML
NSD="$(wf "$PROJ_SD" pipeline next --format json)"
[ "$(jget "$NSD" "d['terminal']['stage_done']")" = "True" ] && ok "next: stage_done when the sub-layer's tasks completed" || bad "stage_done true" "$NSD"
[ "$(jget "$NSD" "d['terminal']['increment_done']")" = "False" ] && ok "next: increment not done mid-increment" || bad "increment_done false" "$NSD"
[ "$(jget "$NSD" "d['terminal']['sprint_done']")" = "False" ] && ok "next: sprint not done mid-increment" || bad "sprint_done false" "$NSD"

# a parked design-issue task does NOT hold the sub-layer open
PROJ_DI="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_DI" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: completed}
  T2: {status: design_issue}
YAML
NDI="$(wf "$PROJ_DI" pipeline next --format json)"
[ "$(jget "$NDI" "d['repairing']")" = "['T2']" ] && ok "next: design_issue task surfaces as repairing" || bad "repairing" "$NDI"
[ "$(jget "$NDI" "d['terminal']['stage_done']")" = "True" ] && ok "next: parked DI does not hold the sub-layer open" || bad "parked stage_done" "$NDI"

# an approved task (awaiting batch merge) is settled — surfaced, not re-dispatched
PROJ_APN="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_APN" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
task_states:
  T1: {status: approved}
  T2: {status: completed}
YAML
NAPN="$(wf "$PROJ_APN" pipeline next --format json)"
[ "$(jget "$NAPN" "d['approved']")" = "['T1']" ] && ok "next: approved task surfaces under approved[]" || bad "approved list" "$NAPN"
[ "$(jget "$NAPN" "[e['task_id'] for e in d['dispatch']]")" = "[]" ] && ok "next: approved task is not re-dispatched" || bad "approved dispatch" "$NAPN"
[ "$(jget "$NAPN" "d['terminal']['stage_done']")" = "True" ] && ok "next: approved task does not hold the sub-layer open" || bad "approved stage_done" "$NAPN"

# last sub-layer of increment 1 complete → increment_done, but a later increment is
# declared, so the sprint is NOT done
PROJ_FIN="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_FIN" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 3, total: 3}
task_states:
  T4: {status: completed}
YAML
NFIN="$(wf "$PROJ_FIN" pipeline next --format json)"
[ "$(jget "$NFIN" "d['terminal']['increment_done']")" = "True" ] && ok "next: increment_done on the last sub-layer" || bad "increment_done true" "$NFIN"
[ "$(jget "$NFIN" "d['terminal']['sprint_done']")" = "False" ] \
    && ok "next: sprint not done while a later increment is declared" || bad "sprint_done later" "$NFIN"

# the last increment's last sub-layer → sprint_done
PROJ_LAST="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_LAST" <<'YAML'
stages: {increment: 2, definitions: [[T5]], current: 1, total: 1}
task_states:
  T5: {status: completed}
YAML
NLAST="$(wf "$PROJ_LAST" pipeline next --format json)"
[ "$(jget "$NLAST" "d['terminal']['sprint_done']")" = "True" ] && ok "next: sprint_done after the last increment" || bad "sprint_done true" "$NLAST"

# next before compute-stages → halt
PROJ_NC="$(echo "$DIAMOND" | new_proj)"
NNC="$(wf "$PROJ_NC" pipeline next --format json)"
[ "$(jget "$NNC" "d['terminal']['halt'] is not None")" = "True" ] \
    && ok "next: halts when stages not computed" || bad "uncomputed halt" "$NNC"

# ── run-state mutations ──────────────────────────────────────────────────────

PROJ_M="$(echo "$DIAMOND" | new_proj)"

# transition writes phase + history
wf "$PROJ_M" pipeline transition --to designing --reason kickoff >/dev/null
CP="$(wf "$PROJ_M" pipeline current-phase --format json)"
[ "$(jget "$CP" "d['phase']")" = "designing" ] && ok "transition sets current_phase" || bad "transition" "$CP"

# --sprint-id records whose sprint this run state belongs to, so `next` names the
# driver's sprint instead of falling back to the sprint file's id
PROJ_SID="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_SID" pipeline transition --to preparing --sprint-id s7 >/dev/null
[ "$(yget "$PROJ_SID/.wf/transient/pipeline-state.yaml" "d['sprint_id']")" = "s7" ] \
    && ok "transition --sprint-id records the sprint id in the run state" \
    || bad "transition sprint_id" "$(cat "$PROJ_SID/.wf/transient/pipeline-state.yaml")"
wf "$PROJ_SID" pipeline compute-stages --increment 1 >/dev/null
NSID="$(wf "$PROJ_SID" pipeline next --format json)"
[ "$(jget "$NSID" "d['dispatch'][0]['worktree']")" = ".wf/transient/worktrees/s7-T1" ] \
    && ok "next: the recorded sprint id names the worktree" || bad "next sprint_id" "$NSID"

# a later transition without the flag keeps the recorded id
wf "$PROJ_SID" pipeline transition --to running_stage >/dev/null
[ "$(yget "$PROJ_SID/.wf/transient/pipeline-state.yaml" "d['sprint_id']")" = "s7" ] \
    && ok "transition without --sprint-id leaves the recorded id alone" \
    || bad "transition sprint_id kept" "$(cat "$PROJ_SID/.wf/transient/pipeline-state.yaml")"

# current-phase resolves sprint_branch from git when the stored value is null (L-020)
PROJ_GB="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_GB" <<'YAML'
current_phase: increment_loop
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

# approve-task → approved (passed all passes, awaiting the batch merge)
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

# retry-task → building, attempt++ — a build that returned no artifact at all spends an
# attempt and goes back in; nothing about the task is decided yet, so it is not blocked
RTC="$(wf "$PROJ_M" pipeline retry-task T2 --reason "build returned escalate_no_artifacts" --format json)"
[ "$(jget "$RTC" "d.get('ok') is True and d.get('event')=='task_retried'")" = "True" ] \
    && ok "retry-task emits a success confirmation (L-059)" || bad "retry confirm" "$RTC"
TSR="$(wf "$PROJ_M" pipeline task-state T2 --format json)"
[ "$(jget "$TSR" "d['state']")" = "building" ] && ok "retry-task → building" || bad "retry state" "$TSR"
[ "$(jget "$TSR" "d['attempt_counter']")" = "2" ] && ok "retry-task bumps attempt_counter" || bad "retry attempt" "$TSR"
[ "$(jget "$(wf "$PROJ_M" pipeline blocked-tasks --format json)" "any(t['task_id']=='T2' for t in d['tasks'])")" = "False" ] \
    && ok "retry-task does not block the task" || bad "retry blocked" ""

# block-task → blocked
BLC="$(wf "$PROJ_M" pipeline block-task T3 --reason "manual" --format json)"
[ "$(jget "$BLC" "d.get('ok') is True and d.get('event')=='task_blocked' and d.get('status')=='blocked'")" = "True" ] \
    && ok "block-task emits a success confirmation (L-059)" || bad "block confirm" "$BLC"
[ "$(jget "$(wf "$PROJ_M" pipeline task-state T3 --format json)" "d['state']")" = "blocked" ] \
    && ok "block-task → blocked" || bad "block" ""

# ── unblock-task ─────────────────────────────────────────────────────────────
# The whole recovery from a `tasks_blocked` halt, in one verb: the named task goes back
# to pending, every task `propagate-blocks` doomed BECAUSE of it comes with it, an
# independently blocked root is left alone, and the stage pointer rewinds to the
# sub-layer the freed work sits in — `compute-stages` preserves an existing plan, so
# nothing else ever rewinds it, and a run resumed without that lands in the wrong layer.
FANOUT='tasks:
  - {id: T1, increment: 1, depends_on: []}
  - {id: T2, increment: 1, depends_on: []}
  - {id: T3, increment: 1, depends_on: [T1]}
  - {id: T4, increment: 1, depends_on: [T3]}
  - {id: T9, increment: 1, depends_on: [T2]}'
PROJ_UB="$(echo "$FANOUT" | new_proj)"
wf "$PROJ_UB" pipeline compute-stages --increment 1 >/dev/null
wf "$PROJ_UB" pipeline reject-task T1 --feedback /tmp/fb.yaml >/dev/null   # spends attempt 1
wf "$PROJ_UB" pipeline block-task T1 --reason "the build wrote no artifact" >/dev/null
wf "$PROJ_UB" pipeline block-task T2 --reason "review rejected it every time" >/dev/null
wf "$PROJ_UB" pipeline propagate-blocks >/dev/null
# the run walked on to the last sub-layer with everything behind it doomed
"$PYTHON" - "$PROJ_UB/.wf/transient/pipeline-state.yaml" <<'PY'
import sys, yaml
p = sys.argv[1]; d = yaml.safe_load(open(p)); d["stages"]["current"] = 3
open(p, "w").write(yaml.safe_dump(d, sort_keys=False))
PY

UBC="$(wf "$PROJ_UB" pipeline unblock-task T1 --format json)"
[ "$(jget "$UBC" "d.get('ok') is True and d.get('event')=='task_unblocked'")" = "True" ] \
    && ok "unblock-task emits a success confirmation (L-059)" || bad "unblock confirm" "$UBC"
[ "$(jget "$UBC" "sorted(d['freed'])")" = "['T1', 'T3', 'T4']" ] \
    && ok "unblock-task frees the task and everything doomed by it" || bad "unblock cascade" "$UBC"
for T in T1 T3 T4; do
    [ "$(jget "$(wf "$PROJ_UB" pipeline task-state $T --format json)" "d['state']")" = "pending" ] \
        && ok "unblock-task: $T back to pending" || bad "unblock $T state" ""
done
[ "$(jget "$(wf "$PROJ_UB" pipeline task-state T1 --format json)" "d['attempt_counter']")" = "1" ] \
    && ok "unblock-task keeps the attempt counter — the record of what it already cost" \
    || bad "unblock attempt_counter" ""
# an independently blocked root and ITS dependents are none of this task's business
[ "$(jget "$(wf "$PROJ_UB" pipeline blocked-tasks --format json)" "sorted(t['task_id'] for t in d['tasks'])")" = "['T2', 'T9']" ] \
    && ok "unblock-task leaves an independently blocked root alone" || bad "unblock other root" ""
[ "$(jget "$UBC" "d['stage']")" = "1" ] && ok "unblock-task rewinds to the freed work's sub-layer" || bad "unblock stage" "$UBC"
[ "$(yget "$PROJ_UB/.wf/transient/pipeline-state.yaml" "d['stages']['current']")" = "1" ] \
    && ok "unblock-task persists the rewound stage" || bad "unblock stage persisted" ""
[ "$(yget "$PROJ_UB/.wf/transient/pipeline-state.yaml" "any(e.get('event')=='task_unblocked' for e in d['history'])")" = "True" ] \
    && ok "unblock-task appends to the audit trail" || bad "unblock history" ""

UB2="$(wf "$PROJ_UB" pipeline unblock-task T2 --format json)"
[ "$(jget "$UB2" "sorted(d['freed'])")" = "['T2', 'T9']" ] \
    && ok "unblock-task frees the second root's chain" || bad "unblock second root" "$UB2"

# the pointer only ever moves BACK: freeing work in a LATER sub-layer must not drag the
# frontier past sub-layers that still have to run
wf "$PROJ_UB" pipeline block-task T9 --reason "blocked on its own, in sub-layer 2" >/dev/null
UB3="$(wf "$PROJ_UB" pipeline unblock-task T9 --format json)"
[ "$(jget "$UB3" "d['stage']")" = "1" ] \
    && ok "unblock-task never moves the stage pointer forward" || bad "unblock forward" "$UB3"
[ "$(yget "$PROJ_UB/.wf/transient/pipeline-state.yaml" "d['stages']['current']")" = "1" ] \
    && ok "unblock-task persists the unmoved pointer" || bad "unblock forward persisted" ""

# a task that is not blocked is a typo, not a no-op
if wf "$PROJ_UB" pipeline unblock-task T1 --format json >/dev/null 2>&1; then
    bad "unblock unblocked" "exited zero on a task that is not blocked"
else
    ok "unblock-task refuses a task that is not blocked"
fi

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
# resolve-design-issue un-parks the implicated task (design_issue → pending)
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

# A boundary DI is task-less: record-design-issue with no --task records the DI and
# parks NO phantom task.
wf "$PROJ_M" pipeline record-design-issue DI-STAGE --severity medium --fix_kind spec_amendment >/dev/null
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

# ── sub-layer mutations ──────────────────────────────────────────────────────

# advance-stage walks current; no-op on the last sub-layer
PROJ_A="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_A" pipeline compute-stages --increment 1 >/dev/null
A1="$(wf "$PROJ_A" pipeline advance-stage --format json)"
[ "$(jget "$A1" "d['current']")" = "2" ] && ok "advance-stage → sub-layer 2" || bad "advance 2" "$A1"
wf "$PROJ_A" pipeline advance-stage >/dev/null   # → 3 (last)
A3="$(wf "$PROJ_A" pipeline advance-stage --format json)"
[ "$(jget "$A3" "d['advanced']")" = "False" ] && ok "advance-stage is a no-op past the last sub-layer" || bad "advance last" "$A3"

# propagate-blocks: an escalated dep dooms its dependents, across increments too
PROJ_P="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_P" <<'YAML'
task_states:
  T1: {status: escalated}
YAML
PB="$(wf "$PROJ_P" pipeline propagate-blocks --format json)"
[ "$(jget "$PB" "sorted(d['blocked'])")" = "['T3', 'T4', 'T5']" ] \
    && ok "propagate-blocks dooms dependents behind escalated T1, across increments" || bad "propagate" "$PB"

# stage timing
PROJ_TM="$(echo "$DIAMOND" | new_proj)"
SS="$(wf "$PROJ_TM" pipeline stage-start --stage 1 --format json)"
[ -n "$(jget "$SS" "d['started_at']")" ] && ok "stage-start records started_at" || bad "stage-start" "$SS"
SE="$(wf "$PROJ_TM" pipeline stage-end --stage 1 --format json)"
[ "$(jget "$SE" "'duration_seconds' in d['timing']")" = "True" ] && ok "stage-end records duration_seconds" || bad "stage-end" "$SE"

# Every increment re-uses the sub-layer numbers 1..N, and stage-start deliberately
# preserves an existing started_at so a resumed stage keeps its origin. Together those
# made the NEXT increment's stage 1 inherit the PREVIOUS increment's start: a fresh
# completed_at measured against a day-old start, reported as a 28-hour sub-layer.
PROJ_XI="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_XI" pipeline compute-stages --increment 1 >/dev/null
seed_timing() { "$PYTHON" - "$1" <<'PY'
import sys, yaml, pathlib
p = pathlib.Path(sys.argv[1]) / ".wf/transient/pipeline-state.yaml"
doc = yaml.safe_load(p.read_text()) or {}
doc.setdefault("stage_summaries", {})[1] = {
    "timing": {"started_at": "2020-01-01T00:00:00Z"}, "tasks": ["T1", "T2"]}
p.write_text(yaml.safe_dump(doc))
PY
}
seed_timing "$PROJ_XI"
wf "$PROJ_XI" pipeline compute-stages --increment 2 >/dev/null
XS="$(wf "$PROJ_XI" pipeline stage-start --stage 1 --format json)"
[ "$(jget "$XS" "d['started_at'].startswith('2020')")" = "False" ] \
    && ok "stage-start: a new increment's sub-layer 1 does not inherit the last one's start" \
    || bad "cross-increment stage start" "$XS"

# ...but re-layering the SAME increment (a design-issue repair) is a continuation, so
# an in-flight sub-layer keeps the origin stage-start's idempotence exists to protect.
PROJ_SI="$(echo "$DIAMOND" | new_proj)"
wf "$PROJ_SI" pipeline compute-stages --increment 1 >/dev/null
seed_timing "$PROJ_SI"
wf "$PROJ_SI" pipeline compute-stages --increment 1 --force >/dev/null
SI="$(wf "$PROJ_SI" pipeline stage-start --stage 1 --format json)"
[ "$(jget "$SI" "d['started_at']")" = "2020-01-01T00:00:00Z" ] \
    && ok "stage-start: re-layering the same increment keeps the sub-layer's origin" \
    || bad "same-increment stage start" "$SI"

# stage-summary derives lists from task_states
PROJ_SS="$(echo "$DIAMOND" | new_proj)"
seed_state "$PROJ_SS" <<'YAML'
stages: {increment: 1, definitions: [[T1, T2], [T3], [T4]], current: 1, total: 3}
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
wf "$PROJ_CS" pipeline transition --to increment_loop >/dev/null
wf "$PROJ_CS" pipeline complete-sprint >/dev/null
[ "$(jget "$(wf "$PROJ_CS" pipeline current-phase --format json)" "d['phase']")" = "idle" ] \
    && ok "complete-sprint resets phase to idle" || bad "complete-sprint phase" ""
[ ! -f "$PROJ_CS/.wf/sprint.yaml" ] && ok "complete-sprint clears the sprint slot" || bad "complete-sprint sprint" "still present"

# complete-sprint with paths.archive set: drains sprint+slice into the archive
PROJ_A2="$(mktemp -d)"; mkdir -p "$PROJ_A2/.wf/transient"
cat > "$PROJ_A2/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_slice: ".wf/transient/design-slice.md"
  capabilities: ".wf/CAPABILITIES.yaml"
  design_issues: ".wf/transient/design-issues.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
YAML
printf 'sprint_id: sprint-xyz\ntasks: []\n' > "$PROJ_A2/.wf/transient/sprint.yaml"
printf '# slice body\n'                     > "$PROJ_A2/.wf/transient/design-slice.md"
printf 'version: 1\ncapabilities: []\n'     > "$PROJ_A2/.wf/CAPABILITIES.yaml"
printf 'issues:\n- id: DI-T1\n  status: resolved\n' > "$PROJ_A2/.wf/transient/design-issues.yaml"
printf 'current_phase: closeout\n'          > "$PROJ_A2/.wf/transient/pipeline-state.yaml"
CA="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_A2/.wf/config.yaml" --format json)"
[ "$(jget "$CA" "d['sprint_id']")" = "sprint-xyz" ] && ok "complete-sprint(archive): reports sprint_id" || bad "archive sprint_id" "$CA"
[ ! -f "$PROJ_A2/.wf/transient/sprint.yaml" ] && ok "complete-sprint(archive): drains the sprint" || bad "archive drain sprint" "still present"
[ ! -f "$PROJ_A2/.wf/transient/design-slice.md" ] && ok "complete-sprint(archive): drains the slice" || bad "archive drain slice" "still present"
[ ! -f "$PROJ_A2/.wf/transient/design-issues.yaml" ] && ok "complete-sprint(archive): drains the design-issues file" || bad "archive drain design_issues" "still present"
[ -f "$PROJ_A2/.wf/CAPABILITIES.yaml" ] && ok "complete-sprint(archive): leaves the capabilities file (snapshot only)" || bad "archive caps kept" "gone"
ls "$PROJ_A2/.wf/archive/sprint-xyz/"*__sprint.yaml >/dev/null 2>&1 && ok "complete-sprint(archive): sprint snapshot under <archive>/<sprint_id>/" || bad "archive sprint snap" "$(ls -R "$PROJ_A2/.wf/archive" 2>&1)"
ls "$PROJ_A2/.wf/archive/sprint-xyz/"*__design-slice.md >/dev/null 2>&1 && ok "complete-sprint(archive): slice snapshot present" || bad "archive slice snap" "$(ls "$PROJ_A2/.wf/archive/sprint-xyz" 2>&1)"
ls "$PROJ_A2/.wf/archive/sprint-xyz/"*__CAPABILITIES.yaml >/dev/null 2>&1 && ok "complete-sprint(archive): capabilities snapshot present" || bad "archive caps snap" "$(ls "$PROJ_A2/.wf/archive/sprint-xyz" 2>&1)"
[ "$(jget "$("$PYTHON" "$WF" pipeline current-phase --config "$PROJ_A2/.wf/config.yaml" --format json)" "d['phase']")" = "idle" ] && ok "complete-sprint(archive): resets phase to idle" || bad "archive phase idle" "$CA"

# archive-history spills the overflow past the cap
PROJ_H="$(echo "$DIAMOND" | new_proj)"
"$PYTHON" - "$PROJ_H/.wf/transient/pipeline-state.yaml" <<'PY'
import sys,yaml
p=sys.argv[1]
d={"current_phase":"increment_loop","history":[{"ts":"t","event":f"e{i}"} for i in range(10)]}
open(p,'w').write(yaml.safe_dump(d))
PY
wf "$PROJ_H" pipeline archive-history --cap 3 >/dev/null
HT="$(wf "$PROJ_H" pipeline history-tail 100 --format json)"
# live history kept at cap (3) + the archival event appended = 4
[ "$(jget "$HT" "len(d)")" = "4" ] && ok "archive-history keeps cap + the archival marker live" || bad "archive-history live" "$HT"
[ -f "$PROJ_H/.wf/transient/pipeline-history.yaml" ] && ok "archive-history writes the spill file" || bad "archive-history spill" "missing"

# ── complete-sprint: the close-time drain ────────────────────────────────────
# The slice's Serves header says what the sprint set out to serve; the merge record
# says what shipped. A learning drains when every task covering it merged.

PROJ_D2="$(mktemp -d)"; mkdir -p "$PROJ_D2/.wf/transient" "$PROJ_D2/tests"
cat > "$PROJ_D2/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_slice: ".wf/transient/design-slice.md"
  learnings: ".wf/LEARNINGS.yaml"
  capabilities: ".wf/CAPABILITIES.yaml"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  archive: ".wf/archive"
  tests: ["tests"]
YAML
cat > "$PROJ_D2/.wf/transient/sprint.yaml" <<'YAML'
sprint_id: sprint-drain
tasks:
- id: T1
  increment: 1
  covers: [CAP-3]
- id: T2
  increment: 1
  covers: [CAP-3]
  system_tests:
  - id: SYS-TC-1
    description: bad X rejected end-to-end
    covers: [CAP-3]
- id: T3
  increment: 2
  covers: [L-2]
- id: T4
  increment: 2
  covers: [L-5]
YAML
cat > "$PROJ_D2/.wf/transient/pipeline-state.yaml" <<'YAML'
current_phase: closeout
task_states:
  T1: {status: completed}
  T2: {status: completed}
  T3: {status: escalated}
  T4: {status: completed}
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
cat > "$PROJ_D2/.wf/transient/design-slice.md" <<'MD'
# slice

**Serves:** CAP-3, L-2, L-5

## Supersessions

- the old rejection path — proven by `tests/old_test.go` (SYS-TC-9) · human-ruled ·
  successor: increment 1
MD
printf '// [SYS-TC:SYS-TC-9] old scenario\nfunc TestOld(t *testing.T) {}\n' > "$PROJ_D2/tests/old_test.go"

CD="$("$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D2/.wf/config.yaml" --format json)"
LRN="$PROJ_D2/.wf/LEARNINGS.yaml"
RPT="$PROJ_D2/.wf/transient/drain-report.yaml"

# L-5's only task merged → drains. L-2's task escalated → stays. L-9 was not served.
[ "$(yget "$LRN" "[e['id'] for e in d['learnings']]")" = "['L-2', 'L-9']" ] \
    && ok "drain: L-5 drained (its task merged), L-2 and L-9 kept" || bad "drain learnings" "$(cat "$LRN")"
grep -q "header comment MUST survive" "$LRN" \
    && ok "drain: the learnings file's comments survive the rewrite (L-106)" || bad "drain comments" "$(cat "$LRN")"
grep -q "batching — keep the — dash" "$LRN" \
    && ok "drain: unicode survives the rewrite (L-106)" || bad "drain unicode" "$(cat "$LRN")"

# capabilities never drain on the merge record — only an adequacy verdict drains one
[ "$(yget "$PROJ_D2/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-3']" ] \
    && ok "drain: a served capability is NOT drained by the merge record" || bad "drain caps" "$(cat "$PROJ_D2/.wf/CAPABILITIES.yaml")"

# drain report: what was served, what drained, what the adequacy gate must review
[ -f "$RPT" ] && ok "drain: report written" || bad "drain report" "missing"
[ "$(yget "$RPT" "d['reports'][0]['sprint_id']")" = "sprint-drain" ] \
    && ok "report: names the sprint" || bad "report sprint_id" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['served']")" = "['CAP-3', 'L-2', 'L-5']" ] \
    && ok "report: carries the slice's serves header" || bad "report served" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['merged_tasks']")" = "['T1', 'T2', 'T4']" ] \
    && ok "report: the merge record spans every increment's tasks" || bad "report merged" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['learnings_drained']")" = "['L-5']" ] \
    && ok "report: drained learning listed" || bad "report learnings" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['learnings_retained'][0]['id']")" = "L-2" ] \
    && ok "report: a retained learning says why" || bad "report retained" "$(cat "$RPT")"
[ "$(yget "$RPT" "'T3' in d['reports'][0]['learnings_retained'][0]['reason']")" = "True" ] \
    && ok "report: the retained reason names the unmerged task" || bad "report retained reason" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['adequacy_candidates'][0]['capability']")" = "CAP-3" ] \
    && ok "report: CAP-3 is an adequacy candidate" || bad "report candidate" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['adequacy_candidates'][0]['shipped_scenarios'][0]['id']")" = "SYS-TC-1" ] \
    && ok "report: candidate carries its shipped scenario" || bad "report scenario" "$(cat "$RPT")"
[ "$(yget "$RPT" "d['reports'][0]['superseded_survivors'][0]['id']")" = "SYS-TC-9" ] \
    && ok "report: surviving superseded SYS-TC tag listed" || bad "report survivors" "$(cat "$RPT")"

# archive: the learnings snapshot holds the PRE-drain state
grep -q "L-5" "$PROJ_D2/.wf/archive/sprint-drain/"*__LEARNINGS.yaml 2>/dev/null \
    && ok "archive: learnings snapshot is pre-drain" || bad "archive pre-drain learnings" "$(ls "$PROJ_D2/.wf/archive/sprint-drain" 2>&1)"

# the emitted summary carries the drain
[ "$(jget "$CD" "d['drain']['learnings_drained']")" = "['L-5']" ] \
    && ok "emit: drain summary in the command output" || bad "emit drain" "$CD"

# no-archive project: the drain still runs (draining is not archiving)
PROJ_D3="$(mktemp -d)"; mkdir -p "$PROJ_D3/.wf/transient"
cat > "$PROJ_D3/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/transient/sprint.yaml"
  design_slice: ".wf/transient/design-slice.md"
  learnings: ".wf/LEARNINGS.yaml"
  drain_report: ".wf/transient/drain-report.yaml"
  pipeline_state: ".wf/transient/pipeline-state.yaml"
YAML
printf 'sprint_id: s2\ntasks:\n- id: T1\n  increment: 1\n  covers: [L-1]\n' > "$PROJ_D3/.wf/transient/sprint.yaml"
printf 'task_states:\n  T1: {status: completed}\n' > "$PROJ_D3/.wf/transient/pipeline-state.yaml"
printf '# slice\n\n**Serves:** L-1\n' > "$PROJ_D3/.wf/transient/design-slice.md"
printf 'version: 1\nlearnings:\n- id: L-1\n  observation: x\n' > "$PROJ_D3/.wf/LEARNINGS.yaml"
"$PYTHON" "$WF" pipeline complete-sprint --config "$PROJ_D3/.wf/config.yaml" --format json >/dev/null
grep -q "L-1" "$PROJ_D3/.wf/LEARNINGS.yaml" && bad "no-archive drain" "L-1 survived" \
    || ok "drain: the learnings drain runs without an archive configured"
[ -f "$PROJ_D3/.wf/transient/drain-report.yaml" ] && ok "drain: report written without an archive" || bad "no-archive report" "missing"

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

# A digest predating the full-promise/iteration-claim naming matches no glob, so the
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

# only the FULL-PROMISE question drains: an iteration-claim digest judges one slice's
# claim, so it can never say the whole promise is covered
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Question:** iteration claim\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-iteration-claim-20260731T090000Z.md"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: an iteration-claim digest should not drain" "exited 0"
else
    ok "drain-capability: an iteration-claim digest alone → non-zero exit"
fi
[ "$(yget "$P/.wf/CAPABILITIES.yaml" "[e['id'] for e in d['capabilities']]")" = "['CAP-3', 'CAP-4']" ] \
    && ok "drain-capability: an iteration-claim digest mutates nothing" || bad "drain-cap iteration mutation" "$(cat "$P/.wf/CAPABILITIES.yaml")"

# a NEWER iteration-claim digest never shadows the full-promise verdict
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-full-promise-20260701T090000Z.md"
printf '# Adequacy: CAP-3 — inadequate\n**Question:** iteration claim\n' \
    > "$P/.wf/transient/drill-cache/adequacy-CAP-3-iteration-claim-20260731T090000Z.md"
DC="$("$PYTHON" "$WF" pipeline drain-capability CAP-3 --config "$P/.wf/config.yaml" --format json)"; RCD=$?
[ "$RCD" -eq 0 ] && [ "$(jget "$DC" "d['drained']")" = "True" ] \
    && ok "drain-capability: a newer iteration-claim digest does not shadow the full-promise one" \
    || bad "drain-cap shadow" "rc=$RCD $DC"
[ "$(jget "$DC" "'full-promise' in d['digest']")" = "True" ] \
    && ok "drain-capability: the digest read is the full-promise one" || bad "drain-cap shadow digest" "$DC"

# an explicit --verdict pointing at an iteration-claim digest is refused too
P="$(mk_cap_proj)"
printf '# Adequacy: CAP-3 — adequate\n**Question:** iteration claim\n' > "$P/claim.md"
if "$PYTHON" "$WF" pipeline drain-capability CAP-3 --verdict "$P/claim.md" \
        --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "drain-capability: --verdict on an iteration-claim digest should fail" "exited 0"
else
    ok "drain-capability: --verdict on an iteration-claim digest → non-zero exit"
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

# ── summary ──
echo ""
echo "  pipeline brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
