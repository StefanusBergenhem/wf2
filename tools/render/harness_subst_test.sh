#!/usr/bin/env bash
#
# harness_subst_test.sh — TDD spec for the harness renderer.
#
# Exercises the two rendering mechanisms (token substitution + wf:if conditional
# blocks) against every supported target, plus the unknown-target error path.
# This test is wf2-source-only — it is never rendered into an install target.
#
# Run:  bash tools/render/harness_subst_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=harness_subst.sh
. "$HERE/harness_subst.sh"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# assert_eq <label> <expected> <actual>
assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  ok: $label"
    else
        echo "  FAIL: $label"
        echo "    expected: [$expected]"
        echo "    actual:   [$actual]"
        FAILS=$((FAILS + 1))
    fi
}

# The fixture exercises: a {{WF_*}} token, a single-target wf:if/else, and a
# comma-list wf:if (claude,opencode share a branch; pi takes the else).
fixture() {
    cat <<'EOF'
Skills install under {{WF_SKILLS_DIR}}.
<!-- wf:if pi -->
PI-ONLY
<!-- wf:else -->
NON-PI
<!-- wf:endif -->
<!-- wf:if claude,opencode -->
CLAUDE-OR-OPENCODE
<!-- wf:else -->
OTHER
<!-- wf:endif -->
Done.
Target={{WF_TARGET}}.
EOF
}

render_for() {
    local target="$1"
    local f="$WORK/$target.md"
    fixture > "$f"
    wf_subst_file "$target" "$f"
    cat "$f"
}

echo "== claude =="
got="$(render_for claude)"
assert_eq "claude: token resolves to .claude/skills" \
    "Skills install under .claude/skills." "$(echo "$got" | sed -n '1p')"
assert_eq "claude: pi block stripped, else kept" "NON-PI" "$(echo "$got" | sed -n '2p')"
assert_eq "claude: comma-list match keeps claude branch" "CLAUDE-OR-OPENCODE" "$(echo "$got" | sed -n '3p')"
assert_eq "claude: WF_TARGET resolves" "Target=claude." "$(echo "$got" | grep '^Target=')"

echo "== pi =="
got="$(render_for pi)"
assert_eq "pi: token resolves to .pi/skills" \
    "Skills install under .pi/skills." "$(echo "$got" | sed -n '1p')"
assert_eq "pi: pi branch kept" "PI-ONLY" "$(echo "$got" | sed -n '2p')"
assert_eq "pi: not in claude,opencode -> else" "OTHER" "$(echo "$got" | sed -n '3p')"
assert_eq "pi: WF_TARGET resolves" "Target=pi." "$(echo "$got" | grep '^Target=')"

echo "== opencode =="
got="$(render_for opencode)"
assert_eq "opencode: token resolves to .opencode/skills" \
    "Skills install under .opencode/skills." "$(echo "$got" | sed -n '1p')"
assert_eq "opencode: pi block stripped, else kept" "NON-PI" "$(echo "$got" | sed -n '2p')"
assert_eq "opencode: comma-list match keeps opencode branch" "CLAUDE-OR-OPENCODE" "$(echo "$got" | sed -n '3p')"
assert_eq "opencode: WF_TARGET resolves" "Target=opencode." "$(echo "$got" | grep '^Target=')"

echo "== unknown target =="
badf="$WORK/bad.md"; fixture > "$badf"
if wf_subst_file frobnicate "$badf" 2>/dev/null; then
    echo "  FAIL: unknown target should return non-zero"; FAILS=$((FAILS + 1))
else
    echo "  ok: unknown target returns non-zero"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then
    echo "ALL GREEN"; exit 0
else
    echo "$FAILS FAILURE(S)"; exit 1
fi
