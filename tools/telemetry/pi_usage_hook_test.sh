#!/usr/bin/env bash
#
# pi_usage_hook_test.sh — TDD spec for pi_usage_hook.py.
#
# Verifies: a pi session JSONL file yields a `kind: usage` row in exactly the shape
# the claude Stop hook writes (so `wf telemetry roles` consumes it unchanged); the
# numbers are summed/deduped correctly across the stream's repeated message shapes;
# a `--session-id` filter emits only that dispatch's session; a child session whose
# header carries `parentSession` emits a `SubagentStop` row; and a failure is swallowed
# (the script must never break the run). Stdlib only.
# wf2-source-only — never rendered into an install target.
#
# Run:  bash tools/telemetry/pi_usage_hook_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOOK="$HERE/pi_usage_hook.py"
PYTHON="${PYTHON:-$(command -v python3)}"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

# row_has <sink> <python-condition>: true when any JSON row in the sink satisfies the
# python expression on the parsed row `r` (handles the spaced json.dumps output).
row_has() { # <sink> <cond>
    "$PYTHON" - "$1" "$2" <<'PY'
import json, sys
sink, cond = sys.argv[1], sys.argv[2]
with open(sink) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if eval(cond):
            sys.exit(0)
sys.exit(1)
PY
}
count_field() { # <sink> <hook_event> -> count of rows with that hook_event
    "$PYTHON" - "$1" "$2" <<'PY'
import json, sys
sink, ev = sys.argv[1], sys.argv[2]
n = 0
with open(sink) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("hook_event") == ev:
            n += 1
print(n)
PY
}

SESS="$WORK/sessions"
mkdir -p "$SESS"

# A minimal pi session file with two assistant requests. r2 is repeated the way the
# json stream repeats the same assistant message on turn_end — must sum once.
TOP_ID="aaaa-bbbb-cccc"
cat > "$SESS/top.jsonl" <<EOF
{"type":"session","version":3,"id":"$TOP_ID","timestamp":"2026-08-10T12:00:00.000Z","cwd":"/proj"}
{"type":"message","id":"e1","timestamp":"2026-08-10T12:00:01.000Z","message":{"role":"user","content":[{"type":"text","text":"build"}]}}
{"type":"message","id":"e2","timestamp":"2026-08-10T12:00:02.000Z","message":{"role":"assistant","content":[{"type":"toolCall","id":"call-1","name":"bash","arguments":{"command":"go test"}}],"usage":{"input":100,"output":20,"cacheRead":500,"cacheWrite":0,"totalTokens":620,"cost":{"input":0.001,"output":0.002,"cacheRead":0.003,"cacheWrite":0,"total":0.006}},"responseId":"r1"}}
{"type":"message","id":"e3","timestamp":"2026-08-10T12:00:03.000Z","message":{"role":"toolResult","toolCallId":"call-1","toolName":"bash","content":[{"type":"text","text":"ok"}]}}
{"type":"message","id":"e4","timestamp":"2026-08-10T12:00:04.000Z","message":{"role":"assistant","content":[{"type":"text","text":"done"}],"usage":{"input":50,"output":10,"cacheRead":200,"cacheWrite":0,"totalTokens":260,"cost":{"input":0.0005,"output":0.001,"cacheRead":0.001,"cacheWrite":0,"total":0.0025}},"responseId":"r2"}}
{"type":"message","id":"e5","timestamp":"2026-08-10T12:00:04.000Z","message":{"role":"assistant","content":[{"type":"text","text":"done"}],"usage":{"input":50,"output":10,"cacheRead":200,"cacheWrite":0,"totalTokens":260,"cost":{"input":0.0005,"output":0.001,"cacheRead":0.001,"cacheWrite":0,"total":0.0025}},"responseId":"r2"}}
EOF
# Expected: input 150, output 30, cache_read 700, cache_creation 0,
# context_max = max(100+500+0, 50+200+0) = 600, requests 2, tool_calls 1.

SINK="$WORK/sink.jsonl"
echo "== a pi session emits one usage row in the claude-hook shape =="
"$PYTHON" "$HOOK" --session-dir "$SESS" --session-id "$TOP_ID" --sink "$SINK"
check "one row appended"            "[ \"\$(wc -l < '$SINK')\" = 1 ]"
check "kind is usage"               "row_has '$SINK' 'r[\"kind\"]==\"usage\"'"
check "hook_event is Stop"          "row_has '$SINK' 'r[\"hook_event\"]==\"Stop\"'"
check "session_id carried"          "row_has '$SINK' 'r[\"session_id\"]==\"$TOP_ID\"'"
check "input summed once (no double)" "row_has '$SINK' 'r[\"tokens\"][\"input\"]==150'"
check "output summed"               "row_has '$SINK' 'r[\"tokens\"][\"output\"]==30'"
check "cache_read summed"           "row_has '$SINK' 'r[\"tokens\"][\"cache_read\"]==700'"
check "cache_creation is zero"      "row_has '$SINK' 'r[\"tokens\"][\"cache_creation\"]==0'"
check "context_max is per-request peak" "row_has '$SINK' 'r[\"context_max\"]==600'"
check "requests counted"            "row_has '$SINK' 'r[\"requests\"]==2'"
check "tool_calls counted once"     "row_has '$SINK' 'r[\"tool_calls\"]==1'"
check "started_at from header"      "row_has '$SINK' 'r[\"started_at\"].startswith(\"2026-08-10T12:00:00\")'"
check "ended_at from last message"  "row_has '$SINK' 'r[\"ended_at\"].startswith(\"2026-08-10T12:00:04\")'"

echo "== non-matching session-id emits nothing (no cross-dispatch bleed) =="
SINK2="$WORK/sink2.jsonl"
"$PYTHON" "$HOOK" --session-dir "$SESS" --session-id "zzzz-not-here" --sink "$SINK2"
check "no rows for a foreign session-id" "[ ! -s '$SINK2' ]"

echo "== a child session with parentSession emits a SubagentStop row =="
CHILD_ID="child-1111"
cat > "$SESS/child.jsonl" <<EOF
{"type":"session","version":3,"id":"$CHILD_ID","timestamp":"2026-08-10T12:05:00.000Z","cwd":"/proj","parentSession":"$SESS/top.jsonl"}
{"type":"message","id":"c1","timestamp":"2026-08-10T12:05:01.000Z","message":{"role":"user","content":[{"type":"text","text":"explore"}]}}
{"type":"message","id":"c2","timestamp":"2026-08-10T12:05:02.000Z","message":{"role":"assistant","content":[{"type":"text","text":"found"}],"usage":{"input":30,"output":5,"cacheRead":0,"cacheWrite":0,"totalTokens":35,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"responseId":"rc1"}}
EOF
SINK3="$WORK/sink3.jsonl"
"$PYTHON" "$HOOK" --session-dir "$SESS" --session-id "$TOP_ID" --sink "$SINK3"
check "with session-id: top row emitted"  "[ \"\$(count_field '$SINK3' Stop)\" = 1 ]"
check "with session-id: child is SubagentStop" "[ \"\$(count_field '$SINK3' SubagentStop)\" = 1 ]"

# A child that belongs to a DIFFERENT dispatch (parentSession -> some other file, not
# our top session) must never be claimed by our --session-id filter.
cat > "$SESS/neighbour-child.jsonl" <<EOF
{"type":"session","version":3,"id":"nbr-child","timestamp":"2026-08-10T12:06:00.000Z","cwd":"/proj","parentSession":"$SESS/some-other-session.jsonl"}
{"type":"message","id":"n1","timestamp":"2026-08-10T12:06:01.000Z","message":{"role":"assistant","content":[{"type":"text","text":"other"}],"usage":{"input":5,"output":1,"cacheRead":0,"cacheWrite":0,"totalTokens":6,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"responseId":"rn"}}
EOF
check "a neighbour's child is not claimed by this dispatch's session-id" \
    "[ \"\$(count_field '$SINK3' SubagentStop)\" = 1 ]"

SINK4="$WORK/sink4.jsonl"
# A fresh dir with a plain session plus a child pointing back at it, no --session-id:
# each emits in its own lane.
S4="$WORK/s4"; mkdir -p "$S4"
cat > "$S4/plain.jsonl" <<EOF
{"type":"session","version":3,"id":"plain-1","timestamp":"2026-08-10T13:00:00.000Z","cwd":"/p"}
{"type":"message","id":"p1","timestamp":"2026-08-10T13:00:01.000Z","message":{"role":"assistant","content":[{"type":"text","text":"ok"}],"usage":{"input":10,"output":2,"cacheRead":0,"cacheWrite":0,"totalTokens":12,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"responseId":"rp"}}
EOF
cp "$SESS/child.jsonl" "$S4/plain-child.jsonl"
"$PYTHON" "$HOOK" --session-dir "$S4" --sink "$SINK4"
check "no ---session-id: parentSession row is SubagentStop" "[ \"\$(count_field '$SINK4' SubagentStop)\" = 1 ]"
check "no ---session-id: plain session is Stop"             "[ \"\$(count_field '$SINK4' Stop)\" = 1 ]"

echo "== a missing/empty session dir writes nothing, never breaks =="
SINK5="$WORK/sink5.jsonl"
mkdir -p "$WORK/empty"
"$PYTHON" "$HOOK" --session-dir "$WORK/empty" --session-id "$TOP_ID" --sink "$SINK5"
check "empty dir emits no rows" "[ ! -s '$SINK5' ]"
"$PYTHON" "$HOOK" --session-dir "$WORK/nope" --sink "$SINK5"
check "missing dir exits 0 (must never break the run)" "[ \$? -eq 0 ]"

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
