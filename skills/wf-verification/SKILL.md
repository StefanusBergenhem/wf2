---
name: wf-verification
description: Evidence-based completion checking — every claim of done must be backed by fresh, verifiable output, not assumptions or cached results. The shared checklist build self-checks against before handoff and review independently verifies.
---

# Verification — Evidence-Based Completion Checking

## Core principle

**Claims without evidence are lies.** It does not matter that you "know" the code is
correct or that it "should" work. If you have not run the command and seen the output in
this session, you do not know. Produce the evidence or do not claim completion.

## When this skill activates

- Before marking any task, step, or phase complete.
- Before submitting work for review.
- After applying any fix (to confirm the fix worked).

## The verification checklist

Every completion claim must pass ALL applicable checks. Skip none. For the evidence-format
and check-command snippets per section, read `references/evidence-formats.md` when you need
them — the checklist below is sufficient for routine claims.

### 1. Fresh test run

- [ ] Tests were executed in this session, not referenced from a previous run.
- [ ] Test output is captured and shown — not summarized, not paraphrased.
- [ ] All tests pass. Any skipped test has a documented reason.
- [ ] No test caching was used (`--no-cache` / `--forceExit` / framework equivalent).
- [ ] The test command matches what the gate would run — not a subset, not a filtered version.

### 2. Preflight pass

- [ ] `commands.preflight` ran in this session and exited clean. It bundles the project's
      lint, type-check, build, and format gates — a green preflight is the single
      mechanical signal that all of them passed.

### 3. Scope compliance

- [ ] Every file in `git diff --name-only` serves the contract's `covers`/acceptance
      criteria, or is a mechanical consequence of serving them (a regenerated file, an
      updated consumer, a test home).
- [ ] Nothing named in the contract's `out_of_scope` was changed.
- [ ] No unrelated drive-by change; nothing was deleted that shouldn't have been.

### 4. No suppression directives

- [ ] No suppression comment was added to make a check pass (the canonical ban list and
      rule live in `wf-agent-preamble`). Grep the diff per `references/evidence-formats.md`
      §4 to confirm none was introduced.

### 5. No debug output

- [ ] No `console.log`, `print()`, `debugger`, `binding.pry`, `dd()`, or equivalent debug
      statement was added.
- [ ] No commented-out code was left behind.
- [ ] No temporary test values (hardcoded IDs, localhost URLs, "test123") remain.

### 6. No TODO comments

- [ ] No new `TODO`, `FIXME`, `HACK`, `XXX`, or `TEMP` comment was introduced.
- [ ] A genuinely-needed TODO (a known, out-of-scope limitation) carries a ticket/issue reference.

### 7. Red-phase evidence (for TDD tasks)

- [ ] Tests were run BEFORE the implementation code was written.
- [ ] The output shows real failures — assertion failures or missing-symbol errors that
      prove the test checks the right thing, not compile/import errors elsewhere.
- [ ] The failure messages correspond to the behavior being implemented.

### 8. Diff review

- [ ] Read your own `git diff` as if you were the reviewer.
- [ ] Every changed line has a reason — no accidental whitespace, no unrelated formatting.
- [ ] The diff tells a coherent story: a reviewer can understand the change from it alone.

## Relationship to other skills

`wf-testing-anti-patterns` ensures the tests themselves are valid evidence — a test that
exhibits an anti-pattern does not satisfy checks 1 or 7.
