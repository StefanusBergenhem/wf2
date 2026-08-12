#!/usr/bin/env bash
#
# Tests for `wf workset check` — the mechanical gate over the durable open work-set
# (paths.capabilities + paths.learnings) and the SYS-TC scenario sets nested in it.
# Run: bash tools/cli/tests/workset_test.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$CLI/../.." && pwd)"
PYTHON="$(command -v python3)"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

pass=0; fail=0
ok()  { pass=$((pass+1)); echo "  ok   - $1"; }
bad() { fail=$((fail+1)); echo "  FAIL - $1"; echo "         $2"; }
jget() { "$PYTHON" -c 'import sys,json; d=json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }
has()  { jget "$1" "any(f['code']=='$2' for f in d['errors'])"; }
warn() { jget "$1" "any(f['code']=='$2' for f in d['warnings'])"; }
count() { jget "$1" "d['entries'].get('$2','-')"; }

PROJ="$(mktemp -d)"; mkdir -p "$PROJ/.wf" "$PROJ/tests"
cat > "$PROJ/.wf/config.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  repo_state: ".wf/wf-repo-state.yaml"
  tests: ["tests"]
YAML
# The high-water marks live in their own file, not in config.yaml: they are state the
# minting roles WRITE, and config.yaml is read-only intent.
cat > "$PROJ/.wf/wf-repo-state.yaml" <<'YAML'
version: 1
id_counters:
  sys_tc: 20
YAML
CAPS="$PROJ/.wf/CAPABILITIES.yaml"
LEARN="$PROJ/.wf/LEARNINGS.yaml"

# The verb dispatches straight from its module: main.py's registry is wired by the
# caller that owns it, and this suite gates the check's behaviour, not the wiring.
wf() { (cd "$PROJ" && WF_CLI="$CLI" "$PYTHON" -c 'import sys,os; sys.path.insert(0,os.environ["WF_CLI"]); import workset; raise SystemExit(workset.COMMANDS[(sys.argv[1],sys.argv[2])](sys.argv[3:]))' "$@" --config "$PROJ/.wf/config.yaml"); }

# The learnings half of the work-set, carrying no scenario set — the ordinary state.
write_learnings() { cat > "$LEARN" <<'YAML'
version: 1
learnings:
  - id: L-118
    statement: "the driver strands a blocked task instead of re-cutting it"
YAML
}

# One capability that has been taken up, one that has not.
write_caps() { cat > "$CAPS" <<'YAML'
version: 1
capabilities:
  - id: CAP-004
    statement: "A user can attach a compliance requirement to a boundary."
    system_tests:
      - id: SYS-TC-12
        title: "a group-scope preview and the commit of the same change agree"
        given: "a group of doors carrying one shared requirement"
        when: "the change is previewed and then committed"
        then: "the two reports name the same elements"
  - id: CAP-005
    statement: "An element is judged against the union of every requirement."
YAML
}

# ---------------------------------------------------------------------------
# the clean work-set passes, and an entry with no set passes SILENTLY
# ---------------------------------------------------------------------------

write_caps; write_learnings
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "clean work-set exits 0" || bad "clean exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "clean work-set verdict pass" || bad "clean verdict" "$OUT"
[ "$(jget "$OUT" "len(d['errors'])")" = "0" ] && ok "clean work-set raises no error" || bad "clean errors" "$OUT"
[ "$(jget "$OUT" "len(d['warnings'])")" = "0" ] && ok "clean work-set raises no warning" || bad "clean warnings" "$OUT"
[ "$(jget "$OUT" "sorted(f['key'] for f in d['files'])")" = "['capabilities', 'learnings']" ] \
  && ok "check echoes both work-set files" || bad "files echo" "$OUT"
[ "$(count "$OUT" CAP-004)" = "1" ] && ok "per-entry scenario count for a taken-up capability" || bad "count CAP-004" "$OUT"
[ "$(count "$OUT" CAP-005)" = "0" ] && ok "a capability with no system_tests counts 0" || bad "count CAP-005" "$OUT"
[ "$(count "$OUT" L-118)" = "0" ] && ok "a learning needs no scenario set" || bad "count L-118" "$OUT"

# ---------------------------------------------------------------------------
# A10 — a scenario the machinery cannot read is a scenario that does not ship
# ---------------------------------------------------------------------------

write_caps
cat >> "$CAPS" <<'YAML'
    system_tests:
      - id: SYS-TC-13
        title: "the union judgement is reported per element"
        given: "an element in two boundaries"
        when: "compliance is requested"
        then: "both requirements are reported"
YAML
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "a second well-formed set still exits 0" || bad "two sets exit" "rc=$RC $OUT"
[ "$(has "$OUT" A10)" = "False" ] && ok "A10 is silent on well-formed scenarios" || bad "A10 false positive" "$OUT"

# a missing given/when/then leg
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace('        then: "the two reports name the same elements"\n', ''))
PY
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "a scenario missing 'then' exits 1" || bad "A10 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a scenario with no 'then'" || bad "A10 missing leg" "$OUT"

# a placeholder title left from the template
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace('title: "a group-scope preview and the commit of the same change agree"',
                             'title: "<one line>"'))
PY
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a placeholder title" || bad "A10 placeholder" "$OUT"

# a TODO stub is as unreadable as a placeholder
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace('given: "a group of doors carrying one shared requirement"',
                             'given: "TODO"'))
PY
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a TODO stub in a scenario leg" || bad "A10 todo" "$OUT"

# an id that is not SYS-TC-<n>
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace("id: SYS-TC-12", "id: TC-12"))
PY
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags an id that is not SYS-TC-<n>" || bad "A10 id shape" "$OUT"

# a scenario that is not a mapping at all
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
head = t.split("    system_tests:")[0]
open(p, 'w').write(head + "    system_tests:\n      - SYS-TC-12\n")
PY
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a bare-id scenario (not a mapping)" || bad "A10 scalar" "$OUT"

# system_tests that is not a list at all
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
head = t.split("    system_tests:")[0]
open(p, 'w').write(head + '    system_tests: "SYS-TC-12"\n')
PY
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A10)" = "True" ] && ok "A10 flags a system_tests that is not a list" || bad "A10 not-list" "$OUT"

# ---------------------------------------------------------------------------
# A14 — one id twice inside a single entry
# ---------------------------------------------------------------------------

write_caps
cat >> "$CAPS" <<'YAML'
    system_tests:
      - id: SYS-TC-13
        title: "the union judgement is reported per element"
        given: "an element in two boundaries"
        when: "compliance is requested"
        then: "both requirements are reported"
      - id: SYS-TC-13
        title: "the union judgement is reported per element"
        given: "an element in two boundaries"
        when: "compliance is requested"
        then: "both requirements are reported"
YAML
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "a repeated id inside one entry exits 1" || bad "A14 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A14)" = "True" ] && ok "A14 flags a duplicate id within one entry" || bad "A14" "$OUT"
[ "$(has "$OUT" A11)" = "False" ] && ok "A14 does not also raise A11 (one entry, not two)" || bad "A14 vs A11" "$OUT"

write_caps
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A14)" = "False" ] && ok "A14 is silent on distinct ids" || bad "A14 false positive" "$OUT"

# ---------------------------------------------------------------------------
# A11 — the duplication guard: one id under two entries, one wording
# ---------------------------------------------------------------------------

# the cross-cutting scenario: same id, identical text under both capabilities
cat > "$CAPS" <<'YAML'
version: 1
capabilities:
  - id: CAP-004
    statement: "A user can attach a compliance requirement to a boundary."
    system_tests:
      - id: SYS-TC-12
        title: "a group-scope preview and the commit of the same change agree"
        given: "a group of doors carrying one shared requirement"
        when: "the change is previewed and then committed"
        then: "the two reports name the same elements"
  - id: CAP-005
    statement: "An element is judged against the union of every requirement."
    system_tests:
      - id: SYS-TC-12
        title: "a group-scope preview and the commit of the same change agree"
        given: "a group of doors carrying one shared requirement"
        when: "the change is previewed and then committed"
        then: "the two reports name the same elements"
YAML
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "one id duplicated with identical text exits 0" || bad "A11 clean exit" "rc=$RC $OUT"
[ "$(has "$OUT" A11)" = "False" ] && ok "A11 is silent when both copies read the same" || bad "A11 false positive" "$OUT"
[ "$(count "$OUT" CAP-005)" = "1" ] && ok "the duplicated copy counts under both entries" || bad "dup count" "$OUT"

# the failure mode duplication introduces: the two copies drift apart
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
i = t.rfind('then: "the two reports name the same elements"')
open(p, 'w').write(t[:i] + 'then: "the commit report names strictly more elements"' + t[i + len('then: "the two reports name the same elements"'):])
PY
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "two copies of one id with different text exits 1" || bad "A11 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A11)" = "True" ] && ok "A11 flags the drifted duplicate" || bad "A11" "$OUT"
jget "$OUT" "[f['msg'] for f in d['errors'] if f['code']=='A11'][0]" | grep -q "SYS-TC-12" \
  && ok "the A11 message names the divergent id" || bad "A11 msg" "$OUT"

# the same id spanning the two FILES is the same guard
cat > "$LEARN" <<'YAML'
version: 1
learnings:
  - id: L-118
    statement: "the driver strands a blocked task instead of re-cutting it"
    system_tests:
      - id: SYS-TC-12
        title: "a group-scope preview and the commit of the same change agree"
        given: "a group of doors carrying one shared requirement"
        when: "the change is previewed and then committed"
        then: "something else entirely happens"
YAML
write_caps
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A11)" = "True" ] && ok "A11 spans capabilities and learnings" || bad "A11 cross-file" "$OUT"
write_learnings

# ---------------------------------------------------------------------------
# A13 — an id above id_counters.sys_tc was never minted
# ---------------------------------------------------------------------------

write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace("id: SYS-TC-12", "id: SYS-TC-20"))
PY
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "an id at the high-water mark exits 0" || bad "A13 boundary exit" "rc=$RC $OUT"
[ "$(has "$OUT" A13)" = "False" ] && ok "A13 is silent at the high-water mark" || bad "A13 false positive" "$OUT"

write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace("id: SYS-TC-12", "id: SYS-TC-21"))
PY
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 1 ] && ok "an id above the high-water mark exits 1" || bad "A13 exit" "rc=$RC $OUT"
[ "$(has "$OUT" A13)" = "True" ] && ok "A13 flags an id the counter never minted" || bad "A13" "$OUT"

# ---------------------------------------------------------------------------
# A15 — the work-set and the shipped tag disagree (WARN, never a gate)
# ---------------------------------------------------------------------------

write_caps
cat > "$PROJ/tests/preview_test.go" <<'GO'
package tests

// [SYS-TC:SYS-TC-12] a group-scope preview and the commit of the same change agree — Given a group of doors carrying one shared requirement; When the change is previewed and then committed; Then the two reports name the same elements
func TestPreview(t *testing.T) {}
GO
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "a shipped scenario matching its tag exits 0" || bad "A15 clean exit" "rc=$RC $OUT"
[ "$(warn "$OUT" A15)" = "False" ] && ok "A15 is silent when the tag and the entry agree" || bad "A15 false positive" "$OUT"

"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace('then: "the two reports name the same elements"',
                             'then: "the commit report names strictly more elements"'))
PY
OUT="$(wf workset check --format json)"; RC=$?
[ "$(warn "$OUT" A15)" = "True" ] && ok "A15 warns when the entry drifted from the shipped tag" || bad "A15" "$OUT"
[ "$(has "$OUT" A15)" = "False" ] && ok "A15 is not an error" || bad "A15 severity" "$OUT"
[ "$RC" -eq 0 ] && ok "an A15 warning alone still exits 0" || bad "A15 exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "d['verdict']")" = "pass" ] && ok "an A15 warning alone still reads pass" || bad "A15 verdict" "$OUT"

# an unshipped scenario is amendable freely — no tag, no warning
write_caps
"$PYTHON" - "$CAPS" <<'PY'
import sys
p = sys.argv[1]; t = open(p).read()
open(p, 'w').write(t.replace("id: SYS-TC-12", "id: SYS-TC-14"))
PY
OUT="$(wf workset check --format json)"
[ "$(warn "$OUT" A15)" = "False" ] && ok "A15 leaves an unshipped scenario alone" || bad "A15 unshipped" "$OUT"
rm -f "$PROJ/tests/preview_test.go"

# ---------------------------------------------------------------------------
# A16 — a work-set file that is there but cannot be read is reported, not crashed on
# ---------------------------------------------------------------------------

write_caps
printf 'capabilities:\n  - id: CAP-004\n   statement: [unclosed\n' > "$CAPS"
OUT="$(wf workset check --format json 2>"$PROJ/err.txt")"; RC=$?
[ "$RC" -eq 1 ] && ok "a malformed work-set file exits 1" || bad "A16 exit" "rc=$RC $OUT"
[ -n "$OUT" ] && [ "$(has "$OUT" A16)" = "True" ] \
  && ok "A16 reports the unparseable file" || bad "A16" "rc=$RC out='$OUT' err='$(cat "$PROJ/err.txt")'"
grep -q "Traceback" "$PROJ/err.txt" && bad "A16 crashed" "$(cat "$PROJ/err.txt")" \
  || ok "a malformed file raises no traceback"

# a top level that is not a mapping is the same class of finding
printf -- '- CAP-004\n- CAP-005\n' > "$CAPS"
OUT="$(wf workset check --format json)"
[ "$(has "$OUT" A16)" = "True" ] && ok "A16 reports a non-mapping top level" || bad "A16 top level" "$OUT"

# ---------------------------------------------------------------------------
# an absent file is a legitimate empty work-set, not a failure
# ---------------------------------------------------------------------------

rm -f "$CAPS"
OUT="$(wf workset check --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "an absent work-set file exits 0" || bad "absent exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "[f['present'] for f in d['files'] if f['key']=='capabilities'][0]")" = "False" ] \
  && ok "check reports the absent file as not present" || bad "absent present flag" "$OUT"
[ "$(has "$OUT" A16)" = "False" ] && ok "an absent file raises no A16" || bad "absent A16" "$OUT"

# ---------------------------------------------------------------------------
# no in-code default for the id high-water mark
# ---------------------------------------------------------------------------

write_caps; write_learnings
nocounter() { (cd "$PROJ" && WF_CLI="$CLI" "$PYTHON" -c 'import sys,os; sys.path.insert(0,os.environ["WF_CLI"]); import workset; raise SystemExit(workset.COMMANDS[("workset","check")](sys.argv[1:]))' --config "$1") >/dev/null 2>&1; }

# The counter is absent from an existing state file.
cat > "$PROJ/.wf/config-nocounter.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  repo_state: ".wf/state-empty.yaml"
  tests: ["tests"]
YAML
printf 'version: 1\nid_counters: {}\n' > "$PROJ/.wf/state-empty.yaml"
if nocounter "$PROJ/.wf/config-nocounter.yaml"; then
  bad "unset id_counters.sys_tc should fail" "exited 0"
else
  ok "workset check: unset id_counters.sys_tc → non-zero exit"
fi

# The state file itself is missing — a repo that was never initialised, or a role that
# deleted it. Silently reading 0 would let every minted id re-collide.
cat > "$PROJ/.wf/config-nostate.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  repo_state: ".wf/absent-state.yaml"
  tests: ["tests"]
YAML
if nocounter "$PROJ/.wf/config-nostate.yaml"; then
  bad "a missing repo-state file should fail" "exited 0"
else
  ok "workset check: missing repo-state file → non-zero exit"
fi

# paths.repo_state unset entirely — an install that predates the extraction.
cat > "$PROJ/.wf/config-nokey.yaml" <<'YAML'
version: 1
paths:
  capabilities: ".wf/CAPABILITIES.yaml"
  learnings: ".wf/LEARNINGS.yaml"
  tests: ["tests"]
YAML
if nocounter "$PROJ/.wf/config-nokey.yaml"; then
  bad "unset paths.repo_state should fail" "exited 0"
else
  ok "workset check: unset paths.repo_state → non-zero exit"
fi

echo ""
echo "  workset: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
