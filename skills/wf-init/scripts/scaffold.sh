#!/usr/bin/env bash
#
# scaffold.sh — bootstrap a target project's .wf/ workspace.
#
# Writes .wf/config.yaml from the template, then creates the transient dir and the
# telemetry sink AT THE PATHS CONFIG DEFINES (config is the single source of truth —
# the only path hard-coded here is .wf/config.yaml, the bootstrap anchor).
# Idempotent: never clobbers an existing config/log nor duplicates the gitignore line.
#
# Usage:
#   scaffold.sh --target <claude|pi|opencode> [--name <name>] [--dir <project_dir>]
#     --dir   defaults to the current working directory.
#     --name  defaults to the basename of --dir.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$HERE/../assets/config.yaml.tmpl"

TARGET=""
NAME=""
DIR="$PWD"
while [ $# -gt 0 ]; do
    case "$1" in
        --target) TARGET="${2:-}"; shift 2 ;;
        --name)   NAME="${2:-}"; shift 2 ;;
        --dir)    DIR="${2:-}"; shift 2 ;;
        *) echo "scaffold: unknown arg '$1'" >&2; exit 2 ;;
    esac
done

case "$TARGET" in
    claude|pi|opencode) ;;
    "") echo "scaffold: --target is required (claude|pi|opencode)" >&2; exit 2 ;;
    *)  echo "scaffold: unknown target '$TARGET' (expected claude|pi|opencode)" >&2; exit 2 ;;
esac
[ -f "$TEMPLATE" ] || { echo "scaffold: missing template: $TEMPLATE" >&2; exit 2; }

mkdir -p "$DIR"
DIR="$(cd "$DIR" && pwd)"
[ -n "$NAME" ] || NAME="$(basename "$DIR")"

WF_DIR="$DIR/.wf"
CONFIG="$WF_DIR/config.yaml"     # the bootstrap anchor — the only path scaffold hard-codes
GITIGNORE="$DIR/.gitignore"

mkdir -p "$WF_DIR"

# The headless launch command the driver runs for every role, per harness. `{prompt}` is
# the DRIVER's substitution — it must reach the config verbatim.
case "$TARGET" in
    claude)   AGENT_CMD='claude -p --dangerously-skip-permissions "{prompt}"' ;;
    opencode) AGENT_CMD='opencode run "{prompt}"' ;;
    # TODO: pi's headless invocation is unverified — confirm it before the first driver run.
    pi)       AGENT_CMD='pi run "{prompt}"' ;;
esac

# Config: write once from the template. An existing config is the user's — never
# overwrite it.
if [ -f "$CONFIG" ]; then
    echo "  kept existing $CONFIG"
else
    sed -e "s|{{PROJECT_NAME}}|$NAME|g" \
        -e "s|{{TARGET}}|$TARGET|g" \
        -e "s|{{AGENT_CMD}}|$AGENT_CMD|g" \
        "$TEMPLATE" > "$CONFIG"
    echo "  wrote $CONFIG"
fi

# Resolve workspace paths FROM the config we just wrote — config is the single
# source of truth; scaffold never restates a default the template already owns.
# cfg_path reads paths.<key> from the paths: block (flat, double-quoted values).
cfg_path() {
    awk -v k="$1" '
        /^paths:/       { inp = 1; next }
        /^[^[:space:]]/ { inp = 0 }
        inp && $1 == k":" { v = $2; gsub(/"/, "", v); print v; exit }
    ' "$CONFIG"
}
TRANSIENT_REL="$(cfg_path transient)"
TELEMETRY_REL="$(cfg_path telemetry)"
[ -n "$TRANSIENT_REL" ] || { echo "scaffold: paths.transient missing from $CONFIG" >&2; exit 2; }
[ -n "$TELEMETRY_REL" ] || { echo "scaffold: paths.telemetry missing from $CONFIG" >&2; exit 2; }

# Transient output dir (skills also recreate it on demand).
if [ -d "$DIR/$TRANSIENT_REL" ]; then
    echo "  transient dir already present: $TRANSIENT_REL"
else
    mkdir -p "$DIR/$TRANSIENT_REL"
    echo "  created transient dir: $TRANSIENT_REL"
fi

# Telemetry sink: a tracked, append-only log. Create it empty once; never clobber
# an existing log (the recorder also creates it lazily, but a fresh init should
# leave a committed, present sink).
mkdir -p "$(dirname "$DIR/$TELEMETRY_REL")"
if [ -f "$DIR/$TELEMETRY_REL" ]; then
    echo "  telemetry sink already present: $TELEMETRY_REL"
else
    : > "$DIR/$TELEMETRY_REL"
    echo "  created telemetry sink: $TELEMETRY_REL"
fi

# Durable homes the planning roles read/write. init is the setup wizard: it creates
# every committed home here so no role faces a missing file. Each home is instantiated
# from the template that defines its shape — a sibling-skill reach stable in both the
# source and the rendered install layout. A home whose config key is absent (a trimmed
# custom config) is simply skipped.
ADRS_REL="$(cfg_path adrs)"
if [ -n "$ADRS_REL" ]; then
    if [ -d "$DIR/$ADRS_REL" ]; then
        echo "  adrs dir already present: $ADRS_REL"
    else
        mkdir -p "$DIR/$ADRS_REL"
        : > "$DIR/$ADRS_REL/.gitkeep"   # committed dir needs a tracked placeholder until the first ADR
        echo "  created adrs dir: $ADRS_REL"
    fi
fi

# scaffold_home <config-key> <template-path-relative-to-this-script> <label>
scaffold_home() {
    local rel; rel="$(cfg_path "$1")"
    local tmpl="$HERE/$2" label="$3"
    [ -n "$rel" ] || return 0
    if [ -f "$DIR/$rel" ]; then
        echo "  $label already present: $rel"
    elif [ -f "$tmpl" ]; then
        mkdir -p "$(dirname "$DIR/$rel")"
        cp "$tmpl" "$DIR/$rel"
        echo "  created $label: $rel"
    else
        echo "  WARN: $label template missing ($tmpl) — skipped $rel" >&2
    fi
}
scaffold_home capabilities    "../../wf-po/assets/capabilities.yaml.tmpl"            "capabilities home"
scaffold_home charter         "../../wf-sa/assets/charter.md.tmpl"                            "charter home"
scaffold_home plan            "../../wf-designer/assets/plan.md.tmpl"                               "plan home"
scaffold_home learnings       "../../wf-retrospective/assets/learnings.yaml.tmpl"    "learnings home"
scaffold_home wf_learnings    "../../wf-retrospective/assets/wf-learnings.yaml.tmpl" "wf-learnings home"

# Gitignore the transient output, exactly once.
IGNORE_LINE="$TRANSIENT_REL/"
if [ -f "$GITIGNORE" ] && grep -qxF "$IGNORE_LINE" "$GITIGNORE"; then
    echo "  gitignore already ignores $IGNORE_LINE"
else
    { [ -f "$GITIGNORE" ] && [ -s "$GITIGNORE" ] && echo ""; \
      echo "# wf2 transient output (derived, disposable)"; \
      echo "$IGNORE_LINE"; } >> "$GITIGNORE"
    echo "  added $IGNORE_LINE to .gitignore"
fi
