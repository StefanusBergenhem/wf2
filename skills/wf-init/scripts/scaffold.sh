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

# The headless launch commands the driver runs, per harness: AGENT_CMD is the workhorse
# tier every role gets by default; AGENT_CMD_STRONG is the stronger tier the template's
# agent_cmd_overrides pins for the judgment roles. `{prompt}` is the DRIVER's
# substitution — it must reach the config verbatim.
#
# The claude tier streams (`--output-format stream-json --verbose`) so the dispatch log
# under <paths.transient>/driver-logs/ fills as the role works. Plain `-p` writes
# nothing until the process exits, which leaves an hour-long dispatch with an empty log
# and no way to tell a working role from a wedged one. The driver never reads the log,
# so the format is free to be whatever a human can follow: `tail -f … | jq`.
#
# `--disallowedTools` takes the wait-for-a-later-turn tools away. A dispatch gets ONE
# turn, so a role that arms one of them ends its session with nothing written and the
# whole cycle is spent again — four dems builds went that way, each reaching the tool
# through ToolSearch, each having just read the skill that forbids it. Bash is untouched:
# a gate run in the FOREGROUND is exactly what this pushes the role back to. The flag is
# variadic, so `"{prompt}"` must stay ahead of it or it is swallowed as another tool name.
#
# `--strict-mcp-config` loads no MCP server at all. No wf role calls one, and a dispatch
# runs with permissions skipped, so a server the user happens to have configured puts its
# write tools in a build agent's reach — a dems dispatch carried github's
# create_pull_request/merge_pull_request/push_files that way. Dropping them also takes
# ~2k off every dispatch's prompt (measured: 33.3k → 31.2k, 147 tools → 27).
case "$TARGET" in
    claude)   AGENT_CMD='claude -p --output-format stream-json --verbose --dangerously-skip-permissions --strict-mcp-config --model sonnet "{prompt}" --disallowedTools "Monitor,ScheduleWakeup,CronCreate"'
              AGENT_CMD_STRONG='claude -p --output-format stream-json --verbose --dangerously-skip-permissions --strict-mcp-config --model opus "{prompt}" --disallowedTools "Monitor,ScheduleWakeup,CronCreate"' ;;
    # TODO: opencode/pi model flags are unverified — both tiers render the harness
    # default; pin models in agent_cmd/agent_cmd_overrides after the first run.
    opencode) AGENT_CMD='opencode run "{prompt}"'
              AGENT_CMD_STRONG='opencode run "{prompt}"' ;;
    # pi's headless prompt command is `pi -p` (NOT `pi run`, which reads no prompt and
    # exits having done nothing). `--mode json` makes the log carry per-request usage
    # and cost so the driver's cost read and the pi usage hook have a source;
    # `--session-dir` persists the session to the project's own transient dir (not the
    # user's ~/.pi store) so the pi usage hook can read it and sweep it with the rest
    # of the transient tree; `--exclude-tools` denies the parking tools the claude
    # template denies. Verified headlessly end-to-end.
    pi)       AGENT_CMD="pi -p --mode json --session-dir \"$DIR/.wf/transient/pi-sessions\" --exclude-tools \"Monitor,ScheduleWakeup,CronCreate\" \"{prompt}\""
              AGENT_CMD_STRONG="$AGENT_CMD" ;;
esac

# Config: write once from the template. An existing config is the user's — never
# overwrite it.
if [ -f "$CONFIG" ]; then
    echo "  kept existing $CONFIG"
else
    sed -e "s|{{PROJECT_NAME}}|$NAME|g" \
        -e "s|{{TARGET}}|$TARGET|g" \
        -e "s|{{AGENT_CMD}}|$AGENT_CMD|g" \
        -e "s|{{AGENT_CMD_STRONG}}|$AGENT_CMD_STRONG|g" \
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
# The id high-water marks used to live in config.yaml. An install made before they moved
# still carries them there, so scaffolding a zeroed state file beside them would hand
# every lane's next mint an id it has already used. Carry the values across first, then
# retire the old block — two copies is worse than one in the wrong place, because a role
# bumps the new one while a later read finds the stale one.
migrate_counters() {
    grep -q '^id_counters:' "$CONFIG" || return 0       # nothing to carry
    local rel; rel="$(cfg_path repo_state)"
    if [ -z "$rel" ]; then
        # An install made before the key existed. scaffold never rewrites an existing
        # config, so without adding it here the migration would skip and every counter
        # read would then die on the missing key.
        rel=".wf/wf-repo-state.yaml"
        awk -v line="  repo_state: \"$rel\"" '
            { print }
            /^paths:/ && !done { print line; done = 1 }
        ' "$CONFIG" > "$CONFIG.keyed" && mv "$CONFIG.keyed" "$CONFIG"
        echo "  added paths.repo_state: $rel"
    fi
    [ -f "$DIR/$rel" ] && return 0                      # already moved
    mkdir -p "$(dirname "$DIR/$rel")"
    {
        echo "# wf repo state — the id high-water marks, migrated from .wf/config.yaml."
        echo "# Each counter only ever INCREASES. Committed."
        echo "version: 1"
        echo ""
        echo "id_counters:"
        # Every lane the template carries is emitted, at the value config held or 0 —
        # a lane that predates a config is still a lane the next mint reads.
        awk '
            /^id_counters:/ { inc = 1; next }
            /^[^[:space:]]/ { inc = 0 }
            inc && match($0, /^[[:space:]]+[a-z_]+:[[:space:]]*[0-9]+/) {
                key = $1; sub(/:$/, "", key)
                val = $2; sub(/[^0-9].*$/, "", val)
                seen[key] = val
            }
            END {
                n = split("cap learning wf_learning sys_tc stage", lanes, " ")
                for (i = 1; i <= n; i++)
                    printf "  %s: %s\n", lanes[i],
                           (lanes[i] in seen) ? seen[lanes[i]] : 0
            }
        ' "$CONFIG"
    } > "$DIR/$rel"
    # Retire the old block, its comment lines, and the blank line left behind.
    awk '
        /^id_counters:/ { inc = 1; next }
        /^[^[:space:]]/ { inc = 0 }
        inc           { next }
        { print }
    ' "$CONFIG" > "$CONFIG.migrated" && mv "$CONFIG.migrated" "$CONFIG"
    echo "  migrated id_counters into $rel and retired them from the config"
}
migrate_counters

scaffold_home repo_state      "../assets/wf-repo-state.yaml.tmpl"                   "repo-state home"
scaffold_home capabilities    "../../wf-po/assets/capabilities.yaml.tmpl"            "capabilities home"
scaffold_home charter         "../../wf-sa/assets/charter.md.tmpl"                            "charter home"
scaffold_home plan            "../../wf-designer/assets/plan.md.tmpl"                               "plan home"
scaffold_home architecture    "../../wf-sa/assets/architecture.md.tmpl"                  "architecture home"
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

# Gitignore the toolkit's Python bytecode, exactly once — a stray __pycache__
# under .wf/tools/ dirties the tree and trips the driver's clean-tree gate.
TOOLS_REL="$(cfg_path tools)"
if [ -n "$TOOLS_REL" ]; then
    PYCACHE_LINE="$TOOLS_REL/**/__pycache__/"
    if [ -f "$GITIGNORE" ] && grep -qxF "$PYCACHE_LINE" "$GITIGNORE"; then
        echo "  gitignore already ignores $PYCACHE_LINE"
    else
        { echo "$PYCACHE_LINE"; } >> "$GITIGNORE"
        echo "  added $PYCACHE_LINE to .gitignore"
    fi
fi

# Gitignore the session log's directory, exactly once. The log is a per-cycle working
# buffer, not source: every dispatch appends to it, so committing it dirties the tree
# under the driver's own clean-tree gate. The durable record is the drain's
# paths.archive snapshot, which IS committed (L-137).
TELEMETRY_DIR_REL="$(dirname "$TELEMETRY_REL")/"
if [ "$TELEMETRY_DIR_REL" != "./" ]; then
    if [ -f "$GITIGNORE" ] && grep -qxF "$TELEMETRY_DIR_REL" "$GITIGNORE"; then
        echo "  gitignore already ignores $TELEMETRY_DIR_REL"
    else
        { echo "# wf session log (per-cycle buffer; the archive snapshot is the record)"; \
          echo "$TELEMETRY_DIR_REL"; } >> "$GITIGNORE"
        echo "  added $TELEMETRY_DIR_REL to .gitignore"
    fi
fi
