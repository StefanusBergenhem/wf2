#!/usr/bin/env bash
#
# scaffold.sh — bootstrap a target project's .wf/ workspace.
#
# Idempotent: creates .wf/config.yaml (from the template asset, tokens resolved),
# the .wf/transient/ output dir, and a .gitignore entry for transient output.
# Re-running never clobbers an existing config nor duplicates the gitignore line.
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
CONFIG="$WF_DIR/config.yaml"
TRANSIENT="$WF_DIR/transient"
TELEMETRY="$WF_DIR/telemetry/sessions.jsonl"   # default; matches paths.telemetry in the template
GITIGNORE="$DIR/.gitignore"

mkdir -p "$TRANSIENT"
# .gitkeep so the empty transient dir survives a fresh clone before any run.
: > "$TRANSIENT/.gitkeep"

# Telemetry sink: a tracked, append-only log. Create it empty once; never clobber
# an existing log (the recorder also creates it lazily, but a fresh init should
# leave a committed, present sink).
mkdir -p "$(dirname "$TELEMETRY")"
[ -f "$TELEMETRY" ] || : > "$TELEMETRY"

# Config: write once. An existing config is the user's — never overwrite it.
if [ -f "$CONFIG" ]; then
    echo "  kept existing $CONFIG"
else
    sed -e "s|{{PROJECT_NAME}}|$NAME|g" \
        -e "s|{{TARGET}}|$TARGET|g" \
        "$TEMPLATE" > "$CONFIG"
    echo "  wrote $CONFIG"
fi

# Gitignore: ensure transient output is ignored, exactly once.
IGNORE_LINE=".wf/transient/"
if [ -f "$GITIGNORE" ] && grep -qxF "$IGNORE_LINE" "$GITIGNORE"; then
    echo "  gitignore already ignores $IGNORE_LINE"
else
    { [ -f "$GITIGNORE" ] && [ -s "$GITIGNORE" ] && echo ""; \
      echo "# wf2 transient output (derived, disposable)"; \
      echo "$IGNORE_LINE"; } >> "$GITIGNORE"
    echo "  added $IGNORE_LINE to .gitignore"
fi
