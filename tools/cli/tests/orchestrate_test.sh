#!/usr/bin/env bash
#
# Tests for the wf orchestrate helpers — the judgment-free return-inspection and
# resume-safety verdicts the driver routes on.
# Run: bash tools/cli/tests/orchestrate_test.sh (exit 0 = pass).
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

# a task that DELETED a file: the live dems failure. `git status` reports a staged
# deletion as "D " — already staged, and gone from both the worktree and the index, so
# `git add <that path>` cannot match it and exits 128. That took the whole preserve down
# with it and NOTHING was preserved, for any file in the task.
RD="$(mkrepo)"
echo old > "$RD/doomed.txt"; echo keep > "$RD/kept.txt"
git -C "$RD" add doomed.txt kept.txt; git -C "$RD" -c core.hooksPath=/dev/null commit -q -m base
git -C "$RD" rm -q doomed.txt                       # staged deletion  → "D "
echo more >> "$RD/kept.txt"                         # unstaged modify  → " M"
echo new > "$RD/added.txt"                          # untracked        → "??"
OUTD="$(wf orchestrate preserve-uncommitted "$RD" T1)"
[ "${OUTD%% *}" = "committed" ] && ok "preserve: a staged deletion does not abort the preserve" || bad "pres deleted" "$OUTD"
[ -z "$(git -C "$RD" status --porcelain=v1)" ] \
    && ok "preserve: deletion + modify + untracked all land in the commit" \
    || bad "pres deleted leftovers" "$(git -C "$RD" status --porcelain=v1)"
git -C "$RD" -c core.hooksPath=/dev/null log --name-only -1 --pretty=format: | grep -q "doomed.txt" \
    && ok "preserve: the deletion itself is recorded" || bad "pres deletion recorded" ""

# an UNSTAGED deletion — "` D`" — is the same file gone with `rm`, never `git rm`
RU="$(mkrepo)"
echo old > "$RU/doomed.txt"; git -C "$RU" add doomed.txt
git -C "$RU" -c core.hooksPath=/dev/null commit -q -m base
rm "$RU/doomed.txt"
OUTU="$(wf orchestrate preserve-uncommitted "$RU" T1)"
[ "${OUTU%% *}" = "committed" ] && ok "preserve: an unstaged deletion is preserved too" || bad "pres rm" "$OUTU"
[ -z "$(git -C "$RU" status --porcelain=v1)" ] \
    && ok "preserve: the unstaged deletion leaves a clean tree" || bad "pres rm leftovers" ""

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

# A slice-scoped entry (no task) is a live handoff until the design role resolves it;
# once resolved it is residue like any other closed entry, whatever the phase.
DI_SLICE='issues:\n  - {id: DI-SLICE-1, task_id: null, scope: slice, status: resolved}\n  - {id: DI-SLICE-2, task_id: null, scope: slice, status: open}\n'
P="$(mk_sweep_di "$DI_SLICE" 'current_phase: designing\n')"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "1" ] && [ "$(jget "$SW" "d['pruned'][0]['id']")" = "DI-SLICE-1" ] \
    && grep -q "DI-SLICE-2" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: resolved slice issue pruned, the open one kept" \
    || bad "sweep-di slice" "$SW"
# No pipeline-state file → keep everything, exactly as the top-level-task_id sweep does.
P="$(mk_sweep_di "$DI_SLICE" 'current_phase: designing\n')"
rm "$P/.wf/transient/pipeline-state.yaml"
SW="$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")"
[ "$(jget "$SW" "len(d['pruned'])")" = "0" ] && [ "$(jget "$SW" "len(d['deleted'])")" = "0" ] \
    && grep -q "DI-SLICE-1" "$P/.wf/transient/design-issues.yaml" \
    && ok "sweep-di: no pipeline-state file → nothing pruned" \
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

# ── consume-marker ───────────────────────────────────────────────────────────
# A presence marker is written by one role and read by exactly one dispatch. The
# driver clears it at the consumption point, so a marker can never be read a second
# time as a fresh verdict. Moved aside rather than unlinked: a rejected build's
# feedback is the only readable account of why, and a human reads it off a blocked
# task. Its path is resolved from the WORKTREE's own config, like the inspectors.

mkrepo_cfg() {  # → repo dir with a .wf/config.yaml naming both markers
    local r; r="$(mkrepo)"
    cat > "$r/.wf/config.yaml" <<'YAML'
version: 1
paths:
  feedback: ".wf/transient/feedback.yaml"
  review_ready: ".wf/transient/review-ready.yaml"
YAML
    echo "$r"
}

R="$(mkrepo_cfg)"
[ "$(wf orchestrate consume-marker "$R" feedback)" = "absent" ] \
    && ok "consume: no marker on disk → absent" || bad "consume absent" ""

printf 'task_id: T1\nfailures: []\n' > "$R/.wf/transient/feedback.yaml"
OUTC="$(wf orchestrate consume-marker "$R" feedback)"
[ "${OUTC%% *}" = "consumed" ] && [ ! -f "$R/.wf/transient/feedback.yaml" ] \
    && [ -f "$R/.wf/transient/feedback.yaml.consumed" ] \
    && ok "consume: feedback → moved aside, no longer a marker" || bad "consume feedback" "$OUTC"
grep -q "task_id: T1" "$R/.wf/transient/feedback.yaml.consumed" \
    && ok "consume: the rejection stays readable for a human" || bad "consume content" ""

: > "$R/.wf/transient/review-ready.yaml"
wf orchestrate consume-marker "$R" review_ready >/dev/null
[ ! -f "$R/.wf/transient/review-ready.yaml" ] \
    && [ -f "$R/.wf/transient/review-ready.yaml.consumed" ] \
    && ok "consume: review_ready → moved aside" || bad "consume review_ready" ""

# A second rejection overwrites the first's residue rather than failing on it.
printf 'task_id: T1\nfailures: [second]\n' > "$R/.wf/transient/feedback.yaml"
OUTC="$(wf orchestrate consume-marker "$R" feedback)"; RCC=$?
[ "$RCC" -eq 0 ] && grep -q second "$R/.wf/transient/feedback.yaml.consumed" \
    && ok "consume: a later marker replaces the prior residue" || bad "consume overwrite" "$OUTC rc=$RCC"

# The residue must be inert: the inspectors key on the exact configured path.
R="$(mkrepo_cfg)"; BUILD="$(gitc "$R" "T1 build: done")"
: > "$R/.wf/transient/review-ready.yaml"
printf 'task_id: T1\n' > "$R/.wf/transient/feedback.yaml"
wf orchestrate consume-marker "$R" feedback >/dev/null
gitc "$R" "T1 review: approved" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "approved" ] \
    && ok "consume: consumed residue is inert to inspect-review-return" || bad "consume inert" ""

# THE REGRESSION (observed live in dems, task T1): the build fixes the rejection but
# leaves feedback.yaml behind, the review then approves — and the approval commit is
# never even looked at, because feedback presence is the first branch of the cascade.
# Every later attempt re-rejects an approved build until the attempt cap blocks it.
R="$(mkrepo_cfg)"; BUILD="$(gitc "$R" "T1 build: done")"
: > "$R/.wf/transient/review-ready.yaml"
printf 'task_id: T1\n' > "$R/.wf/transient/feedback.yaml"
gitc "$R" "T1 review: approved" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "rejected" ] \
    && ok "regression: an unconsumed marker re-rejects an approved build" || bad "regression stale" ""
wf orchestrate consume-marker "$R" feedback >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "approved" ] \
    && ok "regression: consuming it lets the approval be read" || bad "regression consumed" ""

# Guards. An unknown marker name is never a licence to move an arbitrary config path,
# and an unconfigured marker is an error rather than a silently restated default.
R="$(mkrepo_cfg)"
wf orchestrate consume-marker "$R" current_task >/dev/null 2>&1
[ $? -eq 2 ] && ok "consume: unknown marker name → exit 2" || bad "consume unknown" ""
wf orchestrate consume-marker "$R/nope" feedback >/dev/null 2>&1
[ $? -eq 2 ] && ok "consume: missing worktree → exit 2" || bad "consume no-worktree" ""
R="$(mkrepo)"   # no .wf/config.yaml at all
printf 'task_id: T1\n' > "$R/.wf/transient/feedback.yaml"
wf orchestrate consume-marker "$R" feedback >/dev/null 2>&1
[ $? -eq 2 ] && [ -f "$R/.wf/transient/feedback.yaml" ] \
    && ok "consume: unconfigured marker → exit 2, file untouched" || bad "consume unconfigured" ""

# Worktree discipline: the path comes from the worktree's OWN config, not a default.
R="$(mkrepo)"
mkdir -p "$R/.wf/elsewhere"
cat > "$R/.wf/config.yaml" <<'YAML'
version: 1
paths:
  feedback: ".wf/elsewhere/rejected.yaml"
YAML
printf 'task_id: T1\n' > "$R/.wf/elsewhere/rejected.yaml"
wf orchestrate consume-marker "$R" feedback >/dev/null
[ ! -f "$R/.wf/elsewhere/rejected.yaml" ] && [ -f "$R/.wf/elsewhere/rejected.yaml.consumed" ] \
    && ok "consume: resolves the worktree's own configured path" || bad "consume own-config" ""

echo ""
echo "  orchestrate helpers: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
