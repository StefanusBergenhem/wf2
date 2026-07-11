#!/usr/bin/env bash
#
# Tests for wf telemetry — the session-record verb resolves the sink from config
# (paths.telemetry) and delegates to tools/telemetry/record_session.py.
# Run: bash tools/cli/tests/telemetry_test.sh   (exit 0 = all pass)
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
wf() { "$PYTHON" "$WF" "$@"; }

P="$(mktemp -d)"; mkdir -p "$P/.wf"
cat > "$P/.wf/config.yaml" <<'YAML'
version: 1
paths:
  telemetry: ".wf/telemetry/sessions.jsonl"
YAML

# record lands one JSON line in the config-resolved sink
if wf telemetry record-session --agent wf-orchestrate \
        --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:05Z \
        --outcome halted --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    ok "record-session exits zero"
else
    bad "record-session exit" "non-zero"
fi
SINK="$P/.wf/telemetry/sessions.jsonl"
[ -f "$SINK" ] && [ "$(wc -l < "$SINK")" -eq 1 ] \
    && ok "record lands in the config-resolved sink (1 line)" || bad "sink line" "missing/short"
"$PYTHON" -c "import json;d=json.loads(open('$SINK').readline());assert d['agent']=='wf-orchestrate';assert d['outcome']=='halted';assert d['duration_seconds']==5" 2>/dev/null \
    && ok "record carries agent/outcome/duration" || bad "record fields" "$(cat "$SINK" 2>/dev/null)"

# an explicit --sink overrides the config resolution
wf telemetry record-session --agent wf-build \
    --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:01Z \
    --outcome completed --sink "$P/alt.jsonl" --config "$P/.wf/config.yaml" >/dev/null 2>&1
[ -f "$P/alt.jsonl" ] && [ "$(wc -l < "$SINK")" -eq 1 ] \
    && ok "--sink override wins over config" || bad "sink override" "alt missing or config sink grew"

# missing required flag → non-zero
if wf telemetry record-session --agent x --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "missing required flags should fail" "exited 0"
else
    ok "missing required flags rejected"
fi

# --friction-kind and --gotcha pass through to the recorder
wf telemetry record-session --agent wf-build \
    --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:01Z \
    --outcome halted --wf-friction "contract contradicts itself" \
    --friction-kind contract_defect --gotcha "pin COMPOSE_PROJECT_NAME or :5432 collides" \
    --sink "$P/fk.jsonl" --config "$P/.wf/config.yaml" >/dev/null 2>&1
"$PYTHON" -c "import json;d=json.loads(open('$P/fk.jsonl').readline());assert d['feedback']['friction_kind']=='contract_defect';assert d['feedback']['gotcha'].startswith('pin COMPOSE')" 2>/dev/null \
    && ok "record-session forwards --friction-kind and --gotcha" || bad "friction/gotcha pass-through" "$(cat "$P/fk.jsonl" 2>/dev/null)"

# an invalid --friction-kind is rejected non-zero
if wf telemetry record-session --agent wf-build \
        --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:01Z \
        --outcome halted --friction-kind not_a_kind \
        --sink "$P/fk2.jsonl" --config "$P/.wf/config.yaml" >/dev/null 2>&1; then
    bad "invalid friction-kind should fail" "exited 0"
else
    ok "invalid --friction-kind rejected"
fi

echo ""
echo "  telemetry verbs: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
