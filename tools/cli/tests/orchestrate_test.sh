#!/usr/bin/env bash
#
# Tests for the wf orchestrate helpers — the judgment-free return-inspection /
# routing verdicts. Run: bash tools/cli/tests/orchestrate_test.sh (exit 0 = pass).
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
wf() { "$PYTHON" "$WF" "$@"; }

mkrepo() {  # → prints repo dir; a git repo with .wf/transient/
    local r; r="$(mktemp -d)"
    git -C "$r" init -q
    git -C "$r" config user.email wf@test; git -C "$r" config user.name wf
    mkdir -p "$r/.wf/transient"
    echo "$r"
}
gitc() { git -C "$1" -c core.hooksPath=/dev/null commit -q --allow-empty -m "$2"; git -C "$1" rev-parse --short HEAD; }

# ── inspect-build-return ─────────────────────────────────────────────────────
# One result artifact per outcome; verdict from presence, in priority order:
#   design_issue → review_ready → escalate_no_artifacts.

R="$(mkrepo)"

# design issue: an open issue for the task → design_issue + di_id
printf 'issues:\n  - id: DI-1\n    task_id: T1\n    fix_kind: contract_amendment\n    status: open\n' \
    > "$R/.wf/transient/design-issues.yaml"
OUT="$(wf orchestrate inspect-build-return "$R" T1)"
[ "$(jget "$OUT" "d['verdict']")" = "design_issue" ] \
    && ok "inspect-build: open design issue for task → design_issue" || bad "ib di verdict" "$OUT"
[ "$(jget "$OUT" "d['di_id']")" = "DI-1" ] \
    && ok "inspect-build: design_issue carries di_id" || bad "ib di_id" "$OUT"
# an issue for a DIFFERENT task does not fire
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T2)" "d['verdict']")" = "escalate_no_artifacts" ] \
    && ok "inspect-build: design issue for another task → not design_issue" || bad "ib di othertask" ""
# a resolved issue does not fire
printf 'issues:\n  - id: DI-1\n    task_id: T1\n    fix_kind: contract_amendment\n    status: resolved\n' \
    > "$R/.wf/transient/design-issues.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "escalate_no_artifacts" ] \
    && ok "inspect-build: resolved design issue → not design_issue" || bad "ib di resolved" ""
rm "$R/.wf/transient/design-issues.yaml"

: > "$R/.wf/transient/review-ready.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "ready_for_review" ] \
    && ok "inspect-build: review_ready present → ready_for_review" || bad "ib review_ready" ""
rm "$R/.wf/transient/review-ready.yaml"

[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "escalate_no_artifacts" ] \
    && ok "inspect-build: nothing present → escalate_no_artifacts" || bad "ib none" ""

# the build sha travels in the verdict JSON — inspect-review-return takes it as an
# argument, and the only other source is the build agent's prose, which the routing
# protocol forbids reading (L-082)
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['build_commit_sha']")" = "None" ] \
    && ok "inspect-build: no commit yet → build_commit_sha null" || bad "ib sha none" ""
BUILD="$(gitc "$R" "T1 build: done")"
: > "$R/.wf/transient/review-ready.yaml"
OUT="$(wf orchestrate inspect-build-return "$R" T1)"
[ "$(jget "$OUT" "d['build_commit_sha']")" = "$BUILD" ] \
    && ok "inspect-build: emits the build commit sha" || bad "ib sha" "$OUT — expected $BUILD"
# preserve-uncommitted runs FIRST in the protocol; its commit is not the build's
gitc "$R" "chore(T1): preserve uncommitted files from a prior halted dispatch" > /dev/null
OUT="$(wf orchestrate inspect-build-return "$R" T1)"
[ "$(jget "$OUT" "d['build_commit_sha']")" = "$BUILD" ] \
    && ok "inspect-build: peels a preserve commit off the build sha" || bad "ib sha preserve" "$OUT — expected $BUILD"
rm "$R/.wf/transient/review-ready.yaml"

# ── inspect-review-return ────────────────────────────────────────────────────

R="$(mkrepo)"
BUILD="$(gitc "$R" "T1 build: done")"
# feedback present → rejected
: > "$R/.wf/transient/feedback.yaml"
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "rejected" ] \
    && ok "inspect-review: feedback present → rejected" || bad "ir rejected" ""
rm "$R/.wf/transient/feedback.yaml"
# open design issue for the task → design_issue + di_id (review's own DI, routed at its boundary)
printf 'issues:\n  - id: DI-9\n    task_id: T1\n    fix_kind: contract_amendment\n    status: open\n' \
    > "$R/.wf/transient/design-issues.yaml"
ROUT="$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")"
[ "$(jget "$ROUT" "d['verdict']")" = "design_issue" ] && [ "$(jget "$ROUT" "d['di_id']")" = "DI-9" ] \
    && ok "inspect-review: open design issue → design_issue + di_id" || bad "ir design_issue" "$ROUT"
rm "$R/.wf/transient/design-issues.yaml"
# review_ready present, HEAD == build → redispatch_same_attempt
: > "$R/.wf/transient/review-ready.yaml"
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "redispatch_same_attempt" ] \
    && ok "inspect-review: review_ready + HEAD==build → redispatch_same_attempt" || bad "ir redispatch" ""
# HEAD advanced with an approval subject → approved
gitc "$R" "T1 review: approved" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "approved" ] \
    && ok "inspect-review: HEAD advanced + approval subject → approved" || bad "ir approved" ""
# a preserve commit ON TOP of the approval marker → still approved (L-051)
gitc "$R" "chore(T1): preserve uncommitted files from a prior halted dispatch" >/dev/null
ROUT="$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")"
[ "$(jget "$ROUT" "d['verdict']")" = "approved" ] \
    && ok "inspect-review: preserve commit above the approval marker → approved" || bad "ir preserve approved" "$ROUT"
[ "$(jget "$ROUT" "d['tip_sha']")" != "$(jget "$ROUT" "d['head_sha']")" ] \
    && ok "inspect-review: tip_sha reports the peeled verdict commit" || bad "ir tip_sha" "$ROUT"
# a preserve commit on top of the BUILD commit is not an advance → redispatch_same_attempt
R4="$(mkrepo)"; B4="$(gitc "$R4" "T4 build: done")"; : > "$R4/.wf/transient/review-ready.yaml"
gitc "$R4" "chore(T4): preserve uncommitted files from a prior halted dispatch" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R4" T4 "$B4")" "d['verdict']")" = "redispatch_same_attempt" ] \
    && ok "inspect-review: preserve commit above the build commit → redispatch_same_attempt" || bad "ir preserve redispatch" ""
# advanced but NOT an approval subject, review_ready present, no feedback → escalate_ambiguous
R2="$(mkrepo)"; B2="$(gitc "$R2" "T2 build: done")"; : > "$R2/.wf/transient/review-ready.yaml"
gitc "$R2" "T2 some other change" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R2" T2 "$B2")" "d['verdict']")" = "escalate_ambiguous" ] \
    && ok "inspect-review: advanced w/o approval subject → escalate_ambiguous" || bad "ir ambiguous" ""
# no review_ready → defer_to_build_inspector
R3="$(mkrepo)"; B3="$(gitc "$R3" "T3 build: done")"
[ "$(jget "$(wf orchestrate inspect-review-return "$R3" T3 "$B3")" "d['verdict']")" = "defer_to_build_inspector" ] \
    && ok "inspect-review: no review_ready → defer_to_build_inspector" || bad "ir defer" ""

# ── preserve-uncommitted ─────────────────────────────────────────────────────

R="$(mkrepo)"; gitc "$R" "base" >/dev/null
[ "$(wf orchestrate preserve-uncommitted "$R" T1)" = "clean" ] \
    && ok "preserve: clean tree → clean" || bad "pres clean" ""
# tracked-modified file → committed
echo x > "$R/tracked.txt"; git -C "$R" add tracked.txt; git -C "$R" -c core.hooksPath=/dev/null commit -q -m add
echo y >> "$R/tracked.txt"
OUTP="$(wf orchestrate preserve-uncommitted "$R" T1)"
[ "${OUTP%% *}" = "committed" ] && ok "preserve: tracked-modified → committed" || bad "pres tracked" "$OUTP"
# every untracked file is task work — preserved whether or not files_to_touch names it
printf 'files_to_touch:\n  - in_scope.txt\n' > "$R/.wf/transient/current-task.yaml"
echo a > "$R/in_scope.txt"; echo b > "$R/beyond_expected.txt"
OUTP2="$(wf orchestrate preserve-uncommitted "$R" T1)"
[ "${OUTP2%% *}" = "committed" ] && ok "preserve: untracked declared file → committed" || bad "pres declared" "$OUTP2"
git -C "$R" status --porcelain=v1 | grep -q "beyond_expected.txt" \
    && bad "pres beyond" "left uncommitted" || ok "preserve: untracked file beyond the expected set → committed too"
# an ignored file is never swept up
echo "junk.log" > "$R/.gitignore"; git -C "$R" add .gitignore; git -C "$R" -c core.hooksPath=/dev/null commit -q -m ignore
echo z > "$R/junk.log"
wf orchestrate preserve-uncommitted "$R" T1 >/dev/null
git -C "$R" -c core.hooksPath=/dev/null log --name-only -1 --pretty=format: | grep -q "junk.log" \
    && bad "pres ignored" "ignored file committed" || ok "preserve: gitignored file stays out"

# ── dispatch-fix ─────────────────────────────────────────────────────────────
# Every design issue routes to the single spec fixer, wf-spec-fix. Build/review/stage-repair
# raise a BARE issue (no fix_kind); wf-tl tags a preparing-phase slice issue `scope: slice`.

mk_di() {  # mk_di [extra-flow-fields] → project dir; DI-1 is a bare running-stage issue
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<YAML
version: 1
paths:
  design_issues: ".wf/transient/design-issues.yaml"
  sprint: ".wf/transient/sprint.yaml"
YAML
    cat > "$p/.wf/transient/design-issues.yaml" <<YAML
issues:
  - {id: DI-1, task_id: T1, status: open${1:+, $1}}
YAML
    echo "$p"
}
# A bare design issue (no fix_kind) routes to wf-spec-fix autonomously.
P="$(mk_di)"
OUTD="$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml")"; RC=$?
[ "$(jget "$OUTD" "d['subagent_type']")" = "wf-spec-fix" ] && [ "$RC" -eq 0 ] \
    && ok "dispatch-fix: bare design issue → wf-spec-fix (exit 0)" || bad "df bare" "$OUTD rc=$RC"
[ "$(jget "$OUTD" "d['human_gate']")" = "False" ] \
    && ok "dispatch-fix: bare issue is autonomous (no human gate)" || bad "df bare gate" "$OUTD"
# The router ignores any fix_kind on the entry — routing no longer depends on the raiser's guess.
P="$(mk_di "fix_kind: spec_amendment")"
[ "$(jget "$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml")" "d['subagent_type']")" = "wf-spec-fix" ] \
    && ok "dispatch-fix: a leftover fix_kind does not change the route" || bad "df fixkind-ignored" ""

# A slice issue wf-tl raises in `preparing` carries scope: slice — no task, no sprint.
# Routes to wf-spec-fix autonomously, but only for a bounded number of re-cut rounds.

mk_slice_di() {  # mk_slice_di <how-many-rounds> → prints project dir; the LAST is open
    local p n; p="$(mktemp -d)"; n="$1"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<YAML
version: 1
paths:
  design_issues: ".wf/transient/design-issues.yaml"
  sprint: ".wf/transient/sprint.yaml"
YAML
    echo "issues:" > "$p/.wf/transient/design-issues.yaml"
    for i in $(seq 1 "$n"); do
        local st=resolved; [ "$i" -eq "$n" ] && st=open
        echo "  - {id: DI-SLICE-$i, task_id: null, scope: slice, status: $st}" \
            >> "$p/.wf/transient/design-issues.yaml"
    done
    echo "$p"
}
P="$(mk_slice_di 1)"
OUTS="$(wf orchestrate dispatch-fix DI-SLICE-1 --config "$P/.wf/config.yaml")"; RCS=$?
[ "$(jget "$OUTS" "d['subagent_type']")" = "wf-spec-fix" ] && [ "$RCS" -eq 0 ] \
    && ok "dispatch-fix: slice issue → wf-spec-fix (exit 0)" || bad "df slice" "$OUTS rc=$RCS"
[ "$(jget "$OUTS" "d['envelope']['task_id'] is None")" = "True" ] \
    && ok "dispatch-fix: slice envelope carries no task_id" || bad "df slice task_id" "$OUTS"
[ "$(jget "$OUTS" "'sprint_artifact' in d['envelope']")" = "False" ] \
    && ok "dispatch-fix: slice envelope omits sprint_artifact (none exists in preparing)" \
    || bad "df slice sprint_artifact" "$OUTS"
# The envelope names the artifacts that are THERE: dispatch-fix reports what is on disk.
P="$(mk_slice_di 1)"
: > "$P/.wf/transient/sprint.yaml"
OUTSP="$(wf orchestrate dispatch-fix DI-SLICE-1 --config "$P/.wf/config.yaml")"
[ "$(jget "$OUTSP" "d['envelope'].get('sprint_artifact') or ''")" = ".wf/transient/sprint.yaml" ] \
    && ok "dispatch-fix: slice envelope names \$SPRINT when one is on disk" \
    || bad "df slice sprint present" "$OUTSP"
# round 2 is still autonomous — the loop is cheap, and one bad cut deserves one re-cut
P="$(mk_slice_di 2)"
OUT2="$(wf orchestrate dispatch-fix DI-SLICE-2 --config "$P/.wf/config.yaml")"; RC2=$?
[ "$(jget "$OUT2" "d['human_gate']")" = "False" ] && [ "$RC2" -eq 0 ] \
    && ok "dispatch-fix: 2nd slice rejection still routes to wf-spec-fix" || bad "df slice round2" "$OUT2 rc=$RC2"
# round 3 → the fixer is not converging; a human rules on the slice
P="$(mk_slice_di 3)"
OUT3="$(wf orchestrate dispatch-fix DI-SLICE-3 --config "$P/.wf/config.yaml" 2>/dev/null)"; RC3=$?
[ "$(jget "$OUT3" "d['human_gate']")" = "True" ] && [ "$RC3" -eq 1 ] \
    && ok "dispatch-fix: 3rd slice rejection → human gate (exit 1)" || bad "df slice round3" "$OUT3 rc=$RC3"
[ -n "$(jget "$OUT3" "d.get('reason') or ''")" ] \
    && ok "dispatch-fix: the round-bound gate says why" || bad "df slice round3 reason" "$OUT3"
# The bound counts slice rejections ONLY: a sprint's bare running-stage issues share the
# file, and counting them would gate a slice loop that has laps left.
P="$(mk_slice_di 2)"
cat >> "$P/.wf/transient/design-issues.yaml" <<'YAML'
  - {id: DI-1, task_id: T1, status: resolved}
  - {id: DI-2, task_id: T2, status: resolved}
YAML
OUTM="$(wf orchestrate dispatch-fix DI-SLICE-2 --config "$P/.wf/config.yaml" 2>/dev/null)"; RCM=$?
[ "$(jget "$OUTM" "d['human_gate']")" = "False" ] && [ "$RCM" -eq 0 ] \
    && ok "dispatch-fix: the round bound counts slice rejections, not the sprint's other DIs" \
    || bad "df slice mixed-kinds" "$OUTM rc=$RCM"

# ── sweep-transients ─────────────────────────────────────────────────────────
# Staleness routes on pipeline_state.task_states: the artifact names its task_id;
# a terminal task status (approved/completed/blocked/escalated/design_issue) makes
# the artifact stale; an active/unknown task — or no state at all — keeps it.

mk_sweep() {  # mk_sweep <task-status> → project dir with a feedback.yaml for T1
              # (+ task_states.T1.status=<status>; empty <status> → no pipeline-state file)
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<'YAML'
version: 1
paths:
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  feedback: ".wf/transient/feedback.yaml"
  review_ready: ".wf/transient/review-ready.yaml"
YAML
    printf 'task_id: T1\nverdict: REJECTED\n' > "$p/.wf/transient/feedback.yaml"
    if [ -n "$1" ]; then
        cat > "$p/.wf/transient/pipeline-state.yaml" <<YAML
task_states:
  T1: {status: $1}
YAML
    fi
    echo "$p"
}
P="$(mk_sweep approved)"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['deleted'])")" = "1" ] && [ ! -f "$P/.wf/transient/feedback.yaml" ] \
    && ok "sweep: artifact for an approved task → deleted" || bad "sweep approved" "$SW"
P="$(mk_sweep building)"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['skipped'])")" = "1" ] && [ -f "$P/.wf/transient/feedback.yaml" ] \
    && ok "sweep: artifact for a building task → kept" || bad "sweep building" "$SW"
P="$(mk_sweep "")"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['skipped'])")" = "1" ] && [ -f "$P/.wf/transient/feedback.yaml" ] \
    && ok "sweep: no pipeline-state file → kept" || bad "sweep nostate" "$SW"
P="$(mk_sweep approved)"
printf 'verdict: REJECTED\n' > "$P/.wf/transient/feedback.yaml"   # no task_id in artifact
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['skipped'])")" = "1" ] && [ -f "$P/.wf/transient/feedback.yaml" ] \
    && ok "sweep: artifact without a readable task_id → kept" || bad "sweep no-task-id" "$SW"
P="$(mk_sweep approved)"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml" --dry-run)"
[ "$(jget "$SW" "len(d['deleted'])")" = "1" ] && [ -f "$P/.wf/transient/feedback.yaml" ] \
    && ok "sweep: --dry-run reports stale without deleting" || bad "sweep dry-run" "$SW"

# ── sweep-transients: design-issues (per-entry prune) ────────────────────────
# design-issues.yaml is a LIST of entries, each with its own task_id — the top-level
# task_id rule cannot read it. It prunes per entry instead: `resolved` is never a live
# handoff (prune regardless of task_states — the shipped sprint's states are gone by
# then); an `open` entry whose task is terminal in task_states is prunable too; every
# other `open` entry stays, including task_id-less (slice-scoped) ones. Emptied → the
# file is deleted. Per-entry prunes surface in `pruned`; a file left holding live
# issues surfaces in `skipped`.

mk_sweep_di() {  # mk_sweep_di <issues-body> <task-states-body> → project dir with a
                 # design-issues.yaml (+ pipeline-state.yaml)
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<'YAML'
version: 1
paths:
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  design_issues: ".wf/transient/design-issues.yaml"
YAML
    printf '%b' "$1" > "$p/.wf/transient/design-issues.yaml"
    printf '%b' "$2" > "$p/.wf/transient/pipeline-state.yaml"
    echo "$p"
}

# The observed bug: every entry resolved, from a sprint whose task_states complete-sprint
# already wiped (bare `idle`). Nothing is keyed on task_states, so all three prune.
DI_RESOLVED='issues:\n  - {id: DI-T20, task_id: T20, fix_kind: contract_amendment, status: resolved}\n  - {id: DI-T21, task_id: T21, fix_kind: spec_amendment, status: resolved}\n  - {id: DI-T3, task_id: T3, fix_kind: component_defect, status: resolved}\n'
P="$(mk_sweep_di "$DI_RESOLVED" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['deleted'])")" = "1" ] && [ "$(jget "$SW" "len(d['pruned'])")" = "3" ] \
    && [ ! -f "$P/.wf/transient/design-issues.yaml" ] \
    && ok "sweep-di: all-resolved + wiped task_states → entries pruned, file deleted" \
    || bad "sweep-di resolved-empty-state" "$SW"

# Mixed: the resolved entry prunes, the open one survives → file rewritten, both reported.
DI_MIXED='issues:\n  - {id: DI-1, task_id: T20, fix_kind: contract_amendment, status: resolved}\n  - {id: DI-2, task_id: T99, fix_kind: spec_amendment, status: open}\n'
P="$(mk_sweep_di "$DI_MIXED" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && [ "$(jget "$SW" "d['pruned'][0]['id']")" = "DI-1" ] \
    && [ "$(jget "$SW" "len(d['skipped'])")" = "1" ] \
    && grep -q "DI-2" "$P/.wf/transient/design-issues.yaml" \
    && ! grep -q "DI-1" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: mixed → resolved pruned, survivors rewritten, both reported" \
    || bad "sweep-di mixed" "$SW"

# A slice-scoped open issue (no task yet) is a live handoff — never pruned.
DI_NULL='issues:\n  - {id: DI-5, task_id: null, fix_kind: spec_amendment, status: open}\n'
P="$(mk_sweep_di "$DI_NULL" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] && [ "$(jget "$SW" "len(d['skipped'])")" = "1" ] \
    && [ -f "$P/.wf/transient/design-issues.yaml" ] \
    && ok "sweep-di: open entry with task_id: null → kept" || bad "sweep-di null-task" "$SW"

# A RESOLVED slice-scoped entry is the preparing loop's own history: dispatch-fix counts
# these to bound the re-design rounds, so pruning one mid-loop under-counts the round on
# a resume and hands the SA a third lap it should not get. Keep them while preparing runs.
DI_ROUND1='issues:\n  - {id: DI-SLICE-1, task_id: null, scope: slice, status: resolved}\n  - {id: DI-SLICE-2, task_id: null, scope: slice, status: open}\n'
P="$(mk_sweep_di "$DI_ROUND1" 'current_phase: preparing\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] \
    && grep -q "DI-SLICE-1" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: resolved slice issue kept while preparing (it is the round count)" \
    || bad "sweep-di slice-history" "$SW"
# Once preparing is over, the same entry is residue.
P="$(mk_sweep_di "$DI_ROUND1" 'current_phase: running_stage\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && [ "$(jget "$SW" "d['pruned'][0]['id']")" = "DI-SLICE-1" ] \
    && grep -q "DI-SLICE-2" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: resolved slice issue pruned once preparing is over" \
    || bad "sweep-di slice-residue" "$SW"
# No pipeline-state file → the phase is UNKNOWN, which is not "not preparing": keep
# everything, exactly as the top-level-task_id sweep does.
P="$(mk_sweep_di "$DI_ROUND1" 'current_phase: preparing\n')"
rm "$P/.wf/transient/pipeline-state.yaml"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] && [ "$(jget "$SW" "len(d['deleted'])")" = "0" ] \
    && grep -q "DI-SLICE-1" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: no pipeline-state file → nothing pruned (phase unknown)" \
    || bad "sweep-di no-state" "$SW"

# A status-less entry is the malformed-writer case: it defaults to `open` and stays. The
# other default silently destroys a live handoff.
DI_NOSTATUS='issues:\n  - {id: DI-8, task_id: T1, fix_kind: contract_amendment}\n'
P="$(mk_sweep_di "$DI_NOSTATUS" 'task_states:\n  T1: {status: building}\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] && grep -q "DI-8" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: entry without a status → read as open, kept" || bad "sweep-di no-status" "$SW"

# An unreadable (non-mapping) entry survives the rewrite — dropping it destroys data
# nothing else can recover.
DI_JUNK='issues:\n  - a bare string, not a mapping\n  - {id: DI-9, task_id: T20, fix_kind: spec_amendment, status: resolved}\n'
P="$(mk_sweep_di "$DI_JUNK" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && [ -f "$P/.wf/transient/design-issues.yaml" ] \
    && grep -q "a bare string" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: unreadable entry survives the prune rewrite" || bad "sweep-di junk-entry" "$SW"

# An open entry whose task IS terminal in task_states: its consumer never comes back.
DI_STALE_OPEN='issues:\n  - {id: DI-6, task_id: T1, fix_kind: contract_amendment, status: open}\n'
P="$(mk_sweep_di "$DI_STALE_OPEN" 'task_states:\n  T1: {status: approved}\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && [ "$(jget "$SW" "len(d['deleted'])")" = "1" ] \
    && [ ! -f "$P/.wf/transient/design-issues.yaml" ] \
    && ok "sweep-di: open entry for an approved task → pruned" || bad "sweep-di stale-open" "$SW"

# An open entry for a task still in the loop stays put.
DI_LIVE='issues:\n  - {id: DI-7, task_id: T1, fix_kind: contract_amendment, status: open}\n'
P="$(mk_sweep_di "$DI_LIVE" 'task_states:\n  T1: {status: building}\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] && [ "$(jget "$SW" "len(d['skipped'])")" = "1" ] \
    && [ -f "$P/.wf/transient/design-issues.yaml" ] \
    && ok "sweep-di: open entry for a building task → kept" || bad "sweep-di live" "$SW"

# No design-issues file at all → silent no-op, exit 0.
P="$(mk_sweep_di "$DI_RESOLVED" 'current_phase: idle\n')"
rm "$P/.wf/transient/design-issues.yaml"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"; RCS=$?
[ "$RCS" -eq 0 ] && [ "$(jget "$SW" "len(d['pruned'])")" = "0" ] \
    && [ "$(jget "$SW" "len(d['deleted'])")" = "0" ] && [ "$(jget "$SW" "len(d['skipped'])")" = "0" ] \
    && ok "sweep-di: no design-issues file → no-op" || bad "sweep-di absent" "$SW rc=$RCS"

# --dry-run reports the prune but leaves every byte on disk.
P="$(mk_sweep_di "$DI_RESOLVED" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml" --dry-run)"
[ "$(jget "$SW" "len(d['pruned'])")" = "3" ] && [ "$(jget "$SW" "len(d['deleted'])")" = "1" ] \
    && grep -q "DI-T20" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: --dry-run reports prunes without writing" || bad "sweep-di dry-run" "$SW"

# --dry-run on a mixed file must not rewrite the survivors either.
P="$(mk_sweep_di "$DI_MIXED" 'current_phase: idle\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml" --dry-run)"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && grep -q "DI-1" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: --dry-run leaves a mixed file unrewritten" || bad "sweep-di dry-run mixed" "$SW"

echo ""
echo "  orchestrate helpers: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
