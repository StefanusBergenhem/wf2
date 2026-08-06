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
  architecture: ".wf/architecture.md"
  transient: ".wf/transient"
  discover_model: ".wf/transient/discover/model.json"
limits:
  increments_per_sprint: 4
  tasks_per_increment: 10
YAML
SLICE="$PROJ/.wf/design-slice.md"
ARCH="$PROJ/.wf/architecture.md"
MODEL="$PROJ/.wf/transient/discover/model.json"
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

# a Covers line naming no driver id at all is as absent as a missing one
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Covers:** CAP-24", "**Covers:** the zone work"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a Covers line naming no CAP/L id" || bad "A10-bare" "$OUT"

# a learning-driven scenario covers an L id and nothing else — a valid driver
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
t=t.replace("**Serves:** CAP-24, L-88", "**Serves:** L-88")
open(p,'w').write(t.replace("**Covers:** CAP-24", "**Covers:** L-88"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A10)" = "False" ] \
  && ok "A10 accepts an L-only Covers line (a learning-driven scenario)" || bad "A10-L" "rc=$RC $OUT"

# an L-covered scenario is not CAP coverage: a served CAP still needs one of its own
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Covers:** CAP-24", "**Covers:** L-88"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A10)" = "False" ] && [ "$(has "$OUT" A11)" = "True" ] \
  && ok "an L-only case satisfies A10 but not the A11 CAP floor" || bad "A10-L vs A11" "$OUT"

# ---------------------------------------------------------------------------
# A11 — every served CAP is covered by a case the parser actually read
# ---------------------------------------------------------------------------

# the canonical slice serves CAP-24 (covered) and L-88 (no scenario) and passes:
# a learning needs no scenario
write_slice
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A11)" = "False" ] \
  && ok "A11 exempts an L-id in Serves (a learning needs no scenario)" || bad "A11-L" "rc=$RC $OUT"

# case-head syntax drift: the Covers line is still there, but no case parses, so the
# slice yields ZERO scenarios and A10 has nothing to flag — A11 is what catches it
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("- **SYS-TC-1:** end-to-end zone patch",
                            "**SYS-TC-1** — end-to-end zone patch"))
PY
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "a slice yielding zero parsed SYS-TCs exits 1" || bad "A11 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A11)" = "True" ] && ok "A11 flags a served CAP no parsed case covers" || bad "A11" "$OUT"
[ "$(jget "$OUT" "any('CAP-24' in f['msg'] for f in d['errors'] if f['code']=='A11')")" = "True" ] \
  && ok "A11 names the uncovered capability" || bad "A11 id" "$OUT"
[ "$(has "$OUT" A10)" = "False" ] \
  && ok "A10 is silent on a drifted case head — A11 is the backstop" || bad "A11 vs A10" "$OUT"

# no System test cases section at all → same finding
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("## System test cases", "## Scenarios"))
PY
OUT="$(wf slice check --format json)"
[ "$(has "$OUT" A11)" = "True" ] && ok "A11 flags a slice with no scenario section" || bad "A11-nosection" "$OUT"

# a second served CAP with no case of its own is named, while the covered one is not
write_slice
"$PYTHON" - "$SLICE" <<'PY'
import sys
p=sys.argv[1]; t=open(p).read()
open(p,'w').write(t.replace("**Serves:** CAP-24, L-88", "**Serves:** CAP-24, CAP-25, L-88"))
PY
OUT="$(wf slice check --format json)"
[ "$(jget "$OUT" "[f['msg'].split()[1] for f in d['errors'] if f['code']=='A11']")" = "['CAP-25']" ] \
  && ok "A11 names only the uncovered CAP" || bad "A11-partial" "$OUT"

# ---------------------------------------------------------------------------
# A12 — every allocated component is one the repo already carries (discover's derived
# model) or the architecture map names (the planned delta)
# ---------------------------------------------------------------------------

# The derived half: a minimal model.json in the real shape spine.py `merge` writes — nodes
# keyed by `<lang>:<id>`, each carrying the brief's `id` and the repo-relative `path`.
write_model() { mkdir -p "$(dirname "$MODEL")"; "$PYTHON" - "$MODEL" <<'PY'
import json, sys
def node(cid, path, loc):
    return {"uid": f"go:{cid}", "id": cid, "name": cid.rsplit("/", 1)[-1], "path": path,
            "loc": loc, "kind": "package", "lang": "go", "module": "example.com/demo",
            "synopsis": "", "has_doc": False, "has_tests": True, "types": [],
            "functions": [], "deps": []}
nodes = {n["uid"]: n for n in (node("internal/zones", "backend/internal/zones", 220),
                               node("internal/store", "backend/internal/store", 140))}
json.dump({"languages": ["go"], "nodes": nodes, "order": sorted(nodes),
           "title": "demo (go)",
           "meta": {"generated_at": "2026-01-01T00:00:00Z", "source_sha": "abc1234"}},
          open(sys.argv[1], "w"), indent=2)
PY
}

# The durable half: the DELTA only — structure the repo has not reached.
write_arch() { cat > "$ARCH" <<'MD'
# Architecture map

## Components

- **internal/httpapi** (planned) — Will mount the zone routes. Depends on: internal/zones.
MD
}

# Replace the canonical slice's prose increments with the template's
# `- **Allocation:**` bullet, one per increment ($1, $2 — either may embed a newline).
alloc_slice() { write_slice; "$PYTHON" - "$SLICE" "$1" "$2" <<'PY'
import sys
p, a1, a2 = sys.argv[1], sys.argv[2], sys.argv[3]
t = open(p).read()
t = t.replace("Goal: the store can patch one zone. Allocation: store + service.",
              "- **Allocation:** " + a1)
t = t.replace("Goal: the patch is reachable over HTTP. Allocation: handler + router.",
              "- **Allocation:** " + a2)
open(p, 'w').write(t)
PY
}

# an existing component with an EMPTY map passes: existing structure is derived, never
# listed — and a `(planned)` map entry the repo has not built passes too
write_model
printf '# Architecture map\n\n## Components\n\n- **<component-id>** — <what it does>.\n' > "$ARCH"
alloc_slice "internal/zones — patch one zone; internal/store — persist it" \
            "internal/zones — serve it"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "A12 accepts a repo component against an empty map (structure is derived)" \
  || bad "A12 derived" "rc=$RC $OUT"

write_arch
alloc_slice "internal/zones — patch one zone" \
            $'internal/httpapi — mount PATCH /zones/{id};\n  internal/store — persist it'
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "A12 accepts a (planned) map entry the repo has not built, and a wrapped allocation" \
  || bad "A12 planned" "rc=$RC $OUT"

# the repo-relative path is the same component under another name — the brief prints the
# id, grounding pointers print the path
alloc_slice "backend/internal/zones — patch one zone" "backend/internal/store — persist it"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "A12 accepts a component named by its repo-relative path" || bad "A12 path form" "rc=$RC $OUT"

# in NEITHER source → error naming it, routed to an SA session
write_arch
alloc_slice "internal/zones — patch one zone" "internal/router — route it"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "a component in neither source exits 1" || bad "A12 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A12)" = "True" ] && ok "A12 flags a component neither source carries" || bad "A12" "$OUT"
[ "$(jget "$OUT" "any('internal/router' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "A12 names the unknown component" || bad "A12 name" "$OUT"
[ "$(jget "$OUT" "any('SA session' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "A12 routes the structure change through an SA session" || bad "A12 route" "$OUT"

# the entry after a wrapped line break is read too — an author's line break must not
# silently drop half the allocation
write_arch
alloc_slice "internal/zones — patch one zone" \
            $'internal/store — persist it;\n  internal/router — route it'
OUT="$(wf slice check --format json)"
[ "$(jget "$OUT" "[f['msg'].split(\"'\")[1] for f in d['errors'] if f['code']=='A12']")" = "['internal/router']" ] \
  && ok "A12 reads the allocation entry beneath a wrapped line" || bad "A12 wrap" "$OUT"

# no derived inventory + something neither source resolves → never pass open: say so and
# name the run that fixes it
rm -f "$MODEL"
alloc_slice "internal/zones — patch one zone" "internal/router — route it"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "an absent discover model exits 1 when a component is unresolved" \
  || bad "A12-nomodel exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "len([f for f in d['errors'] if f['code']=='A12'])")" = "1" ] \
  && ok "an absent discover model yields one A12 error, not one per component" || bad "A12-nomodel count" "$OUT"
[ "$(jget "$OUT" "any('wf-discover' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "the absent-inventory error names wf-discover" || bad "A12-nomodel msg" "$OUT"

# an unreadable model is an absent one
printf 'not json at all' > "$MODEL"
OUT="$(wf slice check --format json)"
[ "$(jget "$OUT" "any('wf-discover' in f['msg'] for f in d['errors'] if f['code']=='A12')")" = "True" ] \
  && ok "an unreadable discover model is treated as absent" || bad "A12-badmodel" "$OUT"

# greenfield: no derived inventory yet, and the map carries every allocation → green.
# Nothing is unresolved, so there is nothing for discover to adjudicate.
rm -f "$MODEL"
write_arch
alloc_slice "internal/httpapi — mount PATCH /zones/{id}" "internal/httpapi — serve it"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "A12 passes greenfield: no model, every allocation named by the map" || bad "A12 greenfield" "rc=$RC $OUT"

# a slice that declares no allocation yet is silent — nothing to bind
write_slice
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && [ "$(has "$OUT" A12)" = "False" ] \
  && ok "A12 is silent on a slice with no allocation" || bad "A12 silent" "rc=$RC $OUT"
write_model; write_arch

# ---------------------------------------------------------------------------
# --slice override wins over config
# ---------------------------------------------------------------------------

ALT="$PROJ/alt-slice.md"
printf '# alt slice\n\nNothing a check needs.\n' > "$ALT"
write_slice
OUT="$(wf slice check --slice "$ALT" --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "--slice override is checked" || bad "--slice override" "rc=$RC $OUT"
[ "$(has "$OUT" A6)" = "True" ] \
  && ok "--slice override: the findings come from the named file" || bad "--slice findings" "$OUT"

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

# The transient tree is derived and disposable, and it holds the per-task worktrees —
# each a whole checkout, carrying its own copy of every ADR. A scan that walks into it
# finds the repo N+1 times over and reports the repo colliding with itself. Observed on
# a dems resume: two live worktrees turned every cited ADR into an A5 and halted the
# driver with slice_check_red on a slice that had passed at sprint start.
mkdir -p "$PROJ/.wf/transient/worktrees/s1-T1/.wf/adrs"
cp "$PROJ/.wf/adrs/ADR-013.md" "$PROJ/.wf/transient/worktrees/s1-T1/.wf/adrs/ADR-013.md"
cite "bound by ADR-013"
OUT="$(wf slice check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "a worktree's ADR copy is not a second ADR set" || bad "adr worktree exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "any(f['code']=='A5' for f in d['errors'])")" = "False" ] \
  && ok "no A5 from the transient tree's copy of the repo" || bad "adr worktree A5" "$OUT"
[ "$(jget "$OUT" "any('transient' in s for s in d['adr_sets'])")" = "False" ] \
  && ok "adr_sets never reports a path inside transient" || bad "adr worktree sets" "$OUT"
rm -rf "$PROJ/.wf/transient/worktrees"

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
