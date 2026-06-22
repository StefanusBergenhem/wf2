Referenced by `../SKILL.md`. Collects the per-section evidence-format snippets, the overall
Evidence Presentation Format, and the disqualifier table. Load when you need them — the
checklist in SKILL.md is sufficient for routine completion claims.

## Per-section evidence formats

These correspond to the numbered checklist sections in `../SKILL.md`.

### 1. Fresh test run — evidence format

```
TEST RUN:
  Command: <exact command>
  Output: <full output or relevant excerpt showing pass/fail counts>
  Result: ALL PASS / X FAILURES (list them)
```

### 2. Preflight pass — evidence format

```
PREFLIGHT:
  Command: <exact commands.preflight>
  Result: PASS / FAIL (on fail, the gate and last 20 lines)
```

### 3. Scope compliance — evidence format

```
SCOPE CHECK:
  files_to_touch: [list from contract]
  git diff --name-only: [actual output]
  Match: YES / NO (explain discrepancy)
```

### 4. No suppression directives — check command

```bash
git diff | grep -E "(ts-ignore|ts-expect-error|eslint-disable|noqa|noinspection|SuppressWarnings|type:\s*ignore)"
```

### 5. No debug output — check command

```bash
git diff | grep -E "^\+" | grep -E "(console\.log|debugger|binding\.pry|dd\(|print\(|System\.out\.print|var_dump)"
```

### 6. No TODO comments — check command

```bash
git diff | grep -E "^\+" | grep -E "(TODO|FIXME|HACK|XXX|TEMP)"
```

### 7. Red-phase evidence — evidence format

```
RED PHASE:
  Command: <test command>
  Failures:
    - <test name>: <failure message>
    - <test name>: <failure message>
  Interpretation: These failures confirm the tests check <behavior> which does not yet exist.
```

## Evidence presentation format

When presenting completion evidence, use this structure:

```
## Verification Evidence

### Tests
<evidence>

### Preflight
<evidence>

### Scope
<evidence>

### Clean Code Checks
- Suppression directives: NONE ADDED
- Debug output: NONE ADDED
- TODOs: NONE ADDED

### Diff Summary
- Files changed: <count>
- Lines added: <count>
- Lines removed: <count>
- All changes within scope: YES/NO
```

## What disqualifies a completion claim

Any of the following instantly disqualifies a "done" claim:

| Disqualifier | Example |
|-------------|---------|
| Stale evidence | "Tests passed earlier" without fresh output |
| Partial evidence | Showing 3 of 10 tests passing |
| Summarized evidence | "All tests pass" without showing the output |
| Assumed evidence | "This should work because..." |
| Suppressed failures | Adding `skip` to failing tests |
| Scope violations | Files changed that aren't in `files_to_touch` |
| Lingering debug code | `console.log` left in production code |
| Untested error paths | Every error/exception path in the implementation written for this task must have a corresponding test. If the implementation has N distinct error returns/throws, there must be at least N error-path tests. "Only happy-path tests exist" is a disqualifier. |
