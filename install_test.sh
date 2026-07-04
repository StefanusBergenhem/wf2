#!/usr/bin/env bash
#
# install_test.sh — TDD spec for install.sh target selection.
#
# Verifies precedence: explicit --target wins over everything; without a flag,
# project.target in the target's .wf/config.yaml wins over auto-detect (scalar
# renders one target, an inline list renders them all); with neither, harness
# auto-detect falls back. wf2-source-only — never rendered into an install target.
#
# Run:  bash install_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL="$HERE/install.sh"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# Writes a minimal .wf/config.yaml with the given project.target line.
write_cfg() { # <proj_dir> <target-yaml-value>
    mkdir -p "$1/.wf"
    cat > "$1/.wf/config.yaml" <<YAML
version: 1
project:
  name: "demo"
  target: $2        # trailing comment, like the template renders
paths:
  tools: ".wf/tools"
YAML
}

echo "== explicit --target wins over config =="
P1="$WORK/p1"; write_cfg "$P1" '"pi"'
bash "$INSTALL" --target claude "$P1" > "$WORK/run1.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run1.log)"
check "flag target rendered"        "[ -d '$P1/.claude/skills/wf-init' ]"
check "config target NOT rendered"  "! [ -d '$P1/.pi/skills' ]"

echo "== config scalar target used when no flag =="
P2="$WORK/p2"; write_cfg "$P2" '"pi"'
bash "$INSTALL" "$P2" > "$WORK/run2.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run2.log)"
check "config target rendered"       "[ -d '$P2/.pi/skills/wf-init' ]"
check "auto-detect NOT used instead" "! [ -d '$P2/.claude/skills' ]"

echo "== config list renders every listed target =="
P3="$WORK/p3"; write_cfg "$P3" '["claude", "pi"]'
bash "$INSTALL" "$P3" > "$WORK/run3.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run3.log)"
check "first listed target rendered"  "[ -d '$P3/.claude/skills/wf-init' ]"
check "second listed target rendered" "[ -d '$P3/.pi/skills/wf-init' ]"
check "toolkit copied once"           "[ -d '$P3/.wf/tools/discover' ]"

echo "== no config → auto-detect fallback =="
P4="$WORK/p4"; mkdir -p "$P4"
bash "$INSTALL" "$P4" > "$WORK/run4.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run4.log)"
check "defaults to claude"           "[ -d '$P4/.claude/skills/wf-init' ]"

P5="$WORK/p5"; mkdir -p "$P5/.opencode"
bash "$INSTALL" "$P5" > "$WORK/run5.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run5.log)"
check "detects opencode marker"      "[ -d '$P5/.opencode/skills/wf-init' ]"

echo "== config with no target field → auto-detect fallback =="
P6="$WORK/p6"; mkdir -p "$P6/.wf" "$P6/.pi"
cat > "$P6/.wf/config.yaml" <<'YAML'
version: 1
project:
  name: "demo"
paths:
  tools: ".wf/tools"
YAML
bash "$INSTALL" "$P6" > "$WORK/run6.log" 2>&1 \
    || fail "install exited non-zero (see $WORK/run6.log)"
check "field absent → detected pi"   "[ -d '$P6/.pi/skills/wf-init' ]"

echo "== rendered skills carry no tests =="
check "no *_test.* shipped"          "! find '$P1/.claude/skills' -name '*_test.*' | grep -q ."

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
