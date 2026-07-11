#!/usr/bin/env bash
#
# claude_hook_register_test.sh — TDD spec for claude_hook_register.py.
#
# Verifies: the register script creates-or-merges a Claude Code settings.json,
# adding one command entry under hooks.Stop and hooks.SubagentStop; it is
# idempotent on re-run, preserves unrelated settings and foreign hooks, and
# leaves a malformed settings.json untouched (non-zero exit).
# wf2-source-only — never rendered into an install target.
#
# Run:  bash tools/telemetry/claude_hook_register_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REG="$HERE/claude_hook_register.py"

PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }

CMD='python3 "$CLAUDE_PROJECT_DIR"/.wf/tools/telemetry/claude_usage_hook.py'

echo "== fresh create (parent dir auto-created) =="
S1="$WORK/p1/.claude/settings.json"
if "$PYTHON" "$REG" "$S1" "$CMD" > "$WORK/o1" 2>&1; then
    pass "exit zero"
else
    fail "exit non-zero (see $WORK/o1)"; cat "$WORK/o1"
fi
[ -f "$S1" ] && pass "settings.json created" || fail "settings.json missing"
if "$PYTHON" - "$S1" "$CMD" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1])); cmd = sys.argv[2]
for ev in ("Stop", "SubagentStop"):
    entries = d["hooks"][ev]
    assert len(entries) == 1
    assert entries[0]["hooks"] == [{"type": "command", "command": cmd}]
PY
then
    pass "Stop + SubagentStop each carry the command entry"
else
    fail "hook entries wrong: $(cat "$S1" 2>/dev/null)"
fi

echo "== idempotent re-run: no duplicates, file byte-identical =="
cp "$S1" "$WORK/before.json"
"$PYTHON" "$REG" "$S1" "$CMD" > /dev/null 2>&1 || fail "re-run exited non-zero"
cmp -s "$S1" "$WORK/before.json" && pass "re-run left the file unchanged" || fail "re-run rewrote the file"

echo "== merge preserves unrelated settings and foreign hooks =="
S2="$WORK/p2/.claude/settings.json"
mkdir -p "$(dirname "$S2")"
cat > "$S2" <<'JSON'
{
  "permissions": {"allow": ["Bash(ls:*)"]},
  "hooks": {
    "Stop": [{"hooks": [{"type": "command", "command": "echo user-hook"}]}]
  }
}
JSON
"$PYTHON" "$REG" "$S2" "$CMD" > /dev/null 2>&1 || fail "merge run exited non-zero"
if "$PYTHON" - "$S2" "$CMD" <<'PY' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1])); cmd = sys.argv[2]
assert d["permissions"] == {"allow": ["Bash(ls:*)"]}
stop = d["hooks"]["Stop"]
assert stop[0]["hooks"][0]["command"] == "echo user-hook"
assert any(h["command"] == cmd for e in stop for h in e["hooks"])
assert any(h["command"] == cmd for e in d["hooks"]["SubagentStop"] for h in e["hooks"])
PY
then
    pass "unrelated settings + foreign Stop hook preserved, ours merged in"
else
    fail "merge clobbered settings: $(cat "$S2" 2>/dev/null)"
fi

echo "== malformed settings.json: non-zero exit, file untouched =="
S3="$WORK/p3/.claude/settings.json"
mkdir -p "$(dirname "$S3")"
echo '{ this is not json' > "$S3"
cp "$S3" "$WORK/bad-before.json"
if "$PYTHON" "$REG" "$S3" "$CMD" > /dev/null 2>&1; then
    fail "malformed settings should exit non-zero"
else
    pass "malformed settings rejected"
fi
cmp -s "$S3" "$WORK/bad-before.json" && pass "malformed file left untouched" || fail "malformed file was rewritten"

echo "== missing args rejected =="
if "$PYTHON" "$REG" "$S1" > /dev/null 2>&1; then
    fail "missing command arg should be non-zero"
else
    pass "missing args rejected"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
