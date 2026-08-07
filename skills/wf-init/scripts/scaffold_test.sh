#!/usr/bin/env bash
#
# scaffold_test.sh — TDD spec for scaffold.sh.
#
# Verifies: config written from template with tokens resolved (including the per-target
# driver.agent_cmd); the transient dir, telemetry sink, ADR dir, and the
# capabilities/charter/plan/architecture/learnings homes created; gitignore updated; full idempotency
# (re-run clobbers no edited config nor existing home, and does not duplicate the gitignore
# line); and homes skipped when their config key is absent.
# wf2-source-only — never rendered into an install target.
#
# Run:  bash skills/wf-init/scripts/scaffold_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAFFOLD="$HERE/scaffold.sh"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

PROJ="$WORK/proj"
mkdir -p "$PROJ"

echo "== first run =="
bash "$SCAFFOLD" --dir "$PROJ" --target claude --name demo > "$WORK/run1.log" 2>&1 \
    || fail "scaffold exited non-zero (see $WORK/run1.log)"

check "config.yaml created"        "[ -f '$PROJ/.wf/config.yaml' ]"
check "transient dir created"      "[ -d '$PROJ/.wf/transient' ]"
check "name token resolved"        "grep -q 'name: \"demo\"' '$PROJ/.wf/config.yaml'"
check "target token resolved"      "grep -q 'target: \"claude\"' '$PROJ/.wf/config.yaml'"
check "no unresolved {{ tokens"    "! grep -q '{{' '$PROJ/.wf/config.yaml'"
check "gitignore ignores transient" "grep -qF '.wf/transient/' '$PROJ/.gitignore'"
check "telemetry sink created"     "[ -f '$PROJ/.wf/telemetry/sessions.jsonl' ]"
check "adrs dir created"           "[ -d '$PROJ/.wf/adrs' ]"
check "adrs gitkeep created"       "[ -f '$PROJ/.wf/adrs/.gitkeep' ]"
check "capabilities home created"  "[ -f '$PROJ/.wf/CAPABILITIES.yaml' ]"
check "capabilities has structure" "grep -q 'capabilities:' '$PROJ/.wf/CAPABILITIES.yaml'"
check "charter home created"       "[ -f '$PROJ/.wf/charter.md' ]"
check "charter has direction sections" "grep -q '## Target shape' '$PROJ/.wf/charter.md' && grep -q '## No-go zones' '$PROJ/.wf/charter.md'"
check "plan home created"          "[ -f '$PROJ/.wf/plan.md' ]"
check "plan has milestone sections" "grep -q '## Next' '$PROJ/.wf/plan.md'"
check "architecture home created"  "[ -f '$PROJ/.wf/architecture.md' ]"
check "architecture has components section" "grep -q '## Components' '$PROJ/.wf/architecture.md'"
check "learnings home created"     "[ -f '$PROJ/.wf/LEARNINGS.yaml' ]"
check "learnings has structure"    "grep -q 'learnings:' '$PROJ/.wf/LEARNINGS.yaml'"
check "wf-learnings home created"  "[ -f '$PROJ/.wf/wf-learnings.yaml' ]"

echo "== driver.agent_cmd renders per target =="
# The driver launches every role through this template; each harness spells it
# differently, so init bakes the right one in — no runtime branching.
check "claude agent_cmd rendered" "grep -q 'agent_cmd:.*claude -p' '$PROJ/.wf/config.yaml'"
for T in opencode pi; do
    TP="$WORK/t-$T"
    bash "$SCAFFOLD" --dir "$TP" --target "$T" --name "t$T" > "$WORK/run-$T.log" 2>&1 \
        || fail "scaffold --target $T exited non-zero (see $WORK/run-$T.log)"
    check "$T: no unresolved {{ tokens" "! grep -q '{{' '$TP/.wf/config.yaml'"
    check "$T: agent_cmd is not the claude one" \
        "! grep -q 'agent_cmd:.*claude -p' '$TP/.wf/config.yaml'"
    check "$T: agent_cmd passes {prompt}" \
        "grep -q 'agent_cmd:.*{prompt}' '$TP/.wf/config.yaml'"
done
check "opencode agent_cmd is opencode run" "grep -q 'agent_cmd:.*opencode run' '$WORK/t-opencode/.wf/config.yaml'"
# Two model tiers on claude: the shared template pins the workhorse tier, the
# overrides pin the judgment roles (designer, tl, adequacy) to the stronger one.
check "claude agent_cmd pins sonnet" \
    "grep -q \"agent_cmd:.*--model sonnet.*{prompt}\" '$PROJ/.wf/config.yaml'"
check "claude overrides block present" \
    "grep -q 'agent_cmd_overrides:' '$PROJ/.wf/config.yaml'"
# A headless dispatch gets one turn. Four dems builds ended theirs on ToolSearch →
# Monitor → "I'll resume when it notifies me", writing no artifact — with the rule
# against it in the skill they had just read. The tools are denied at launch instead.
# `--disallowedTools` is variadic, so the prompt must come BEFORE it or the flag eats it.
for R in "agent_cmd" "wf-designer" "wf-tl" "wf-adequacy"; do
    check "claude $R denies the parking tools" \
        "grep -q \"$R:.*{prompt}.*--disallowedTools\" '$PROJ/.wf/config.yaml'"
    check "claude $R denies Monitor and ScheduleWakeup" \
        "grep -q \"$R:.*Monitor,ScheduleWakeup\" '$PROJ/.wf/config.yaml'"
done
for R in wf-designer wf-tl wf-adequacy; do
    check "claude override: $R pinned to opus" \
        "grep -A6 'agent_cmd_overrides:' '$PROJ/.wf/config.yaml' | grep -q \"$R:.*--model opus.*{prompt}\""
done
check "opencode overrides carry no claude flags" \
    "! grep -A6 'agent_cmd_overrides:' '$WORK/t-opencode/.wf/config.yaml' | grep -q -- '--model'"

echo "== idempotency =="
# Mark the config as user-edited and append content to durable homes, then re-run.
echo "# user edit" >> "$PROJ/.wf/config.yaml"
echo '{"agent":"prior"}' >> "$PROJ/.wf/telemetry/sessions.jsonl"
echo "  - id: CAP-001" >> "$PROJ/.wf/CAPABILITIES.yaml"
echo "  - id: L-001" >> "$PROJ/.wf/LEARNINGS.yaml"
echo "- edited by hand" >> "$PROJ/.wf/charter.md"
echo "- edited by hand" >> "$PROJ/.wf/plan.md"
echo "- edited by hand" >> "$PROJ/.wf/architecture.md"
bash "$SCAFFOLD" --dir "$PROJ" --target claude --name demo > "$WORK/run2.log" 2>&1 \
    || fail "second scaffold exited non-zero (see $WORK/run2.log)"

check "existing config not clobbered" "grep -q '# user edit' '$PROJ/.wf/config.yaml'"
check "telemetry sink not clobbered" "grep -q 'prior' '$PROJ/.wf/telemetry/sessions.jsonl'"
check "capabilities home not clobbered" "grep -q 'CAP-001' '$PROJ/.wf/CAPABILITIES.yaml'"
check "learnings home not clobbered" "grep -q 'L-001' '$PROJ/.wf/LEARNINGS.yaml'"
check "charter home not clobbered"  "grep -q 'edited by hand' '$PROJ/.wf/charter.md'"
check "plan home not clobbered"     "grep -q 'edited by hand' '$PROJ/.wf/plan.md'"
check "architecture home not clobbered" "grep -q 'edited by hand' '$PROJ/.wf/architecture.md'"
gi_count="$(grep -cF '.wf/transient/' "$PROJ/.gitignore")"
check "gitignore line not duplicated" "[ '$gi_count' -eq 1 ]"

echo "== config is the source of truth for paths =="
# A pre-existing config with non-default paths: scaffold must honor IT, not
# restate defaults. Proves there is one source of truth.
CUSTOM="$WORK/custom"; mkdir -p "$CUSTOM/.wf"
cat > "$CUSTOM/.wf/config.yaml" <<'YAML'
version: 1
project:
  name: "c"
  target: "claude"
paths:
  tools: ".wf/tools"
  transient: ".wf/t2"
  telemetry: ".wf/tel2/log.jsonl"
YAML
bash "$SCAFFOLD" --dir "$CUSTOM" --target claude > /dev/null 2>&1
check "custom telemetry sink honored" "[ -f '$CUSTOM/.wf/tel2/log.jsonl' ]"
check "custom transient dir honored"  "[ -d '$CUSTOM/.wf/t2' ]"
check "custom transient gitignored"   "grep -qxF '.wf/t2/' '$CUSTOM/.gitignore'"
check "default sink NOT created"      "! [ -f '$CUSTOM/.wf/telemetry/sessions.jsonl' ]"
check "no home when key absent"       "! [ -f '$CUSTOM/.wf/CAPABILITIES.yaml' ]"
check "no charter when key absent"    "! [ -f '$CUSTOM/.wf/charter.md' ]"
check "no plan when key absent"       "! [ -f '$CUSTOM/.wf/plan.md' ]"
check "no architecture when key absent" "! [ -f '$CUSTOM/.wf/architecture.md' ]"

echo "== bad target rejected =="
if bash "$SCAFFOLD" --dir "$WORK/proj2" --target frobnicate --name x > /dev/null 2>&1; then
    fail "unknown target should exit non-zero"
else
    pass "unknown target rejected"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
