#!/usr/bin/env bash
set -euo pipefail

# wf2 — Project Installer (bare-bones)
#
# Installs the wf2 toolkit into a target project for one agent harness:
#   claude   → .claude/skills/      (Claude Code)
#   pi       → .pi/skills/          (pi)
#   opencode → .opencode/skills/    (opencode)
#
# Two things happen:
#   1. Skills are RENDERED per target — each skill dir is copied whole and its
#      *.md token-substituted (harness_subst.sh) so the installed file is
#      single-target with no runtime branching. Test files (*_test.*) are stripped
#      on the way in: only the script ships, never its tests.
#   2. The user-facing toolkit machinery (tools/, minus the install-time render/
#      lib and dev cruft) is copied to .wf/tools/, git-tracked so it rides into
#      worktrees.
#
# Config + .wf/ workspace scaffolding is NOT done here — run /wf-init after.
#
# Usage:
#   ./install.sh [--target claude|pi|opencode] [target_project_dir]
#     target_project_dir defaults to the current working directory.
#     --target auto-detects when omitted: .opencode/ or opencode.json → opencode;
#              .pi/ → pi; otherwise claude.

WF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TARGET=""
POS_DIR=""
while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="${2:-}"; shift 2 ;;
        --target=*) TARGET="${1#*=}"; shift ;;
        -h|--help) sed -n '/^# wf2 — Project Installer/,/^WF_DIR=/p' "$0" | sed '$d'; exit 0 ;;
        -*) echo "error: unknown flag '$1'" >&2; exit 2 ;;
        *) POS_DIR="$1"; shift ;;
    esac
done

TARGET_DIR="$(cd "${POS_DIR:-$PWD}" && pwd)"

# Auto-detect the harness when --target is omitted.
if [ -z "$TARGET" ]; then
    if [ -d "$TARGET_DIR/.opencode" ] || [ -f "$TARGET_DIR/opencode.json" ] || [ -f "$TARGET_DIR/opencode.jsonc" ]; then
        TARGET="opencode"
    elif [ -d "$TARGET_DIR/.pi" ]; then
        TARGET="pi"
    else
        TARGET="claude"
    fi
fi

case "$TARGET" in
    claude)   HARNESS_DIR="$TARGET_DIR/.claude" ;;
    pi)       HARNESS_DIR="$TARGET_DIR/.pi" ;;
    opencode) HARNESS_DIR="$TARGET_DIR/.opencode" ;;
    *) echo "error: unknown target '$TARGET' (expected: claude | pi | opencode)" >&2; exit 2 ;;
esac

# The one place harness-specific names live: provides wf_subst_file <target> <file>.
# shellcheck source=tools/render/harness_subst.sh
. "$WF_DIR/tools/render/harness_subst.sh"

# Test files never ship into a target (TDD rule: render the script, not its test).
strip_tests() {
    find "$1" -type f \( -name '*_test.sh' -o -name '*_test.py' \
        -o -name 'test_*.sh' -o -name 'test_*.py' \) -delete 2>/dev/null || true
    find "$1" -type d -name 'tests' -prune -exec rm -rf {} + 2>/dev/null || true
}

echo "=== wf2 — Project Installer ==="
echo "  wf2 source: $WF_DIR"
echo "  target:     $TARGET_DIR"
echo "  harness:    $TARGET"
echo ""

# [1/2] Render skills for the chosen harness.
SKILLS_DIR="$HARNESS_DIR/skills"
mkdir -p "$SKILLS_DIR"
echo "[1/2] Rendering skills ($TARGET)..."
for skill_dir in "$WF_DIR"/skills/*/; do
    skill_name="$(basename "$skill_dir")"
    dest="$SKILLS_DIR/$skill_name"
    rm -rf "$dest"
    cp -R "$skill_dir" "$dest"
    strip_tests "$dest"
    while IFS= read -r -d '' md; do
        wf_subst_file "$TARGET" "$md"
    done < <(find "$dest" -type f -name '*.md' -print0)
    echo "  rendered: $skill_name"
done

# [2/2] Copy user-facing toolkit machinery into .wf/tools/ (git-tracked).
# Excludes the install-time render/ lib and dev cruft (venv, caches, build
# artifacts, vendored libs, node_modules) — those are regenerated per checkout.
echo ""
echo "[2/2] Installing toolkit machinery into .wf/tools/..."
WF_TOOLS_DEST="$TARGET_DIR/.wf/tools"
rm -rf "$WF_TOOLS_DEST"
mkdir -p "$WF_TOOLS_DEST"
for tool_dir in "$WF_DIR"/tools/*/; do
    name="$(basename "$tool_dir")"
    [ "$name" = "render" ] && continue   # install-time only; never shipped
    cp -R "$tool_dir" "$WF_TOOLS_DEST/$name"
done
strip_tests "$WF_TOOLS_DEST"
# Prune dev cruft that may exist in the source tree.
find "$WF_TOOLS_DEST" -type d \( -name '.venv' -o -name '__pycache__' -o -name 'node_modules' \
    -o -name 'vendor' -o -name 'dist' \) -prune -exec rm -rf {} + 2>/dev/null || true
echo "  installed: .wf/tools/ (toolkit machinery)"

echo ""
echo "=== Done ==="
echo "Skills rendered for: $TARGET (under $HARNESS_DIR/skills/)."
echo "Next: run /wf-init to scaffold .wf/config.yaml + .wf/transient/."
