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

# ── summary ──
echo ""
echo "  pipeline brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
