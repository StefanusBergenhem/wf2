#!/usr/bin/env bash
#
# Tests for next_id.py — the repo-unique REQ-id allocator (tag-system writer side).
# Run: bash next_id_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/next_id.py"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; }

# --- fixtures -------------------------------------------------------------
TESTS="$TMP/tests"; mkdir -p "$TESTS/go" "$TESTS/ts"
# co-located Go test tags REQ-3 and REQ-7 (different whitespace / comment styles)
cat > "$TESTS/go/import_test.go" <<'GO'
// [REQ:REQ-3] imported rooms persist
/* [REQ: REQ-7 ] registry stays consistent */
GO
# a retired/orphan tag REQ-40 — allocation must still burn it (over-count is safe)
cat > "$TESTS/ts/old.test.ts" <<'TS'
// [REQ:REQ-40] a retired requirement's lingering tag
TS

BACKLOG="$TMP/design-backlog.md"
cat > "$BACKLOG" <<'MD'
# Design backlog
- **REQ-12** — the system shall persist the thing  *(owner: auth)*
- **REQ-13** — the system shall expose the thing   *(owner: gateway)*
MD

ADRS="$TMP/adrs"; mkdir -p "$ADRS"
cat > "$ADRS/0001-foo.md" <<'MD'
# ADR 0001
Supersedes the behavior formerly required by REQ-5.
MD

# --- 1: scans tags + backlog + adrs, returns max(all mentions)+1 ---------
# max across REQ-3,7,40 (tags) + 12,13 (backlog) + 5 (adr) = 40 -> next REQ-41
OUT="$(python3 "$SCRIPT" --scan "$TESTS" --scan "$BACKLOG" --scan "$ADRS" 2>"$TMP/err1")"; rc=$?
[ "$rc" -eq 0 ] && ok "runs (exit 0)" || bad "run failed (rc=$rc): $(cat "$TMP/err1")"
[ "$OUT" = "REQ-41" ] && ok "next id = max(all mentions)+1 (REQ-41)" || bad "wrong next id: got '$OUT', want REQ-41"

# --- 2: orphan/non-tag mentions count too (over-count is safe) ------------
# the REQ-40 orphan tag is what pushes the max; remove it -> max falls to backlog 13
rm "$TESTS/ts/old.test.ts"
OUT2="$(python3 "$SCRIPT" --scan "$TESTS" --scan "$BACKLOG" --scan "$ADRS")"
[ "$OUT2" = "REQ-14" ] && ok "drops to backlog max+1 once orphan tag gone (REQ-14)" || bad "wrong: got '$OUT2', want REQ-14"

# --- 3: --count N returns N consecutive ids ------------------------------
OUT3="$(python3 "$SCRIPT" --scan "$BACKLOG" --count 3)"
EXP3=$'REQ-14\nREQ-15\nREQ-16'
[ "$OUT3" = "$EXP3" ] && ok "--count 3 returns three consecutive ids" || bad "count wrong: got '$OUT3'"

# --- 4: greenfield — paths given but absent -> REQ-1 ----------------------
OUT4="$(python3 "$SCRIPT" --scan "$TMP/nonexistent" --scan "$TMP/also-missing")"
[ "$OUT4" = "REQ-1" ] && ok "greenfield (absent paths) -> REQ-1" || bad "greenfield wrong: got '$OUT4'"

# --- 5: word-boundary — PREQ-/FREQ- must NOT be read as REQ ids -----------
NOISE="$TMP/noise"; mkdir -p "$NOISE"
cat > "$NOISE/x.go" <<'GO'
// PREQ-9999 is not a requirement id; FREQ-5000 neither
GO
OUT5="$(python3 "$SCRIPT" --scan "$NOISE")"
[ "$OUT5" = "REQ-1" ] && ok "word-boundary: PREQ-/FREQ- not matched -> REQ-1" || bad "boundary wrong: got '$OUT5'"

# --- 6: no --scan path given is an input error (not a silent REQ-1) -------
python3 "$SCRIPT" --count 1 >/dev/null 2>"$TMP/err6"; rc=$?
[ "$rc" -eq 2 ] && ok "no --scan -> input error (exit 2)" || bad "no --scan not rejected (rc=$rc)"

# --- 7: --count < 1 is an input error ------------------------------------
python3 "$SCRIPT" --scan "$BACKLOG" --count 0 >/dev/null 2>"$TMP/err7"; rc=$?
[ "$rc" -eq 2 ] && ok "--count 0 -> input error (exit 2)" || bad "--count 0 not rejected (rc=$rc)"

# --- 8: --prefix SYS-TC allocates in its own namespace, blind to REQ ------
# a slice with a SYS-TC-2 tag + a REQ mention; SYS-TC allocation must ignore REQ and
# return SYS-TC-3, while REQ allocation on the same tree ignores SYS-TC.
SYS="$TMP/sys"; mkdir -p "$SYS"
cat > "$SYS/e2e_test.go" <<'GO'
// [SYS-TC:SYS-TC-2] operator exports yesterday's memos end-to-end
// [REQ:REQ-99] a component requirement in the same tree
GO
OUT8="$(python3 "$SCRIPT" --scan "$SYS" --prefix SYS-TC)"
[ "$OUT8" = "SYS-TC-3" ] && ok "--prefix SYS-TC ignores REQ ids -> SYS-TC-3" || bad "sys-tc wrong: got '$OUT8', want SYS-TC-3"
OUT8b="$(python3 "$SCRIPT" --scan "$SYS")"
[ "$OUT8b" = "REQ-100" ] && ok "default REQ prefix ignores SYS-TC ids -> REQ-100" || bad "req-lane wrong: got '$OUT8b', want REQ-100"

echo ""
echo "  $pass passed, $fail failed"
[ "$fail" -eq 0 ] && echo "ALL GREEN" || { echo "FAILURES"; exit 1; }
