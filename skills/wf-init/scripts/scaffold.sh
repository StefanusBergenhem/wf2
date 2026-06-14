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

# Config: write once from the template. An existing config is the user's — never
# overwrite it.
if [ -f "$CONFIG" ]; then
    echo "  kept existing $CONFIG"
else
    sed -e "s|{{PROJECT_NAME}}|$NAME|g" \
        -e "s|{{TARGET}}|$TARGET|g" \
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
mkdir -p "$DIR/$TRANSIENT_REL"

# Telemetry sink: a tracked, append-only log. Create it empty once; never clobber
# an existing log (the recorder also creates it lazily, but a fresh init should
# leave a committed, present sink).
mkdir -p "$(dirname "$DIR/$TELEMETRY_REL")"
[ -f "$DIR/$TELEMETRY_REL" ] || : > "$DIR/$TELEMETRY_REL"

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
