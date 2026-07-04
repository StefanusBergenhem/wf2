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
#     When --target is omitted, project.target in the target's .wf/config.yaml
#     wins (a scalar renders one target; an inline list, e.g.
#     target: ["claude", "pi"], renders them all — the re-install path).
#     With no config either, auto-detect: .opencode/ or opencode.json → opencode;
#     .pi/ → pi; otherwise claude.

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

# When --target is omitted, an installed project's config wins over auto-detect:
# read project.target from the target's .wf/config.yaml — a scalar or an inline
# list (a list means render every listed target). Mirrors scaffold.sh's cfg_path
# awk, scoped to the project: block; prints the target(s) space-separated.
cfg_project_target() {
    awk '
        /^project:/     { inp = 1; next }
        /^[^[:space:]]/ { inp = 0 }
        inp && $1 == "target:" {
            line = $0
            sub(/^[[:space:]]*target:[[:space:]]*/, "", line)
            sub(/#.*$/, "", line)
            gsub(/[][",]/, " ", line)
            gsub(/[[:space:]]+/, " ", line)
            sub(/^ /, "", line); sub(/ $/, "", line)
            print line; exit
        }
    ' "$1"
}
if [ -z "$TARGET" ] && [ -f "$TARGET_DIR/.wf/config.yaml" ]; then
    TARGET="$(cfg_project_target "$TARGET_DIR/.wf/config.yaml")"
fi

# Auto-detect the harness when neither --target nor config decided.
if [ -z "$TARGET" ]; then
    if [ -d "$TARGET_DIR/.opencode" ] || [ -f "$TARGET_DIR/opencode.json" ] || [ -f "$TARGET_DIR/opencode.jsonc" ]; then
        TARGET="opencode"
    elif [ -d "$TARGET_DIR/.pi" ]; then
        TARGET="pi"
    else
        TARGET="claude"
    fi
fi

# Validate every requested target ($TARGET may hold several, space-separated).
TARGETS=()
for t in $TARGET; do
    case "$t" in
        claude|pi|opencode) TARGETS+=("$t") ;;
        *) echo "error: unknown target '$t' (expected: claude | pi | opencode)" >&2; exit 2 ;;
    esac
done

harness_dir() {
    case "$1" in
        claude)   echo "$TARGET_DIR/.claude" ;;
        pi)       echo "$TARGET_DIR/.pi" ;;
        opencode) echo "$TARGET_DIR/.opencode" ;;
    esac
}

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
echo "  harness:    ${TARGETS[*]}"
echo ""

# [1/3] + [2/3] run once per target; [3/3] (the .wf/tools copy) runs once.
for T in "${TARGETS[@]}"; do
    HARNESS_DIR="$(harness_dir "$T")"

    # [1/3] Render skills for this harness.
    SKILLS_DIR="$HARNESS_DIR/skills"
    mkdir -p "$SKILLS_DIR"
    echo "[1/3] Rendering skills ($T)..."
    for skill_dir in "$WF_DIR"/skills/*/; do
        skill_name="$(basename "$skill_dir")"
        dest="$SKILLS_DIR/$skill_name"
        rm -rf "$dest"
        cp -R "$skill_dir" "$dest"
        strip_tests "$dest"
        while IFS= read -r -d '' md; do
            wf_subst_file "$T" "$md"
        done < <(find "$dest" -type f -name '*.md' -print0)
        echo "  rendered: $skill_name"
    done

    # [2/3] Render agents for this harness. Agents are single .md files
    # (frontmatter + system prompt), token-substituted like skills — no test stripping
    # (agents carry no scripts). Dispatched by name from the planning skills.
    if [ -d "$WF_DIR/agents" ]; then
        AGENTS_DIR="$HARNESS_DIR/agents"
        mkdir -p "$AGENTS_DIR"
        echo ""
        echo "[2/3] Rendering agents ($T)..."
        for agent_file in "$WF_DIR"/agents/*.md; do
            [ -e "$agent_file" ] || continue
            dest="$AGENTS_DIR/$(basename "$agent_file")"
            cp "$agent_file" "$dest"
            wf_subst_file "$T" "$dest"
            echo "  rendered: $(basename "$agent_file" .md)"
        done
    fi
    echo ""
done

# [3/3] Copy user-facing toolkit machinery into .wf/tools/ (git-tracked).
# Excludes the install-time render/ lib and dev cruft (venv, caches, build
# artifacts, node_modules) — those are regenerated per checkout. Deliberately-
# vendored offline libs (the self-contained-view graph lib) are KEPT — they must
# ship for the rendered HTML views to work without internet.
echo "[3/3] Installing toolkit machinery into .wf/tools/..."
WF_TOOLS_DEST="$TARGET_DIR/.wf/tools"
rm -rf "$WF_TOOLS_DEST"
mkdir -p "$WF_TOOLS_DEST"
for tool_dir in "$WF_DIR"/tools/*/; do
    name="$(basename "$tool_dir")"
    [ "$name" = "render" ] && continue   # install-time only; never shipped
    cp -R "$tool_dir" "$WF_TOOLS_DEST/$name"
done
strip_tests "$WF_TOOLS_DEST"
# Prune dev cruft that may exist in the source tree. NOTE: 'vendor' is deliberately
# NOT pruned — the only vendor/ dirs hold the committed offline graph lib, which must
# ship. Add a narrower guard here only if a regenerable vendor/ dir ever appears.
find "$WF_TOOLS_DEST" -type d \( -name '.venv' -o -name '__pycache__' -o -name 'node_modules' \
    -o -name 'dist' \) -prune -exec rm -rf {} + 2>/dev/null || true
echo "  installed: .wf/tools/ (toolkit machinery)"

echo ""
echo "=== Done ==="
for T in "${TARGETS[@]}"; do
    echo "Skills rendered for: $T (under $(harness_dir "$T")/skills/)."
done
echo "Next: run /wf-init to scaffold .wf/config.yaml + .wf/transient/."
