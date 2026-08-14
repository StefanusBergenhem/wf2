#!/usr/bin/env bash
#
# Tests for `wf impact` — mechanical consumer/fan-out computation: given changed
# symbols and/or files, emit the consumer file set (grep, source/tests split) and
# the companion fan-out (built-in *.up.sql→*.down.sql sibling + config-declared
# impact.companions rules), so a change's reach is computed, not recalled.
# Run: bash tools/cli/tests/impact_test.sh   (exit 0 = all pass)
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

make_proj() {  # $1 = project dir
  mkdir -p "$1/.wf" "$1/core" "$1/e2e" "$1/other" "$1/backend/migrations" "$1/frontend/gen"
  cat > "$1/.wf/config.yaml" <<'YAML'
version: 1
paths:
  sprint: ".wf/sprint.yaml"
impact:
  companions:
    - when: "backend/migrations/*"
      add: ["frontend/gen/*.ts"]
YAML
  printf 'package core\nfunc DoorSwing() {}\n'        > "$1/core/widget.go"
  printf 'package core\nfunc TestDoorSwing() { DoorSwing() }\n' > "$1/core/widget_test.go"
  printf 'describe("flow", () => { DoorSwing() })\n'  > "$1/e2e/flow_spec.ts"
  printf 'package other\n// nothing here\n'           > "$1/other/nothing.go"
  printf 'CREATE TABLE doors (id text);\n'            > "$1/backend/migrations/0001_x.up.sql"
  printf 'export type A = {}\n'                       > "$1/frontend/gen/a.ts"
  printf 'export type B = {}\n'                       > "$1/frontend/gen/b.ts"
}

# ---- a git project (the primary scan path; --untracked must be seen) ----
PROJ="$(mktemp -d)"; make_proj "$PROJ"
git -C "$PROJ" init -q && git -C "$PROJ" add -A
printf 'package core\nfunc helper() { DoorSwing() }\n' > "$PROJ/core/new_helper.go"  # untracked
wf() { "$PYTHON" "$WF" "$@" --config "$PROJ/.wf/config.yaml"; }

OUT="$(wf impact files --symbol DoorSwing --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "impact exits 0" || bad "exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "'core/widget.go' in d['symbols']['DoorSwing']['files']")" = "True" ] \
  && ok "impact finds a source consumer" || bad "src consumer" "$OUT"
[ "$(jget "$OUT" "'core/new_helper.go' in d['symbols']['DoorSwing']['files']")" = "True" ] \
  && ok "impact sees untracked files" || bad "untracked" "$OUT"
[ "$(jget "$OUT" "'core/widget_test.go' in d['symbols']['DoorSwing']['tests']")" = "True" ] \
  && ok "impact splits test consumers out" || bad "test consumer" "$OUT"
[ "$(jget "$OUT" "'e2e/flow_spec.ts' in d['symbols']['DoorSwing']['tests']")" = "True" ] \
  && ok "impact catches a spec-named e2e consumer" || bad "spec consumer" "$OUT"
[ "$(jget "$OUT" "'other/nothing.go' in d['candidates']")" = "False" ] \
  && ok "impact excludes non-consumers" || bad "non-consumer" "$OUT"
[ "$(jget "$OUT" "'core/widget.go' in d['candidates'] and 'core/widget_test.go' in d['candidates']")" = "True" ] \
  && ok "impact candidates union the consumer sets" || bad "candidates" "$OUT"

# toolkit artifacts are never consumers: a .wf/ hit stays out of the results
mkdir -p "$PROJ/.wf/archive"
printf 'old sprint mentioning DoorSwing\n' > "$PROJ/.wf/archive/old-sprint.yaml"
git -C "$PROJ" add -A
OUT="$(wf impact files --symbol DoorSwing --format json)"
[ "$(jget "$OUT" "any('.wf/' in c for c in d['candidates'])")" = "False" ] \
  && ok "impact excludes .wf/ artifacts from consumers" || bad "wf excl" "$OUT"

# a symbol nothing consumes → present, empty
OUT="$(wf impact files --symbol DoorSwing --symbol NoSuchSymbolAnywhere --format json)"
[ "$(jget "$OUT" "d['symbols']['NoSuchSymbolAnywhere']['files']")" = "[]" ] \
  && ok "impact reports an unconsumed symbol as empty" || bad "empty sym" "$OUT"

# built-in companion: *.up.sql carries its *.down.sql sibling (even before it exists)
OUT="$(wf impact files --file backend/migrations/0001_x.up.sql --format json)"
[ "$(jget "$OUT" "'backend/migrations/0001_x.down.sql' in d['candidates']")" = "True" ] \
  && ok "impact adds the down.sql sibling of an up.sql" || bad "down.sql" "$OUT"

# config companion rule: a migrations file fans out to the generated *.ts set
[ "$(jget "$OUT" "'frontend/gen/a.ts' in d['candidates'] and 'frontend/gen/b.ts' in d['candidates']")" = "True" ] \
  && ok "impact expands a config companions rule" || bad "config rule" "$OUT"
[ "$(jget "$OUT" "any(c['rule']=='backend/migrations/*' for c in d['companions'])")" = "True" ] \
  && ok "impact names the rule that fired" || bad "rule name" "$OUT"

# a --file that matches no companion rule adds nothing
OUT="$(wf impact files --file core/widget.go --format json)"
[ "$(jget "$OUT" "d['companions']")" = "[]" ] \
  && ok "impact: no rule fires for an unmatched file" || bad "no rule" "$OUT"
[ "$(jget "$OUT" "d['candidates']")" = "['core/widget.go']" ] \
  && ok "impact: a bare --file is still a candidate" || bad "bare file" "$OUT"

# a consumer hit can also trigger a companion rule (not just --file inputs)
printf 'ALTER TABLE doors ADD swing text; -- DoorSwing\n' > "$PROJ/backend/migrations/0002_y.up.sql"
OUT="$(wf impact files --symbol DoorSwing --format json)"
[ "$(jget "$OUT" "'frontend/gen/a.ts' in d['candidates']")" = "True" ] \
  && ok "impact: a consumer hit triggers companion rules too" || bad "consumer rule" "$OUT"
[ "$(jget "$OUT" "'backend/migrations/0002_y.down.sql' in d['candidates']")" = "True" ] \
  && ok "impact: a consumer up.sql hit carries its down.sql" || bad "consumer down" "$OUT"

# no --symbol and no --file → usage error
if wf impact files >/dev/null 2>&1; then bad "no-args should fail" "exited 0"; else ok "impact: no symbol/file → non-zero exit"; fi

# ---- a project with no git repo (pure-python fallback scan) ----
PROJ="$(mktemp -d)"; make_proj "$PROJ"
OUT="$(wf impact files --symbol DoorSwing --format json)"; RC=$?
[ "$RC" -eq 0 ] && ok "impact (no git) exits 0" || bad "nogit exit" "rc=$RC $OUT"
[ "$(jget "$OUT" "'core/widget.go' in d['symbols']['DoorSwing']['files']")" = "True" ] \
  && ok "impact (no git) falls back to a filesystem scan" || bad "nogit scan" "$OUT"

echo ""
echo "  impact: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
