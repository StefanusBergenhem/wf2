#!/usr/bin/env bash
#
# Tests for `wf archive add` — snapshot a working-set artifact into the maintainer
# research archive (paths.archive). Write-only sink; no wf role reads it.
# Run: bash tools/cli/tests/archive_test.sh   (exit 0 = all pass)
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
  archive: ".wf/archive"
YAML
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

SRC="$PROJ/.wf/transient/design-slice.md"
printf 'slice body v1\n' > "$SRC"

# add copies the file under <archive>/<label>/ and leaves the source in place
OUT="$(wf archive add "$SRC" --label sprint-1 --format json)"; RC=$?
DEST="$(jget "$OUT" "d['archived']")"
[ "$RC" -eq 0 ] && ok "archive add exits 0" || bad "add exit" "rc=$RC $OUT"
[ -f "$DEST" ] && ok "archive add creates the snapshot" || bad "add dest" "$OUT"
case "$DEST" in "$PROJ/.wf/archive/sprint-1/"*) ok "archive add lands under <archive>/<label>/";; *) bad "add path" "$DEST";; esac
grep -q "slice body v1" "$DEST" && ok "snapshot holds the source content" || bad "add content" "$(cat "$DEST" 2>/dev/null)"
[ -f "$SRC" ] && ok "archive add (copy) leaves the source in place" || bad "add keeps source" "source gone"

# two snapshots of the same file+label do not clobber each other
printf 'slice body v2\n' > "$SRC"
OUT2="$(wf archive add "$SRC" --label sprint-1 --format json)"
DEST2="$(jget "$OUT2" "d['archived']")"
[ "$DEST" != "$DEST2" ] && ok "a second snapshot gets a distinct path" || bad "no clobber" "$DEST == $DEST2"
[ "$(ls "$PROJ/.wf/archive/sprint-1/" | wc -l)" -eq 2 ] && ok "both snapshots are retained" || bad "retain both" "$(ls "$PROJ/.wf/archive/sprint-1/")"

# --move drains the source
MSRC="$PROJ/.wf/transient/sprint.yaml"; printf 'sprint: x\n' > "$MSRC"
MOUT="$(wf archive add "$MSRC" --label sprint-1 --move --format json)"
MDEST="$(jget "$MOUT" "d['archived']")"
[ -f "$MDEST" ] && [ ! -f "$MSRC" ] && ok "archive add --move drains the source" || bad "move" "$MOUT (src exists: $([ -f "$MSRC" ] && echo yes || echo no))"

# a missing source is a hard error
if wf archive add "$PROJ/.wf/transient/nope.md" --label x >/dev/null 2>&1; then bad "missing source should fail" "exited 0"; else ok "archive add: missing source → non-zero exit"; fi

# archiving with paths.archive unset is a hard error (no silent default)
cat > "$PROJ/.wf/config-noarch.yaml" <<'YAML'
version: 1
paths:
  transient: ".wf/transient"
YAML
if "$PYTHON" "$WF" archive add "$SRC" --label x --config "$PROJ/.wf/config-noarch.yaml" >/dev/null 2>&1; then
  bad "unset paths.archive should fail" "exited 0"
else
  ok "archive add: unset paths.archive → non-zero exit"
fi

echo ""
echo "  archive: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
