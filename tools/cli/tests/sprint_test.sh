#!/usr/bin/env bash
#
# Tests for `wf sprint task` — the task-contract extractor.
# Run: bash tools/cli/tests/sprint_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CLI/../.." && pwd)"
WF="$CLI/wf"
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; echo "         $2"; }
jget() { "$PYTHON" -c 'import sys,json; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf/transient"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/sprint.yaml"
YAML
cat > "$PROJ/.wf/sprint.yaml" <<'YAML'
tasks:
  - id: T1
    title: "First task"
    component: "core"
    files_to_touch: ["a.go"]
  - id: T2
    title: "Second task"
    depends_on: [T1]
YAML
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

# emit a single task's contract to stdout
OUT="$(wf sprint task T1 --format json)"
[ "$(jget "$OUT" "d['id']")" = "T1" ] && ok "sprint task emits the requested task" || bad "emit id" "$OUT"
[ "$(jget "$OUT" "d['files_to_touch']")" = "['a.go']" ] && ok "sprint task carries the full contract" || bad "emit contract" "$OUT"

# --write drops the contract at the given path (the orchestrator's current_task)
DEST="$PROJ/.wf/transient/current-task.yaml"
WR="$(wf sprint task T2 --write "$DEST" --format json)"
[ "$(jget "$WR" "d['written']")" = "$DEST" ] && ok "sprint task --write reports the path" || bad "write report" "$WR"
[ -f "$DEST" ] && ok "sprint task --write creates the file" || bad "write file" "missing"
grep -q "Second task" "$DEST" && ok "written contract holds the task body" || bad "write body" "$(cat "$DEST")"

# unknown task → non-zero exit
if wf sprint task NOPE >/dev/null 2>&1; then bad "unknown task should fail" "exited 0"; else ok "sprint task: unknown id → non-zero exit"; fi

echo ""
echo "  sprint extractor: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
