#!/usr/bin/env bash
#
# Runs the whole driver test suite. Every test is standalone (stdlib unittest), so
# `python3 <file>` works on its own too; this just runs them all and sums up.
# Run: bash tools/driver/tests/run_all.sh   (exit 0 = all pass)
# wf2-source-only — never rendered into an install target.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
PYTHON="${PYTHON:-$(command -v python3)}"
[ -x "$ROOT/tools/.venv/bin/python" ] && PYTHON="$ROOT/tools/.venv/bin/python"

fail=0
for test_file in "$HERE"/*_test.py; do
    name="$(basename "$test_file")"
    out="$("$PYTHON" "$test_file" 2>&1)"
    rc=$?
    tail="$(printf '%s\n' "$out" | grep -E '^(OK|FAILED|Ran )' | tr '\n' ' ')"
    if [ $rc -eq 0 ]; then
        echo "ok   - $name  ($tail)"
    else
        fail=$((fail + 1))
        echo "FAIL - $name"
        printf '%s\n' "$out" | tail -30 | sed 's/^/       /'
    fi
done

if [ $fail -gt 0 ]; then
    echo "$fail test file(s) failed"
    exit 1
fi
echo "all driver tests pass"
