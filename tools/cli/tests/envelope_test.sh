#!/usr/bin/env bash
#
# Tests for `wf envelope show` — the resolved paths/commands block a dispatched role
# reads instead of opening .wf/config.yaml.
# Run: bash tools/cli/tests/envelope_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CLI/../.." && pwd)"
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; echo "         $2"; }

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1

project:
  name: "demo"
  base_branch: "main"

paths:
  tools: ".wf/tools"                     # a trailing comment the block must not carry
  transient: ".wf/transient"
  current_task: ".wf/transient/current-task.yaml"
  review_ready: ".wf/transient/review-ready.yaml"
  tests: ["backend", "frontend/src"]
  capabilities: ".wf/CAPABILITIES.yaml"

commands:
  preflight: "make check"
  format: ""

driver:
  agent_cmd: 'claude -p "{prompt}"'

review:
  passes: [wf-review]

limits:
  tasks_per_stage: 10

hygiene:
  plan_max: 60
YAML

wf() { (cd "$PROJ" && WF_CLI="$CLI" "$PYTHON" -c 'import sys,os; sys.path.insert(0,os.environ["WF_CLI"]); import envelope; raise SystemExit(envelope.COMMANDS[(sys.argv[1],sys.argv[2])](sys.argv[3:]))' "$@" --config "$PROJ/.wf/config.yaml"); }

echo "wf envelope show"
OUT="$(wf envelope show 2>&1)"; RC=$?

[ $RC -eq 0 ] && ok "exits 0" || bad "exits 0" "rc=$RC: $OUT"

grep -q '^paths.current_task: .wf/transient/current-task.yaml$' <<<"$OUT" \
  && ok "carries a path key as 'paths.<key>: <value>'" \
  || bad "carries a path key" "$OUT"

grep -q '^commands.preflight: make check$' <<<"$OUT" \
  && ok "carries a command key" || bad "carries a command key" "$OUT"

# The whole point: the block is the config's DATA, never its prose.
grep -q '#' <<<"$OUT" && bad "strips comments" "a '#' survived: $OUT" \
  || ok "strips comments"

# An empty command means "no such gate in this repo" — a role must be able to tell that
# apart from a key the block simply forgot, so it is listed with an empty value.
grep -q '^commands.format: $' <<<"$OUT" \
  && ok "an empty command is listed, not dropped" || bad "empty command listed" "$OUT"

# paths.tests is a list (a polyglot repo has several test roots).
grep -q '^paths.tests: backend, frontend/src$' <<<"$OUT" \
  && ok "a list path renders comma-joined" || bad "list path" "$OUT"

# A role IS held to these two, so it must be able to read them: tasks_per_stage bounds
# a cut, hygiene.*_max bounds what a role writes.
grep -q '^limits.tasks_per_stage: 10$' <<<"$OUT" \
  && ok "carries the limits a role is held to" || bad "carries limits" "$OUT"
grep -q '^hygiene.plan_max: 60$' <<<"$OUT" \
  && ok "carries the hygiene ceilings a role is held to" || bad "carries hygiene" "$OUT"

# Loop knobs are not: no role acts on them, and agent_cmd is the launch template of the
# very dispatch reading this.
grep -qi 'agent_cmd\|review.passes\|base_branch' <<<"$OUT" \
  && bad "carries no loop knobs" "leaked a loop knob: $OUT" \
  || ok "carries no loop knobs"

# Size is the whole reason this exists.
CHARS=$(wc -c <<<"$OUT")
[ "$CHARS" -lt 1000 ] && ok "block is small (${CHARS} chars)" \
  || bad "block is small" "${CHARS} chars — the config it replaces is ~20k"

echo "transport only"
# The block is the config's values verbatim — NOT resolved to absolute. Which tree a
# path roots on stays the preamble's rule (a worktree-local artifact roots on the
# worktree, a host-level one on the repo), so moving the values off config.yaml cannot
# change what any of them mean.
grep -q '^paths.capabilities: .wf/CAPABILITIES.yaml$' <<<"$OUT" \
  && ok "values are the configured relatives, unanchored" || bad "values unanchored" "$OUT"

echo "declared subset"
# A dispatch renders the keys the role declares and nothing else: an undeclared key is
# one that role never resolves, and every extra line is context each of its dispatches
# pays for. An unknown key is named as an error rather than silently skipped — the role
# would otherwise read a line that is not there.
OUT2="$("$PYTHON" -c '
import sys; sys.path.insert(0, sys.argv[1])
import envelope
print(envelope.render(sys.argv[2], keys=["paths.current_task", "commands.preflight"]))
' "$CLI" "$PROJ/.wf/config.yaml" 2>&1)"
[ "$(grep -c . <<<"$OUT2")" = "2" ] \
  && ok "renders only the declared keys" || bad "declared subset" "$OUT2"
grep -q '^paths.current_task: .wf/transient/current-task.yaml$' <<<"$OUT2" \
  && ok "a declared key keeps its value" || bad "declared value" "$OUT2"
grep -q 'capabilities\|tasks_per_stage\|plan_max' <<<"$OUT2" \
  && bad "undeclared keys are dropped" "$OUT2" || ok "undeclared keys are dropped"
OUT4="$("$PYTHON" -c '
import sys; sys.path.insert(0, sys.argv[1])
import envelope
print(envelope.render(sys.argv[2], keys=["paths.nope"]))
' "$CLI" "$PROJ/.wf/config.yaml" 2>&1)"; RC4=$?
[ $RC4 -ne 0 ] && grep -q 'paths.nope' <<<"$OUT4" \
  && ok "a declared key the config lacks names itself and fails" \
  || bad "unknown declared key" "rc=$RC4: $OUT4"

echo "failure modes"
cat > "$PROJ/.wf/bad.yaml" <<'YAML'
version: 1
paths: {}
YAML
OUT3="$( (cd "$PROJ" && WF_CLI="$CLI" "$PYTHON" -c 'import sys,os; sys.path.insert(0,os.environ["WF_CLI"]); import envelope; raise SystemExit(envelope.COMMANDS[("envelope","show")](sys.argv[1:]))' --config "$PROJ/.wf/bad.yaml") 2>&1 )"; RC3=$?
[ $RC3 -ne 0 ] && ok "a config with no paths is a named failure, not an empty block" \
  || bad "empty paths fails" "rc=$RC3: $OUT3"

echo
echo "pass=$pass fail=$fail"
rm -rf "$PROJ"
[ "$fail" -eq 0 ]
