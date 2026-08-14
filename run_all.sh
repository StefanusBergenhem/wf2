#!/usr/bin/env bash
#
# wf2's own gate. Runs every test suite in the repo: the driver's Python suite, the
# CLI verb suites, the per-tool script suites, and the installer/rendering test.
# Run: bash run_all.sh   (exit 0 = all pass)
#
# Run this before every commit. Nothing else checks the source-wide invariants —
# envelope parity in particular is a cross-file rule (a role's frontmatter against the
# text it loads against the config template) that no single suite would catch.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

fail=0
run() {
    local label="$1"
    shift
    local out rc
    out="$("$@" 2>&1)"
    rc=$?
    if [ $rc -eq 0 ]; then
        printf 'ok   - %s\n' "$label"
    else
        fail=$((fail + 1))
        printf 'FAIL - %s\n' "$label"
        printf '%s\n' "$out" | tail -25 | sed 's/^/       /'
    fi
}

echo "[1/3] driver"
run "driver suite" bash tools/driver/tests/run_all.sh

echo ""
echo "[2/3] cli + tools"
for suite in tools/cli/tests/*_test.sh tools/*/*_test.sh; do
    [ -f "$suite" ] || continue
    run "${suite#tools/}" bash "$suite"
done

echo ""
echo "[3/3] install + rendering"
run "install_test.sh" bash install_test.sh

echo ""
if [ $fail -gt 0 ]; then
    echo "$fail suite(s) failed"
    exit 1
fi
echo "all wf2 suites pass"
