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

# ---------------------------------------------------------------------------
# wf telemetry roles — per-role context-footprint report (window-joins the
# SubagentStop usage rows to the skill rows; excludes wf-orchestrate, which is
# the main loop and shows under main_loop from its Stop rows).
# ---------------------------------------------------------------------------
FIX="$P/roles.jsonl"
cat > "$FIX" <<'JSONL'
{"agent":"wf-build","started_at":"2026-07-11T10:00:00Z","ended_at":"2026-07-11T10:05:00Z","outcome":"completed"}
{"agent":"wf-sa","started_at":"2026-07-11T10:10:00Z","ended_at":"2026-07-11T10:20:00Z","outcome":"completed"}
{"agent":"wf-orchestrate","started_at":"2026-07-11T09:00:00Z","ended_at":"2026-07-11T21:00:00Z","outcome":"completed"}
{"kind":"usage","session_id":"S1","hook_event":"SubagentStop","started_at":"2026-07-11T10:00:10Z","ended_at":"2026-07-11T10:04:50Z","tokens":{"input":1000,"output":2000,"cache_read":50000,"cache_creation":9000},"tool_calls":5,"requests":7,"context_max":60000}
{"kind":"usage","session_id":"S1","hook_event":"SubagentStop","started_at":"2026-07-11T10:10:30Z","ended_at":"2026-07-11T10:19:00Z","tokens":{"input":2000,"output":4000,"cache_read":80000,"cache_creation":18000},"tool_calls":9}
{"kind":"usage","session_id":"S1","hook_event":"Stop","started_at":"2026-07-11T09:00:00Z","ended_at":"2026-07-11T21:00:00Z","tokens":{"input":500,"output":3000,"cache_read":100000,"cache_creation":4500},"tool_calls":20,"requests":40,"context_max":104000}
JSONL
OUT="$(wf telemetry roles --sink "$FIX" --config "$P/.wf/config.yaml" --format json 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "telemetry roles exits zero" || bad "roles exit" "rc=$RC $OUT"
rget() { "$PYTHON" -c 'import sys,json; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2" 2>/dev/null; }
# wf-build subagent row: footprint = input+cache_creation = 10000
[ "$(rget "$OUT" "next(r['footprint_max'] for r in d['roles'] if r['role']=='wf-build')")" = "10000" ] \
    && ok "roles: wf-build footprint (input+cache_creation)" || bad "roles build foot" "$OUT"
[ "$(rget "$OUT" "next(r['footprint_max'] for r in d['roles'] if r['role']=='wf-sa')")" = "20000" ] \
    && ok "roles: wf-sa footprint" || bad "roles sa foot" "$OUT"
[ "$(rget "$OUT" "next(r['output_max'] for r in d['roles'] if r['role']=='wf-build')")" = "2000" ] \
    && ok "roles: carries output" || bad "roles output" "$OUT"
[ "$(rget "$OUT" "next(r['tool_calls_max'] for r in d['roles'] if r['role']=='wf-sa')")" = "9" ] \
    && ok "roles: carries tool_calls" || bad "roles tools" "$OUT"
# context_max separates "loaded too much" from cache churn: a hook-era row
# carries it; a pre-upgrade row reads as 0 rather than breaking the report
[ "$(rget "$OUT" "next(r['context_max_max'] for r in d['roles'] if r['role']=='wf-build')")" = "60000" ] \
    && ok "roles: carries context_max from the usage row" || bad "roles ctx" "$OUT"
[ "$(rget "$OUT" "next(r['context_max_max'] for r in d['roles'] if r['role']=='wf-sa')")" = "0" ] \
    && ok "roles: a pre-upgrade row reads context_max as 0" || bad "roles ctx legacy" "$OUT"
[ "$(rget "$OUT" "next(r['requests_avg'] for r in d['roles'] if r['role']=='wf-build')")" = "7" ] \
    && ok "roles: carries requests" || bad "roles requests" "$OUT"
# with context_max known, the report sorts by it first (honest peak beats
# churn-inflated footprint)
[ "$(rget "$OUT" "d['roles'][0]['role']")" = "wf-build" ] \
    && ok "roles: sorts by context_max before footprint" || bad "roles sort" "$OUT"
# wf-orchestrate must NOT be a subagent role; it appears in main_loop instead
[ "$(rget "$OUT" "any(r['role']=='wf-orchestrate' for r in d['roles'])")" = "False" ] \
    && ok "roles: excludes wf-orchestrate from subagent roles" || bad "roles orch excl" "$OUT"
[ "$(rget "$OUT" "d['main_loop'][0]['footprint']")" = "5000" ] \
    && ok "roles: main_loop footprint from the Stop row" || bad "roles main_loop" "$OUT"
[ "$(rget "$OUT" "d['main_loop'][0]['context_max']")" = "104000" ] \
    && ok "roles: main_loop carries context_max" || bad "roles main ctx" "$OUT"
[ "$(rget "$OUT" "d['matched']")" = "2" ] && ok "roles: reports matched count" || bad "roles matched" "$OUT"

# ---------------------------------------------------------------------------
# driver rows (kind: driver_event) — the driver brackets every dispatch, so its
# window joins the subagent's usage row exactly instead of by time-window overlap.
# ---------------------------------------------------------------------------
DRV="$P/driver.jsonl"
cat > "$DRV" <<'JSONL'
{"kind":"driver_event","ts":"2026-07-12T11:00:00Z","event":"sprint_start","sprint":"sprint-1"}
{"kind":"driver_event","ts":"2026-07-12T11:00:00Z","event":"dispatch","agent":"wf-build","role":"wf-build","mode":"build","sprint":"sprint-1","increment":1,"task":"T1","rc":0,"duration_s":3600,"started_at":"2026-07-12T11:00:00Z","ended_at":"2026-07-12T12:00:00Z"}
{"kind":"driver_event","ts":"2026-07-12T11:20:00Z","event":"dispatch","agent":"wf-review","role":"wf-review","mode":"review","sprint":"sprint-1","increment":1,"task":"T1","rc":0,"duration_s":600,"started_at":"2026-07-12T11:10:00Z","ended_at":"2026-07-12T11:20:00Z"}
{"kind":"driver_event","ts":"2026-07-12T12:00:00Z","event":"stop","reason":"work_exhausted","sprint":"sprint-1"}
{"kind":"usage","session_id":"S2","hook_event":"SubagentStop","started_at":"2026-07-12T11:05:00Z","ended_at":"2026-07-12T11:15:00Z","tokens":{"input":700,"output":300,"cache_read":40000,"cache_creation":300},"tool_calls":4,"requests":6,"context_max":42000}
JSONL
OUT="$(wf telemetry roles --sink "$DRV" --config "$P/.wf/config.yaml" --format json 2>&1)"; RC=$?
[ "$RC" -eq 0 ] && ok "roles: a driver-event sink exits zero" || bad "driver exit" "rc=$RC $OUT"
[ "$(rget "$OUT" "d['matched']")" = "1" ] \
    && ok "roles: a driver dispatch row is a join candidate" || bad "driver matched" "$OUT"
# the usage window sits INSIDE wf-build's dispatch and merely overlaps wf-review's —
# window-overlap scoring picks wf-review, containment picks the role that ran it
[ "$(rget "$OUT" "next(r['context_max_max'] for r in d['roles'] if r['role']=='wf-build')")" = "42000" ] \
    && ok "roles: the containing dispatch wins the join, not the best overlap" || bad "driver exact" "$OUT"
[ "$(rget "$OUT" "any(r['role']=='wf-review' for r in d['roles'])")" = "False" ] \
    && ok "roles: the overlapping dispatch takes no run it did not run" || bad "driver overlap" "$OUT"
# a phase event (sprint_start, stop) names no role and must not become one
[ "$(rget "$OUT" "len(d['roles'])")" = "1" ] \
    && ok "roles: a driver phase event is not a role" || bad "driver phase" "$OUT"

# a driver row that names its role only under `role` still joins
cat > "$DRV" <<'JSONL'
{"kind":"driver_event","ts":"2026-07-12T11:00:00Z","event":"dispatch","role":"wf-tl","mode":"increment","sprint":"sprint-1","increment":1,"rc":0,"duration_s":900,"started_at":"2026-07-12T11:00:00Z","ended_at":"2026-07-12T11:15:00Z"}
{"kind":"usage","session_id":"S3","hook_event":"SubagentStop","started_at":"2026-07-12T11:01:00Z","ended_at":"2026-07-12T11:14:00Z","tokens":{"input":100,"output":200,"cache_read":300,"cache_creation":400},"tool_calls":2,"requests":3,"context_max":31000}
JSONL
OUT="$(wf telemetry roles --sink "$DRV" --config "$P/.wf/config.yaml" --format json 2>&1)"
[ "$(rget "$OUT" "[r['role'] for r in d['roles']]")" = "['wf-tl']" ] \
    && ok "roles: a driver row naming only 'role' still joins" || bad "driver role key" "$OUT"

# the driver stamps whole seconds, so a transcript can overrun the dispatch span that
# bracketed it: containment fails and the overlap fallback must still land the right role
cat > "$DRV" <<'JSONL'
{"kind":"driver_event","ts":"2026-07-12T11:00:00Z","event":"dispatch","agent":"wf-build","role":"wf-build","mode":"build","sprint":"sprint-1","increment":1,"task":"T1","rc":0,"duration_s":3600,"started_at":"2026-07-12T11:00:00Z","ended_at":"2026-07-12T12:00:00Z"}
{"kind":"driver_event","ts":"2026-07-12T11:20:00Z","event":"dispatch","agent":"wf-review","role":"wf-review","mode":"review","sprint":"sprint-1","increment":1,"task":"T1","rc":0,"duration_s":600,"started_at":"2026-07-12T11:10:00Z","ended_at":"2026-07-12T11:20:00Z"}
{"kind":"usage","session_id":"S5","hook_event":"SubagentStop","started_at":"2026-07-12T10:59:59.400Z","ended_at":"2026-07-12T12:00:00.700Z","tokens":{"input":1,"output":2,"cache_read":3,"cache_creation":4},"tool_calls":1,"requests":1,"context_max":51000}
JSONL
OUT="$(wf telemetry roles --sink "$DRV" --config "$P/.wf/config.yaml" --format json 2>&1)"
[ "$(rget "$OUT" "[r['role'] for r in d['roles']]")" = "['wf-build']" ] \
    && ok "roles: a transcript overrunning its dispatch span still joins that role" || bad "driver trunc" "$OUT"

# no candidate contains the usage window (clock skew) → the overlap join still matches
cat > "$DRV" <<'JSONL'
{"kind":"driver_event","ts":"2026-07-12T11:00:00Z","event":"dispatch","agent":"wf-build","role":"wf-build","mode":"build","sprint":"sprint-1","increment":1,"task":"T1","rc":0,"duration_s":300,"started_at":"2026-07-12T11:00:00Z","ended_at":"2026-07-12T11:05:00Z"}
{"kind":"usage","session_id":"S4","hook_event":"SubagentStop","started_at":"2026-07-12T10:59:58Z","ended_at":"2026-07-12T11:05:03Z","tokens":{"input":10,"output":20,"cache_read":30,"cache_creation":40},"tool_calls":1,"requests":1,"context_max":7000}
JSONL
OUT="$(wf telemetry roles --sink "$DRV" --config "$P/.wf/config.yaml" --format json 2>&1)"
[ "$(rget "$OUT" "next(r['context_max_max'] for r in d['roles'] if r['role']=='wf-build')")" = "7000" ] \
    && ok "roles: falls back to the overlap join when nothing contains the window" || bad "driver fuzzy" "$OUT"

echo ""
echo "  telemetry verbs: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
