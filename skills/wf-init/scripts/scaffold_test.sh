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
# The session log is a per-cycle working buffer, not source: every dispatch appends to it,
# and the durable record is the drain's paths.archive snapshot. Committing it would dirty
# the tree under the driver's own clean-tree gate (L-137).
check "gitignore ignores telemetry" "grep -qF '.wf/telemetry/' '$PROJ/.gitignore'"
check "adrs dir created"           "[ -d '$PROJ/.wf/adrs' ]"
check "adrs gitkeep created"       "[ -f '$PROJ/.wf/adrs/.gitkeep' ]"
check "repo-state home created"    "[ -f '$PROJ/.wf/wf-repo-state.yaml' ]"
check "repo-state carries every id lane" "grep -q 'cap:' '$PROJ/.wf/wf-repo-state.yaml' && grep -q 'sys_tc:' '$PROJ/.wf/wf-repo-state.yaml' && grep -q 'stage:' '$PROJ/.wf/wf-repo-state.yaml' && grep -q 'learning:' '$PROJ/.wf/wf-repo-state.yaml' && grep -q 'wf_learning:' '$PROJ/.wf/wf-repo-state.yaml'"
# The counters left config.yaml — a lingering copy there is the one a role would bump,
# and the two would silently diverge.
check "config carries no id_counters" "! grep -q '^id_counters:' '$PROJ/.wf/config.yaml'"
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
# pi's headless prompt command is `pi -p` — NOT `pi run`, which reads no prompt and
# exits having done nothing. The render must carry the real verb, stream json for the
# driver's cost read, persist its session under the project's transient dir for the
# usage hook, and deny the parking tools like the claude template does.
check "pi agent_cmd is pi -p (the real headless verb)" \
    "grep -q 'agent_cmd:.*pi -p.*{prompt}' '$WORK/t-pi/.wf/config.yaml'"
check "pi agent_cmd is NOT the broken pi run" \
    "! grep -q 'agent_cmd:.*pi run' '$WORK/t-pi/.wf/config.yaml'"
check "pi agent_cmd streams json (cost read has a source)" \
    "grep -q \"agent_cmd:.*--mode json\" '$WORK/t-pi/.wf/config.yaml'"
check "pi agent_cmd persists a session for the usage hook" \
    "grep -q 'agent_cmd:.*--session-dir' '$WORK/t-pi/.wf/config.yaml'"
check "pi agent_cmd denies the parking tools" \
    "grep -q 'agent_cmd:.*--exclude-tools.*Monitor,ScheduleWakeup,CronCreate' '$WORK/t-pi/.wf/config.yaml'"

# Two model tiers on claude: the shared template pins the workhorse tier, the
# overrides pin the judgment roles (designer, tl, adequacy) to the stronger one.
check "claude agent_cmd pins sonnet" \
    "grep -q \"agent_cmd:.*--model sonnet.*{prompt}\" '$PROJ/.wf/config.yaml'"
check "claude overrides block present" \
    "grep -q 'agent_cmd_overrides:' '$PROJ/.wf/config.yaml'"
# No wf role calls an MCP server, and a dispatch runs with permissions skipped — an
# inherited github/railway server would put write tools (create_pull_request,
# merge_pull_request, push_files) in reach of a build agent. Loading none also drops
# ~2k of tool listing off every dispatch's prompt.
for R in "agent_cmd" "wf-designer" "wf-adequacy"; do
    check "claude $R loads no MCP servers" \
        "grep -q \"$R:.*--strict-mcp-config\" '$PROJ/.wf/config.yaml'"
done
# A headless dispatch gets one turn. Four dems builds ended theirs on ToolSearch →
# Monitor → "I'll resume when it notifies me", writing no artifact — with the rule
# against it in the skill they had just read. The tools are denied at launch instead.
# `--disallowedTools` is variadic, so the prompt must come BEFORE it or the flag eats it.
for R in "agent_cmd" "wf-designer" "wf-adequacy"; do
    check "claude $R denies the parking tools" \
        "grep -q \"$R:.*{prompt}.*--disallowedTools\" '$PROJ/.wf/config.yaml'"
    check "claude $R denies Monitor and ScheduleWakeup" \
        "grep -q \"$R:.*Monitor,ScheduleWakeup\" '$PROJ/.wf/config.yaml'"
done
for R in wf-designer wf-adequacy; do
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
check "no repo-state when key absent" "! [ -f '$CUSTOM/.wf/wf-repo-state.yaml' ]"
check "no charter when key absent"    "! [ -f '$CUSTOM/.wf/charter.md' ]"
check "no plan when key absent"       "! [ -f '$CUSTOM/.wf/plan.md' ]"
check "no architecture when key absent" "! [ -f '$CUSTOM/.wf/architecture.md' ]"

echo "== migration: counters that still live in config.yaml =="
# An install made before the counters moved carries them in config.yaml and has no
# repo-state file. Scaffolding a zeroed one beside them would hand every lane's next
# mint an id it already used.
MIG="$WORK/t-migrate"; mkdir -p "$MIG"
bash "$SCAFFOLD" --dir "$MIG" --target claude --name mig > "$WORK/mig1.log" 2>&1 \
    || fail "migration setup scaffold failed (see $WORK/mig1.log)"
rm -f "$MIG/.wf/wf-repo-state.yaml"
cat >> "$MIG/.wf/config.yaml" <<'YAML'

id_counters:
  cap: 15
  learning: 137
  wf_learning: 52
  sys_tc: 41
  stage: 4
YAML
bash "$SCAFFOLD" --dir "$MIG" --target claude --name mig > "$WORK/mig2.log" 2>&1 \
    || fail "migrating scaffold exited non-zero (see $WORK/mig2.log)"

check "migrated state file created" "[ -f '$MIG/.wf/wf-repo-state.yaml' ]"
for pair in "cap:15" "learning:137" "wf_learning:52" "sys_tc:41" "stage:4"; do
    k="${pair%%:*}"; v="${pair##*:}"
    check "migrated $k carried over as $v" \
        "grep -qE '^  $k: $v( |$)' '$MIG/.wf/wf-repo-state.yaml'"
done
# Two copies of a counter is worse than one in the wrong file: a role bumps the new one
# and a re-scaffold would read the stale old one back.
check "config's id_counters retired after the move" \
    "! grep -q '^id_counters:' '$MIG/.wf/config.yaml'"
# A repo that already moved must not be re-migrated back to whatever config once held.
echo "  cap: 99" >> "$MIG/.wf/wf-repo-state.yaml"
bash "$SCAFFOLD" --dir "$MIG" --target claude --name mig > "$WORK/mig3.log" 2>&1 \
    || fail "third scaffold exited non-zero (see $WORK/mig3.log)"
check "an already-migrated state file is left alone" \
    "grep -q 'cap: 99' '$MIG/.wf/wf-repo-state.yaml'"

# The real shape of an install made before the move: counters in config.yaml and no
# paths.repo_state key at all, because scaffold never rewrites an existing config. The
# migration has to add the key too, or it silently skips and every counter read dies.
OLD="$WORK/t-oldconfig"; mkdir -p "$OLD"
bash "$SCAFFOLD" --dir "$OLD" --target claude --name old > "$WORK/old1.log" 2>&1 \
    || fail "old-config setup scaffold failed (see $WORK/old1.log)"
rm -f "$OLD/.wf/wf-repo-state.yaml"
python3 - "$OLD/.wf/config.yaml" <<'PY'
import re, sys
p = sys.argv[1]; t = open(p).read()
t = re.sub(r'^\s+repo_state:.*\n', '', t, flags=re.M)   # the key an old config lacks
open(p, 'w').write(t + '\nid_counters:\n  cap: 7\n  sys_tc: 33\n  stage: 2\n')
PY
bash "$SCAFFOLD" --dir "$OLD" --target claude --name old > "$WORK/old2.log" 2>&1 \
    || fail "old-config migration exited non-zero (see $WORK/old2.log)"
check "paths.repo_state added to an old config" \
    "grep -qE '^  repo_state: \"?\.wf/wf-repo-state\.yaml' '$OLD/.wf/config.yaml'"
check "old-config state file created"    "[ -f '$OLD/.wf/wf-repo-state.yaml' ]"
check "old-config cap carried over"      "grep -qE '^  cap: 7( |$)' '$OLD/.wf/wf-repo-state.yaml'"
check "old-config sys_tc carried over"   "grep -qE '^  sys_tc: 33( |$)' '$OLD/.wf/wf-repo-state.yaml'"
check "old-config id_counters retired"   "! grep -q '^id_counters:' '$OLD/.wf/config.yaml'"
# The lanes that config never carried still have to exist, or the first mint dies.
check "absent lanes default to zero"     "grep -qE '^  learning: 0( |$)' '$OLD/.wf/wf-repo-state.yaml' && grep -qE '^  wf_learning: 0( |$)' '$OLD/.wf/wf-repo-state.yaml'"

echo "== bad target rejected =="
if bash "$SCAFFOLD" --dir "$WORK/proj2" --target frobnicate --name x > /dev/null 2>&1; then
    fail "unknown target should exit non-zero"
else
    pass "unknown target rejected"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
