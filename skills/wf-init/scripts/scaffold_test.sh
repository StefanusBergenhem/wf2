#!/usr/bin/env bash
#
# scaffold_test.sh — TDD spec for scaffold.sh.
#
# Verifies: config written from template with tokens resolved, .wf/transient/
# created, gitignore updated, and full idempotency (re-run does not clobber an
# edited config nor duplicate the gitignore line).
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

echo "== idempotency =="
# Mark the config as user-edited and append a telemetry line, then re-run.
echo "# user edit" >> "$PROJ/.wf/config.yaml"
echo '{"agent":"prior"}' >> "$PROJ/.wf/telemetry/sessions.jsonl"
bash "$SCAFFOLD" --dir "$PROJ" --target claude --name demo > "$WORK/run2.log" 2>&1 \
    || fail "second scaffold exited non-zero (see $WORK/run2.log)"

check "existing config not clobbered" "grep -q '# user edit' '$PROJ/.wf/config.yaml'"
check "telemetry sink not clobbered" "grep -q 'prior' '$PROJ/.wf/telemetry/sessions.jsonl'"
gi_count="$(grep -cF '.wf/transient/' "$PROJ/.gitignore")"
check "gitignore line not duplicated" "[ '$gi_count' -eq 1 ]"

echo "== bad target rejected =="
if bash "$SCAFFOLD" --dir "$WORK/proj2" --target frobnicate --name x > /dev/null 2>&1; then
    fail "unknown target should exit non-zero"
else
    pass "unknown target rejected"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
