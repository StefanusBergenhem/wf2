#!/usr/bin/env bash
#
# claude_usage_hook_test.sh — TDD spec for claude_usage_hook.py.
#
# Verifies: the Stop/SubagentStop hook parses the transcript JSONL, sums usage
# tokens (deduped by message id), counts tool_use blocks, and appends ONE
# `kind: usage` JSON line to the config-resolved telemetry sink — silently
# exiting 0 (writing nothing) on any failure, because a telemetry hook must
# never break the session.
# wf2-source-only — never rendered into an install target.
#
# Run:  bash tools/telemetry/claude_usage_hook_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
HOOK="$HERE/claude_usage_hook.py"

PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }

# mk_proj <dir> — a project dir with a config whose sink is .wf/telemetry/sessions.jsonl
mk_proj() {
    mkdir -p "$1/.wf"
    cat > "$1/.wf/config.yaml" <<'YAML'
version: 1
project:
  name: "demo"
  target: "claude"
paths:
  tools: ".wf/tools"
  telemetry: ".wf/telemetry/sessions.jsonl"   # Append-only session log. Committed.
YAML
}

# Main transcript: 1 user line, 3 assistant lines — msg_2 split across two lines
# (same id, usage repeated: must count ONCE), 2 distinct tool_use blocks.
TRANSCRIPT="$WORK/main.jsonl"
cat > "$TRANSCRIPT" <<'JSONL'
{"type":"user","timestamp":"2026-01-01T00:00:00.000Z","message":{"role":"user","content":"do the thing"}}
{"type":"assistant","timestamp":"2026-01-01T00:00:01.000Z","message":{"id":"msg_1","role":"assistant","content":[{"type":"text","text":"on it"}],"usage":{"input_tokens":100,"output_tokens":10,"cache_read_input_tokens":50,"cache_creation_input_tokens":20}}}
{"type":"assistant","timestamp":"2026-01-01T00:00:02.000Z","message":{"id":"msg_2","role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Bash","input":{}}],"usage":{"input_tokens":200,"output_tokens":30,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
{"type":"assistant","timestamp":"2026-01-01T00:00:03.000Z","message":{"id":"msg_2","role":"assistant","content":[{"type":"tool_use","id":"toolu_2","name":"Read","input":{}}],"usage":{"input_tokens":200,"output_tokens":30,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
JSONL

AGENT_TRANSCRIPT="$WORK/agent.jsonl"
cat > "$AGENT_TRANSCRIPT" <<'JSONL'
{"type":"assistant","timestamp":"2026-01-01T00:01:00.000Z","message":{"id":"msg_a1","role":"assistant","content":[{"type":"text","text":"sub"}],"usage":{"input_tokens":5,"output_tokens":7,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
JSONL

P="$WORK/proj"; mk_proj "$P"
SINK="$P/.wf/telemetry/sessions.jsonl"

echo "== Stop event appends one usage record (silent, exit 0) =="
OUT="$("$PYTHON" "$HOOK" <<EOF 2>&1
{"session_id":"sess-1","transcript_path":"$TRANSCRIPT","hook_event_name":"Stop","cwd":"$P"}
EOF
)"
RC=$?
[ "$RC" -eq 0 ] && pass "exit zero" || fail "exit $RC"
[ -z "$OUT" ] && pass "no output (silent)" || fail "unexpected output: $OUT"
[ -f "$SINK" ] && pass "sink + parent dir created" || fail "sink not created"
[ "$(wc -l < "$SINK" 2>/dev/null)" = "1" ] && pass "one line written" || fail "expected 1 line"
if "$PYTHON" - "$SINK" <<'PY' 2>/dev/null
import json, sys
d = json.loads(open(sys.argv[1]).readline())
assert d["kind"] == "usage"
assert d["session_id"] == "sess-1"
assert d["hook_event"] == "Stop"
assert d["tokens"] == {"input": 300, "output": 40, "cache_read": 50, "cache_creation": 20}
assert d["tool_calls"] == 2
assert d["started_at"] == "2026-01-01T00:00:00.000Z"
assert d["ended_at"] == "2026-01-01T00:00:03.000Z"
assert d["recorded_at"]
# requests = distinct API messages (msg_1, msg_2); context_max = the largest
# single-request context (input+cache_read+cache_creation) — the honest peak,
# unlike summed cache_creation, which double-counts on cache-TTL re-writes.
assert d["requests"] == 2, d["requests"]
assert d["context_max"] == 200, d["context_max"]
PY
then
    pass "record fields: kind/session/event/tokens(deduped)/tools/timestamps/requests/context_max"
else
    fail "record fields wrong: $(cat "$SINK" 2>/dev/null)"
fi

echo "== SubagentStop prefers agent_transcript_path; appends (never rewrites) =="
FIRST_LINE="$(head -n1 "$SINK")"
"$PYTHON" "$HOOK" > /dev/null 2>&1 <<EOF
{"session_id":"sess-1","transcript_path":"$WORK/nope.jsonl","agent_transcript_path":"$AGENT_TRANSCRIPT","hook_event_name":"SubagentStop","cwd":"$P"}
EOF
[ "$(wc -l < "$SINK")" = "2" ] && pass "appended (2 lines)" || fail "append failed"
[ "$(head -n1 "$SINK")" = "$FIRST_LINE" ] && pass "existing line untouched" || fail "first line rewritten"
if "$PYTHON" - "$SINK" <<'PY' 2>/dev/null
import json, sys
d = json.loads(open(sys.argv[1]).readlines()[1])
assert d["hook_event"] == "SubagentStop"
assert d["tokens"]["input"] == 5 and d["tokens"]["output"] == 7
assert d["tool_calls"] == 0
assert d["requests"] == 1 and d["context_max"] == 5
PY
then
    pass "subagent record from agent_transcript_path"
else
    fail "subagent record wrong: $(tail -n1 "$SINK" 2>/dev/null)"
fi

echo "== relative sink from a linked worktree lands in the main checkout =="
REPO="$WORK/repo"; mk_proj "$REPO"
git init -q "$REPO"
git -C "$REPO" add -A
git -C "$REPO" -c user.email=t@t -c user.name=t commit -qm init
git -C "$REPO" worktree add -q "$WORK/wt" -b task/t1
"$PYTHON" "$HOOK" > /dev/null 2>&1 <<EOF
{"session_id":"sess-wt","transcript_path":"$TRANSCRIPT","hook_event_name":"Stop","cwd":"$WORK/wt"}
EOF
[ -f "$REPO/.wf/telemetry/sessions.jsonl" ] \
    && pass "record landed in the main checkout's sink" \
    || fail "main checkout sink not written from worktree"
[ ! -e "$WORK/wt/.wf/telemetry/sessions.jsonl" ] \
    && pass "nothing written into the worktree" \
    || fail "record leaked into the worktree"

echo "== failure modes: silent exit 0, nothing written =="
Q="$WORK/q"; mk_proj "$Q"
QSINK="$Q/.wf/telemetry/sessions.jsonl"

OUT="$("$PYTHON" "$HOOK" <<EOF 2>&1
{"session_id":"s","transcript_path":"$WORK/missing.jsonl","hook_event_name":"Stop","cwd":"$Q"}
EOF
)"
[ $? -eq 0 ] && [ -z "$OUT" ] && [ ! -e "$QSINK" ] \
    && pass "missing transcript: exit 0, silent, nothing written" \
    || fail "missing transcript mishandled"

OUT="$(echo 'not json at all' | "$PYTHON" "$HOOK" 2>&1)"
[ $? -eq 0 ] && [ -z "$OUT" ] \
    && pass "malformed stdin: exit 0, silent" \
    || fail "malformed stdin mishandled (rc=$? out=$OUT)"

EMPTY="$WORK/empty.jsonl"
printf '{"type":"user","message":{"role":"user","content":"hi"}}\n' > "$EMPTY"
"$PYTHON" "$HOOK" > /dev/null 2>&1 <<EOF
{"session_id":"s","transcript_path":"$EMPTY","hook_event_name":"Stop","cwd":"$Q"}
EOF
[ ! -e "$QSINK" ] \
    && pass "transcript without assistant usage: nothing written" \
    || fail "usage-less transcript wrote a record"

NOCFG="$WORK/nocfg"; mkdir -p "$NOCFG"
OUT="$("$PYTHON" "$HOOK" <<EOF 2>&1
{"session_id":"s","transcript_path":"$TRANSCRIPT","hook_event_name":"Stop","cwd":"$NOCFG"}
EOF
)"
[ $? -eq 0 ] && [ -z "$OUT" ] \
    && pass "missing config: exit 0, silent" \
    || fail "missing config mishandled"

echo "== malformed transcript lines are skipped, valid ones counted =="
MIXED="$WORK/mixed.jsonl"
cat > "$MIXED" <<'JSONL'
this line is garbage {{{
{"type":"assistant","timestamp":"2026-01-01T00:00:01.000Z","message":{"id":"m1","role":"assistant","content":[{"type":"text","text":"x"}],"usage":{"input_tokens":1,"output_tokens":2,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}
JSONL
M="$WORK/m"; mk_proj "$M"
"$PYTHON" "$HOOK" > /dev/null 2>&1 <<EOF
{"session_id":"s-m","transcript_path":"$MIXED","hook_event_name":"Stop","cwd":"$M"}
EOF
if "$PYTHON" - "$M/.wf/telemetry/sessions.jsonl" <<'PY' 2>/dev/null
import json, sys
d = json.loads(open(sys.argv[1]).readline())
assert d["tokens"]["input"] == 1 and d["tokens"]["output"] == 2
PY
then
    pass "garbage line skipped, valid usage recorded"
else
    fail "mixed transcript mishandled"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
