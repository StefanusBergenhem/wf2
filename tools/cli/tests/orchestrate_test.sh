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

R="$(mkrepo)"
: > "$R/.wf/transient/build-blocked.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "build_blocked" ] \
    && ok "inspect-build: build_blocked present → build_blocked" || bad "ib build_blocked" ""
rm "$R/.wf/transient/build-blocked.yaml"
: > "$R/.wf/transient/review-ready.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "ready_for_review" ] \
    && ok "inspect-build: review_ready present → ready_for_review" || bad "ib review_ready" ""
rm "$R/.wf/transient/review-ready.yaml"
printf 'last_step: all_gates_passed\n' > "$R/.wf/transient/build-progress.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "resume_after_gates" ] \
    && ok "inspect-build: build_progress all_gates_passed → resume_after_gates" || bad "ib resume" ""
printf 'last_step: committed\n' > "$R/.wf/transient/build-progress.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "recovered_lost_signal_committed" ] \
    && ok "inspect-build: build_progress committed → recovered_lost_signal_committed" || bad "ib committed" ""
printf 'last_step: scaffolding\n' > "$R/.wf/transient/build-progress.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "restart" ] \
    && ok "inspect-build: build_progress early step → restart" || bad "ib restart" ""
rm "$R/.wf/transient/build-progress.yaml"
[ "$(jget "$(wf orchestrate inspect-build-return "$R" T1)" "d['verdict']")" = "escalate_no_artifacts" ] \
    && ok "inspect-build: nothing present → escalate_no_artifacts" || bad "ib none" ""

# ── inspect-review-return ────────────────────────────────────────────────────

R="$(mkrepo)"
BUILD="$(gitc "$R" "T1 build: done")"
# feedback present → rejected
: > "$R/.wf/transient/feedback.yaml"
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "rejected" ] \
    && ok "inspect-review: feedback present → rejected" || bad "ir rejected" ""
rm "$R/.wf/transient/feedback.yaml"
# review_ready present, HEAD == build → redispatch_same_attempt
: > "$R/.wf/transient/review-ready.yaml"
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "redispatch_same_attempt" ] \
    && ok "inspect-review: review_ready + HEAD==build → redispatch_same_attempt" || bad "ir redispatch" ""
# HEAD advanced with an approval subject → approved
gitc "$R" "T1 review: approved" >/dev/null
[ "$(jget "$(wf orchestrate inspect-review-return "$R" T1 "$BUILD")" "d['verdict']")" = "approved" ] \
    && ok "inspect-review: HEAD advanced + approval subject → approved" || bad "ir approved" ""
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
# untracked file IN files_to_touch → committed; NOT in scope → left alone
printf 'files_to_touch:\n  - in_scope.txt\n' > "$R/.wf/transient/current-task.yaml"
echo a > "$R/in_scope.txt"; echo b > "$R/out_of_scope.txt"
OUTP2="$(wf orchestrate preserve-uncommitted "$R" T1)"
[ "${OUTP2%% *}" = "committed" ] && ok "preserve: untracked in-scope file → committed" || bad "pres in_scope" "$OUTP2"
git -C "$R" status --porcelain=v1 | grep -q "out_of_scope.txt" \
    && ok "preserve: out-of-scope untracked file left uncommitted" || bad "pres out_of_scope" "got committed"

# ── classify-amendment ───────────────────────────────────────────────────────

D="$(mktemp -d)"
cat > "$D/feature.diff" <<'DIFF'
diff --git a/src/foo.go b/src/foo.go
+func New() {}
DIFF
[ "$(wf orchestrate classify-amendment --task-id T1 --diff "$D/feature.diff")" = "feature" ] \
    && ok "classify: non-test file → feature" || bad "cls feature" ""
cat > "$D/newtest.diff" <<'DIFF'
diff --git a/src/foo_test.go b/src/foo_test.go
+func TestNew(t *testing.T) {}
DIFF
[ "$(wf orchestrate classify-amendment --task-id T1 --diff "$D/newtest.diff")" = "feature" ] \
    && ok "classify: test file w/ new test decl → feature" || bad "cls newtest" ""
cat > "$D/follow.diff" <<'DIFF'
diff --git a/src/foo_test.go b/src/foo_test.go
+    assert.Equal(t, want, Renamed())
DIFF
[ "$(wf orchestrate classify-amendment --task-id T1 --diff "$D/follow.diff")" = "mechanical_follow_on" ] \
    && ok "classify: test-only, no new decl → mechanical_follow_on" || bad "cls follow" ""
[ "$(wf orchestrate classify-amendment --task-id T1 --diff "$D/feature.diff" --claim mechanical_follow_on)" = "reject" ] \
    && ok "classify: non-test file claimed mechanical → reject" || bad "cls reject" ""

# ── dispatch-fix ─────────────────────────────────────────────────────────────

mk_di() {  # mk_di <fix_kind> → prints project dir
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<YAML
version: 1
paths:
  design_issues: ".wf/transient/design-issues.yaml"
  sprint: ".wf/transient/sprint.yaml"
YAML
    cat > "$p/.wf/transient/design-issues.yaml" <<YAML
issues:
  - {id: DI-1, task_id: T1, fix_kind: $1, status: open}
YAML
    echo "$p"
}
P="$(mk_di contract_amendment)"
OUTD="$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml")"; RC=$?
[ "$(jget "$OUTD" "d['subagent_type']")" = "wf-swa" ] && [ "$RC" -eq 0 ] \
    && ok "dispatch-fix: contract_amendment → wf-swa (exit 0)" || bad "df contract" "$OUTD rc=$RC"
P="$(mk_di spec_amendment)"
[ "$(jget "$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml")" "d['subagent_type']")" = "wf-sa" ] \
    && ok "dispatch-fix: spec_amendment → wf-sa" || bad "df spec" ""
# recut is deliberately NOT a wf2 fix_kind (PO doesn't cut sprints; SA owns the slice) —
# it falls through to the human gate, never to wf-po.
P="$(mk_di recut)"
OUTR="$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml" 2>/dev/null)"; RCR=$?
[ "$(jget "$OUTR" "d['human_gate']")" = "True" ] && [ "$RCR" -eq 1 ] \
    && ok "dispatch-fix: recut is unrouted in wf2 → human gate (not wf-po)" || bad "df recut-dropped" "$OUTR rc=$RCR"
P="$(mk_di mystery)"
OUTU="$(wf orchestrate dispatch-fix DI-1 --config "$P/.wf/config.yaml" 2>/dev/null)"; RCU=$?
[ "$(jget "$OUTU" "d['human_gate']")" = "True" ] && [ "$RCU" -eq 1 ] \
    && ok "dispatch-fix: unknown fix_kind → human gate (exit 1)" || bad "df unknown" "$OUTU rc=$RCU"

# ── sweep-transients ─────────────────────────────────────────────────────────

mk_sweep() {  # mk_sweep <event-ts> → project dir with a feedback.yaml + a build_returned event at <ts>
    local p; p="$(mktemp -d)"; mkdir -p "$p/.wf/transient"
    cat > "$p/.wf/config.yaml" <<'YAML'
version: 1
paths:
  pipeline_state: ".wf/transient/pipeline-state.yaml"
  feedback: ".wf/transient/feedback.yaml"
YAML
    : > "$p/.wf/transient/feedback.yaml"
    cat > "$p/.wf/transient/pipeline-state.yaml" <<YAML
history:
  - {ts: "$1", event: build_returned}
YAML
    echo "$p"
}
P="$(mk_sweep 2099-01-01T00:00:00Z)"   # event after the file mtime → stale → deleted
[ "$(jget "$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")" "len(d['deleted'])")" = "1" ] \
    && ok "sweep: consumed transient (event after mtime) → deleted" || bad "sweep deleted" ""
P="$(mk_sweep 2000-01-01T00:00:00Z)"   # event before the file mtime → live → kept
[ "$(jget "$(wf orchestrate sweep-transients --config "$P/.wf/config.yaml")" "len(d['skipped'])")" = "1" ] \
    && ok "sweep: live transient (no event after mtime) → skipped" || bad "sweep skipped" ""

echo ""
echo "  orchestrate helpers: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
