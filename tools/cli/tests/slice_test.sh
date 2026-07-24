#!/usr/bin/env bash
#
# Tests for `wf slice check` — the mechanical gate over the design-slice itself:
# no UNCONFIRMED assumption may leave the SA or enter the TL.
# Run: bash tools/cli/tests/slice_test.sh   (exit 0 = all pass)
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

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  design_slice: ".wf/design-slice.md"
YAML
SLICE="$PROJ/.wf/design-slice.md"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

# no assumptions section at all -> pass
cat > "$SLICE" <<'MD'
# Design-slice — widget

## Component requirements

- **REQ-1** — the widget does X  *(owner: core · CAP-1)*
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "no assumptions section exits 0" || bad "no-section exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "no assumptions section verdict pass" || bad "no-section verdict" "$OUT"

# all-CONFIRMED -> pass
cat >> "$SLICE" <<'MD'

## Assumptions requiring confirmation

- **A-1 · CONFIRMED** — CAP-1 read as widget-per-user, not widget-per-team.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "confirmed-only exits 0" || bad "confirmed exit" "rc=$RC $OUT"

# an UNCONFIRMED entry -> fail, code A3, id named
cat >> "$SLICE" <<'MD'
- **A-2 · UNCONFIRMED** — "driven" read as manual trigger only.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "unconfirmed exits 1" || bad "unconfirmed exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "fail" ] && ok "unconfirmed verdict fail" || bad "unconfirmed verdict" "$OUT"
[ "$(jget "$OUT" "any(f['code']=='A3' for f in d['errors'])")" = "True" ] && ok "finding code A3" || bad "A3 code" "$OUT"
[ "$(jget "$OUT" "any('A-2' in f['msg'] for f in d['errors'])")" = "True" ] && ok "names the assumption id" || bad "A3 id" "$OUT"

# --slice override wins over config
ALT="$PROJ/alt-slice.md"
printf '## Assumptions requiring confirmation\n\n- **A-9 · UNCONFIRMED** — x.\n' > "$ALT"
cat > "$SLICE" <<'MD'
# clean
MD
OUT="$(wf slice check --slice "$ALT" --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "--slice override is checked" || bad "--slice override" "rc=$RC $OUT"

# ---------------------------------------------------------------------------
# ADR citations (A4/A5) — resolved against every ADR set in the tree
# ---------------------------------------------------------------------------
mkdir -p "$PROJ/.wf/adrs" "$PROJ/doc/design/adrs"
# the canonical shape carries the title in frontmatter; a legacy set may lead with a heading
printf -- '---\nid: ADR-011\ntitle: baseline edited in place\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-011.md"
printf -- '---\nid: ADR-013\ntitle: widget port\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-013.md"
printf '# in-process goroutine workers\n' > "$PROJ/doc/design/adrs/ADR-011.md"

# a citation resolving to exactly one ADR passes, and echoes that ADR's own title
printf '# slice\n\n- bound by ADR-013\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "resolvable ADR citation exits 0" || bad "adr ok exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "widget port" ] \
  && ok "citation echoes the ADR's own title" || bad "adr title" "$OUT"

# a citation to an id no ADR file defines → A4
printf '# slice\n\n- bound by ADR-777\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "undefined ADR citation exits 1" || bad "adr undef exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' and 'ADR-777' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A4 names the undefined ADR" || bad "A4 undef" "$OUT"

# an id defined in TWO ADR sets, cited bare → A5, listing both paths
printf '# slice\n\n- baseline edited in place (ADR-011)\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "colliding ADR id cited bare exits 1" || bad "adr collide exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A5' and '.wf/adrs/ADR-011.md' in f['msg'] and 'doc/design/adrs/ADR-011.md' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A5 lists both defining paths" || bad "A5 collide" "$OUT"

# path-qualified, it resolves — and the echoed title exposes a mis-pointed citation
printf '# slice\n\n- baseline edited in place (doc/design/adrs/ADR-011)\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "path-qualified colliding id resolves" || bad "adr path exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "in-process goroutine workers" ] \
  && ok "the qualified path's own title is echoed" || bad "adr path title" "$OUT"

# a path-qualified citation whose file does not exist → A4, naming where it does live
printf '# slice\n\n- see doc/adrs/ADR-013\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "bad ADR path exits 1" || bad "adr badpath exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' and '.wf/adrs/ADR-013.md' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A4 names where the ADR actually lives" || bad "A4 badpath" "$OUT"

# a two-id shorthand 'REQ-216/ADR-013' is not a path citation — ADR-013 still resolves (L-066)
printf '# slice\n\n- REQ-216/ADR-013 governs this door\n' > "$SLICE"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "spec-id shorthand before an ADR is not a path citation" || bad "shorthand exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' for f in d['errors'])")" = "False" ] \
  && ok "no A4 on a REQ-id/ADR two-id shorthand" || bad "shorthand A4" "$OUT"

# missing slice file -> mechanical failure (exit 2)
rm -f "$SLICE"
if wf slice check >/dev/null 2>&1; then
    bad "missing slice should exit non-zero" "exited 0"
else
    RC=$?
    [ "$RC" -eq 2 ] && ok "missing slice exits 2" || bad "missing slice rc" "rc=$RC"
fi

echo ""
echo "  slice: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
