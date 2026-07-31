#!/usr/bin/env bash
#
# Tests for `wf slice check` — the mechanical gate over the design-slice itself:
# the sections the increment loop and the close-time drain both depend on.
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
has() { jget "$1" "any(f['code']=='$2' for f in d['errors'])"; }

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  design_slice: ".wf/design-slice.md"
limits:
  increments_per_sprint: 4
  tasks_per_increment: 10
YAML
SLICE="$PROJ/.wf/design-slice.md"
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

# The canonical shape every passing slice carries: a Serves header, a narrative, the
# claimed scope, ordered increments, and SYS-TC cases naming what they cover.
write_slice() { cat > "$SLICE" <<'MD'
# Design-slice — zones

**Serves:** CAP-24, L-88

## Design narrative

The zone store gains a patch seam. Flow: HTTP handler -> zone service -> store,
wired in the composition root.

## Claimed scope

- **CAP-24** — this iteration delivers single-zone patch end to end; bulk patch is
  knowingly left for a later sprint.
- **L-88** — the collision guard lands with the first fleet increment.

## Increments

### Increment 1 — Zone store seam

Goal: the store can patch one zone. Allocation: store + service.
Checkpoint: after this, a patch round-trips through the service in a unit test.

### Increment 2 — HTTP surface

Goal: the patch is reachable over HTTP. Allocation: handler + router.
Checkpoint: after this, PATCH /zones/{id} demonstrably updates a zone.

## System test cases

- **SYS-TC-1:** end-to-end zone patch
  **Covers:** CAP-24
  - **Given** a stored zone
  - **When** it is patched over HTTP
  - **Then** the change is readable
MD
}

# ---------------------------------------------------------------------------
# the clean slice passes
# ---------------------------------------------------------------------------

write_slice
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "clean slice exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "clean slice verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "d['serves']")" = "['CAP-24', 'L-88']" ] \
  && ok "check echoes the serves header ids" || bad "serves echo" "$OUT"
[ "$(jget "$OUT" "[i['n'] for i in d['increments']]")" = "[1, 2]" ] \
  && ok "check echoes the declared increments" || bad "increments echo" "$OUT"
[ "$(jget "$OUT" "d['increments'][0]['title']")" = "Zone store seam" ] \
  && ok "check echoes each increment's title" || bad "increment title" "$OUT"

# ---------------------------------------------------------------------------
# A6 — the design narrative
# ---------------------------------------------------------------------------

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import re, sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("## Design narrative", "## Old narrative"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "missing narrative exits 1" || bad "narrative missing exit" "rc=$RC $OUT"
[ "$(has "$OUT" A6)" = "True" ] && ok "missing narrative finding is A6" || bad "A6 code" "$OUT"

# a narrative section holding only comments/blanks -> still A6
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import re, sys
p=sys.argv[1]; t=open(p).read()
t=re.sub(r"(## Design narrative\n).*?(?=\n## )", r"\1\n<!-- write the story here -->\n", t, flags=re.S)
open(p,'w').write(t)
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A6)" = "True" ] && ok "comment-only narrative finding is A6" || bad "A6 empty code" "$OUT"

# ---------------------------------------------------------------------------
# A7 — the Serves header (the close-time drain anchor)
# ---------------------------------------------------------------------------

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Serves:** CAP-24, L-88\n", ""))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "missing Serves header exits 1" || bad "A7 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A7)" = "True" ] && ok "A7 flags a slice with no Serves header" || bad "A7" "$OUT"

# a header naming no id at all is as absent as a missing one
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Serves:** CAP-24, L-88", "**Serves:** the zone work"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A7)" = "True" ] && ok "A7 flags a Serves header naming no CAP/L id" || bad "A7-bare" "$OUT"

# ---------------------------------------------------------------------------
# A8 — the claimed scope (the design-time adequacy input)
# ---------------------------------------------------------------------------

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("## Claimed scope", "## Scope notes"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "missing claimed scope exits 1" || bad "A8 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A8)" = "True" ] && ok "A8 flags a slice with no claimed scope" || bad "A8" "$OUT"

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import re, sys
p=sys.argv[1]; t=open(p).read()
t=re.sub(r"(## Claimed scope\n).*?(?=\n## )", r"\1\n<!-- what does this iteration claim? -->\n", t, flags=re.S)
open(p,'w').write(t)
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A8)" = "True" ] && ok "A8 flags a claimed scope holding only comments" || bad "A8-empty" "$OUT"

# ---------------------------------------------------------------------------
# A9 — the ordered increments and the sizing cap
# ---------------------------------------------------------------------------

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("## Increments", "## Plan"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "missing increments exits 1" || bad "A9 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A9)" = "True" ] && ok "A9 flags a slice with no Increments section" || bad "A9" "$OUT"

# a section declaring no increment block
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import re, sys
p=sys.argv[1]; t=open(p).read()
t=re.sub(r"(## Increments\n).*?(?=\n## System test cases)", r"\1\nTBD\n", t, flags=re.S)
open(p,'w').write(t)
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A9)" = "True" ] && ok "A9 flags an Increments section with no blocks" || bad "A9-empty" "$OUT"

# out-of-order numbering
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("### Increment 1 —", "### Increment 3 —"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A9)" = "True" ] && ok "A9 flags increments numbered out of order" || bad "A9-order" "$OUT"
[ "$(jget "$OUT" "any('[3, 2]' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A9 names the numbering it found" || bad "A9-order msg" "$OUT"

# over limits.increments_per_sprint → error (a trust knob, not a suggestion)
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
extra = "".join(f"### Increment {n} — filler\n\nGoal: n/a.\n\n" for n in (3, 4, 5))
open(p,'w').write(t.replace("## System test cases", extra + "## System test cases"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "over-cap increments exits 1" || bad "A9-cap exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A9' and 'increments_per_sprint' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A9 names limits.increments_per_sprint on the over-cap slice" || bad "A9-cap" "$OUT"

# the cap comes from config, not from code — raising it accepts the same slice
cat > "$PROJ/.wf/config-big.yaml" <<'YAML'
version: 1
paths:
  design_slice: ".wf/design-slice.md"
limits:
  increments_per_sprint: 9
  tasks_per_increment: 10
YAML
OUT="$("$PYTHON" "$WF" slice check --config "$PROJ/.wf/config-big.yaml" --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "A9 reads the cap from config (raised cap → pass)" || bad "A9-cap config" "rc=$RC $OUT"

# an unset limits block is a mechanical failure, not a silent default
cat > "$PROJ/.wf/config-nolimits.yaml" <<'YAML'
version: 1
paths:
  design_slice: ".wf/design-slice.md"
YAML
write_slice
if "$PYTHON" "$WF" slice check --config "$PROJ/.wf/config-nolimits.yaml" >/dev/null 2>&1; then
  bad "missing limits should fail" "exited 0"
else
  ok "unset limits.increments_per_sprint → non-zero exit"
fi

# ---------------------------------------------------------------------------
# A10 — every SYS-TC case names what it covers
# ---------------------------------------------------------------------------

write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("  **Covers:** CAP-24\n", ""))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "an uncovered SYS-TC exits 1" || bad "A10 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a SYS-TC with no Covers line" || bad "A10" "$OUT"
[ "$(jget "$OUT" "any('SYS-TC-1' in f['msg'] for f in d['errors'] if f['code']=='A10')")" = "True" ] \
  && ok "A10 names the uncovered case" || bad "A10 id" "$OUT"

# a Covers line naming no CAP is as absent as a missing one
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Covers:** CAP-24", "**Covers:** the zone work"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a Covers line naming no CAP" || bad "A10-bare" "$OUT"

# ---------------------------------------------------------------------------
# Assumptions (A3)
# ---------------------------------------------------------------------------

write_slice
cat >> "$SLICE" <<'MD'

## Assumptions requiring confirmation

- **A-1 · CONFIRMED** — CAP-24 read as zone-per-site, not zone-per-tenant.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "confirmed-only assumptions exit 0" || bad "confirmed exit" "rc=$RC $OUT"

cat >> "$SLICE" <<'MD'
- **A-2 · UNCONFIRMED** — "patched" read as partial update only.
MD
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "unconfirmed exits 1" || bad "unconfirmed exit" "rc=$RC $OUT"
[ "$(has "$OUT" A3)" = "True" ] && ok "finding code A3" || bad "A3 code" "$OUT"
[ "$(jget "$OUT" "any('A-2' in f['msg'] for f in d['errors'])")" = "True" ] && ok "names the assumption id" || bad "A3 id" "$OUT"

# --slice override wins over config
ALT="$PROJ/alt-slice.md"
printf '## Assumptions requiring confirmation\n\n- **A-9 · UNCONFIRMED** — x.\n' > "$ALT"
write_slice
OUT="$(wf slice check --slice "$ALT" --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "--slice override is checked" || bad "--slice override" "rc=$RC $OUT"

# ---------------------------------------------------------------------------
# ADR citations (A4/A5) — resolved against every ADR set in the tree
# ---------------------------------------------------------------------------
mkdir -p "$PROJ/.wf/adrs" "$PROJ/doc/design/adrs"
# the canonical shape carries the title in frontmatter; a legacy set may lead with a heading
printf -- '---\nid: ADR-011\ntitle: baseline edited in place\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-011.md"
printf -- '---\nid: ADR-013\ntitle: zone port\n---\n\n## Context\n' \
  > "$PROJ/.wf/adrs/ADR-013.md"
printf '# in-process goroutine workers\n' > "$PROJ/doc/design/adrs/ADR-011.md"

cite() { write_slice; printf '\n## Binding ADRs\n\n- %s\n' "$1" >> "$SLICE"; }

# a citation resolving to exactly one ADR passes, and echoes that ADR's own title
cite "bound by ADR-013"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "resolvable ADR citation exits 0" || bad "adr ok exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "zone port" ] \
  && ok "citation echoes the ADR's own title" || bad "adr title" "$OUT"

# a citation to an id no ADR file defines → A4
cite "bound by ADR-777"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "undefined ADR citation exits 1" || bad "adr undef exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' and 'ADR-777' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A4 names the undefined ADR" || bad "A4 undef" "$OUT"

# an id defined in TWO ADR sets, cited bare → A5, listing both paths
cite "baseline edited in place (ADR-011)"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "colliding ADR id cited bare exits 1" || bad "adr collide exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A5' and '.wf/adrs/ADR-011.md' in f['msg'] and 'doc/design/adrs/ADR-011.md' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A5 lists both defining paths" || bad "A5 collide" "$OUT"

# path-qualified, it resolves — and the echoed title exposes a mis-pointed citation
cite "baseline edited in place (doc/design/adrs/ADR-011)"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "path-qualified colliding id resolves" || bad "adr path exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['adr_citations'][0]['title']")" = "in-process goroutine workers" ] \
  && ok "the qualified path's own title is echoed" || bad "adr path title" "$OUT"

# a path-qualified citation whose file does not exist → A4, naming where it does live
cite "see doc/adrs/ADR-013"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "bad ADR path exits 1" || bad "adr badpath exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' and '.wf/adrs/ADR-013.md' in f['msg'] for f in d['errors'])")" = "True" ] \
  && ok "A4 names where the ADR actually lives" || bad "A4 badpath" "$OUT"

# a two-id shorthand 'CAP-216/ADR-013' is not a path citation — ADR-013 still resolves (L-066)
cite "CAP-216/ADR-013 governs this door"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "spec-id shorthand before an ADR is not a path citation" || bad "shorthand exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A4' for f in d['errors'])")" = "False" ] \
  && ok "no A4 on a CAP-id/ADR two-id shorthand" || bad "shorthand A4" "$OUT"

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
