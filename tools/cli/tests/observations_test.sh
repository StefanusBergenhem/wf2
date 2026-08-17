#!/usr/bin/env bash
#
# Tests for `wf observations age` — the mechanical bound on the admission buffer that
# sits in front of paths.learnings. The buffer holds what has been seen ONCE; it grows
# every run and drains only by promotion, so without a cap it becomes the accumulator
# the admission gate exists to prevent.
# Run: bash tools/cli/tests/observations_test.sh   (exit 0 = all pass)
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
yget() { "$PYTHON" -c 'import sys,yaml; d=yaml.safe_load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"; }

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf/archive"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  observations: ".wf/observations.yaml"
  archive: ".wf/archive"
hygiene:
  observations_max: 3
YAML
OBS="$PROJ/.wf/observations.yaml"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

write_obs() { cat > "$OBS" <<'YAML'
version: 1
observations:
- statement: "oldest — one sighting, never reinforced"
  sources: ["2026-01-01T00:00:00Z"]
- statement: "second oldest"
  sources: ["2026-02-01T00:00:00Z"]
- statement: "reinforced late, so it is recent"
  sources: ["2026-01-05T00:00:00Z", "2026-09-01T00:00:00Z"]
- statement: "recent"
  sources: ["2026-08-01T00:00:00Z"]
- statement: "newest"
  sources: ["2026-10-01T00:00:00Z"]
YAML
}

# ── the cap ──────────────────────────────────────────────────────────────────
write_obs
OUT="$(wf observations age --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "age: exits 0" || bad "age exit" "rc=$RC $OUT"
[ "$(yget "$OBS" "len(d['observations'])")" = "3" ] \
    && ok "age: the buffer is cut to hygiene.observations_max" || bad "age cap" "$(cat "$OBS")"
[ "$(jget "$OUT" "d['dropped']")" = "2" ] \
    && ok "age: it reports how many it dropped" || bad "age dropped count" "$OUT"

# recency is the NEWEST source, not the first — an entry sighted again is not stale
[ "$(yget "$OBS" "any('reinforced late' in o['statement'] for o in d['observations'])")" = "True" ] \
    && ok "age: recency reads the newest source, so a reinforced entry survives" || bad "age recency" "$(cat "$OBS")"
[ "$(yget "$OBS" "any('oldest' in o['statement'] for o in d['observations'])")" = "False" ] \
    && ok "age: the least recently sighted entries are the ones dropped" || bad "age oldest" "$(cat "$OBS")"

# ── nothing is lost ──────────────────────────────────────────────────────────
ARCH="$(jget "$OUT" "d['archived']")"
[ -f "$ARCH" ] \
    && ok "age: dropped entries are archived, never deleted outright" || bad "age archive" "$ARCH"
[ "$(yget "$ARCH" "len(d['observations'])")" = "2" ] \
    && ok "age: the archive holds exactly what was dropped" || bad "age archive body" "$(cat "$ARCH")"
[ "$(yget "$ARCH" "sorted(o['statement'][:6] for o in d['observations'])")" = "['oldest', 'second']" ] \
    && ok "age: the archive holds the two least recently sighted" || bad "age archive who" "$(cat "$ARCH")"

# ── idempotence + the quiet cases ────────────────────────────────────────────
OUT2="$(wf observations age --format json)"
[ "$(jget "$OUT2" "d['dropped']")" = "0" ] \
    && ok "age: a buffer already under the cap drops nothing" || bad "age idempotent" "$OUT2"
BEFORE="$(cat "$OBS")"
wf observations age >/dev/null
[ "$BEFORE" = "$(cat "$OBS")" ] \
    && ok "age: a no-op run does not rewrite the file" || bad "age no-op write" "$(cat "$OBS")"

rm -f "$OBS"
OUT3="$(wf observations age --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(jget "$OUT3" "d['dropped']")" = "0" ] \
    && ok "age: an absent buffer is silent, not an error" || bad "age absent" "rc=$RC $OUT3"

# an entry with no parseable timestamp cannot be judged stale, so it is kept
cat > "$OBS" <<'YAML'
version: 1
observations:
- statement: "no sources at all"
- statement: "a sprint marker only"
  sources: ["sprint:s13"]
- statement: "a real stamp"
  sources: ["2026-10-01T00:00:00Z"]
- statement: "another real stamp"
  sources: ["2026-09-01T00:00:00Z"]
YAML
wf observations age >/dev/null
[ "$(yget "$OBS" "len(d['observations'])")" = "3" ] \
    && ok "age: the cap still applies when some entries carry no stamp" || bad "age unstamped cap" "$(cat "$OBS")"
[ "$(yget "$OBS" "any('no sources at all' in o['statement'] for o in d['observations'])")" = "True" ] \
    && ok "age: an entry with no stamp is kept — it cannot be judged stale" || bad "age unstamped kept" "$(cat "$OBS")"

echo ""
echo "  observations: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
