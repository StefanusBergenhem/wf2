#!/usr/bin/env bash
#
# record_session_test.sh — TDD spec for record_session.py.
#
# Verifies: a session record is appended as one JSON line, the sink's parent dir
# is auto-created, duration is computed from the timestamps, appends accumulate,
# the feedback block carries friction_kind (closed enum) and gotcha (free text)
# with safe defaults, and a missing required arg / invalid enum is rejected.
# wf2-source-only — never rendered into an install target.
#
# Runtime is system python3 (stdlib only) — the same invocation a target uses;
# wf2 ships no venv into targets, so the test exercises the real path.
#
# Run:  bash tools/telemetry/record_session_test.sh   (exit 0 = all green)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REC="$HERE/record_session.py"

FAILS=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
pass() { echo "  ok: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }

SINK="$WORK/tel/sessions.jsonl"   # parent dir does NOT exist yet

echo "== first record (auto-creates parent dir) =="
if python3 "$REC" --agent wf-discover --started-at 2026-01-01T00:00:00Z \
        --ended-at 2026-01-01T00:00:05Z --outcome completed \
        --wf-friction "step 3 verb ambiguous" --friction-kind skill_gap \
        --repo-observation "" \
        --gotcha "docker compose collides on :5432 unless COMPOSE_PROJECT_NAME is pinned" \
        --sink "$SINK" > "$WORK/o1" 2>&1; then
    pass "exit zero"
else
    fail "exit non-zero (see $WORK/o1)"; cat "$WORK/o1"
fi
[ -f "$SINK" ] && pass "sink + parent dir created" || fail "sink not created"
[ "$(wc -l < "$SINK")" -eq 1 ] && pass "one line written" || fail "expected 1 line"
if python3 -c "import json;d=json.loads(open('$SINK').readline());assert d['agent']=='wf-discover';assert d['outcome']=='completed';assert d['duration_seconds']==5;assert d['feedback']['wf_friction']=='step 3 verb ambiguous';assert d['feedback']['friction_kind']=='skill_gap';assert d['feedback']['repo_observation']=='';assert d['feedback']['gotcha'].startswith('docker compose');assert 'notes' not in d" 2>/dev/null; then
    pass "json fields + duration + structured feedback (friction_kind + gotcha)"
else
    fail "json fields / duration / feedback"
fi

echo "== append + feedback defaults empty when omitted =="
python3 "$REC" --agent wf-review --started-at 2026-01-01T00:00:00Z \
    --ended-at 2026-01-01T00:00:01Z --outcome halted --sink "$SINK" > /dev/null 2>&1
[ "$(wc -l < "$SINK")" -eq 2 ] && pass "appended (2 lines)" || fail "append failed"
if python3 -c "import json;d=json.loads(open('$SINK').readlines()[1]);assert d['feedback']['wf_friction']=='';assert d['feedback']['friction_kind']=='none';assert d['feedback']['repo_observation']=='';assert d['feedback']['gotcha']==''" 2>/dev/null; then
    pass "feedback defaults: prose empty, friction_kind 'none', gotcha empty"
else
    fail "feedback default"
fi

echo "== relative sink from a linked worktree lands in the main checkout =="
REPO="$WORK/repo"
git init -q "$REPO"
git -C "$REPO" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
git -C "$REPO" worktree add -q "$WORK/wt" -b task/t1
( cd "$WORK/wt" && python3 "$REC" --agent wf-review --started-at 2026-01-01T00:00:00Z \
    --ended-at 2026-01-01T00:00:02Z --outcome completed \
    --sink ".wf/telemetry/sessions.jsonl" > /dev/null 2>&1 )
if [ -f "$REPO/.wf/telemetry/sessions.jsonl" ]; then
    pass "append landed in the main checkout's sink"
else
    fail "main checkout sink not written from worktree"
fi
if [ -e "$WORK/wt/.wf/telemetry/sessions.jsonl" ]; then
    fail "record leaked into the worktree (lost on worktree close)"
else
    pass "nothing written into the worktree"
fi

echo "== relative sink from the main checkout resolves to its root =="
mkdir -p "$REPO/sub"
( cd "$REPO/sub" && python3 "$REC" --agent wf-build --started-at 2026-01-01T00:00:00Z \
    --ended-at 2026-01-01T00:00:01Z --outcome completed \
    --sink ".wf/telemetry/sessions.jsonl" > /dev/null 2>&1 )
[ "$(wc -l < "$REPO/.wf/telemetry/sessions.jsonl")" -eq 2 ] \
    && pass "appended to the repo-root sink from a subdir" \
    || fail "subdir invocation missed the repo-root sink"

echo "== relative sink outside any git repo falls back to cwd =="
mkdir -p "$WORK/nogit"
( cd "$WORK/nogit" && python3 "$REC" --agent wf-discover --started-at 2026-01-01T00:00:00Z \
    --ended-at 2026-01-01T00:00:01Z --outcome completed \
    --sink "tel/sessions.jsonl" > /dev/null 2>&1 )
[ -f "$WORK/nogit/tel/sessions.jsonl" ] \
    && pass "cwd-relative fallback outside git" \
    || fail "cwd-relative fallback missing"

echo "== invalid outcome rejected =="
if python3 "$REC" --agent x --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:01Z \
        --outcome banana --sink "$SINK" > /dev/null 2>&1; then
    fail "invalid --outcome should be non-zero"
else
    pass "invalid outcome rejected"
fi

echo "== invalid friction_kind rejected =="
if python3 "$REC" --agent x --started-at 2026-01-01T00:00:00Z --ended-at 2026-01-01T00:00:01Z \
        --outcome completed --friction-kind vibes --sink "$SINK" > /dev/null 2>&1; then
    fail "invalid --friction-kind should be non-zero"
else
    pass "invalid friction_kind rejected"
fi

echo "== missing required arg rejected =="
if python3 "$REC" --agent x --sink "$SINK" > /dev/null 2>&1; then
    fail "missing required args should be non-zero"
else
    pass "missing args rejected"
fi

echo ""
if [ "$FAILS" -eq 0 ]; then echo "ALL GREEN"; exit 0; else echo "$FAILS FAILURE(S)"; exit 1; fi
