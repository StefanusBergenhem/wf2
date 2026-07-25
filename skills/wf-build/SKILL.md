---
name: wf-build
description: TDD developer procedure — executes one task contract red→green→refactor, derives test oracles from its acceptance criteria, stamps an e2e task's [SYS-TC:<id>] tag, runs preflight, and writes the review handoff.
---

# wf-build — execute one task contract

You execute the contract at `paths.current_task` under TDD. You do not plan or read the
spec layer (ADRs, design slice) — the contract is complete for what this task builds.
Resolve every path and command from `.wf/config.yaml`:

- `CONTRACT` = `paths.current_task` — what to build (the single source)
- `FEEDBACK` = `paths.feedback` — a prior review's rejection; present → Fix mode
- `commands.preflight` — the mechanical gate to pass before handoff
- result artifacts you may write: `paths.review_ready`, `paths.design_issues`

## Step 0 — Mode

- `FEEDBACK` exists → **Fix mode** (see below).
- otherwise → **Build mode**, from Step 1.

## Step 1 — Load the contract

1. Read `CONTRACT`. It carries `acceptance_criteria` (each with the `tests` that prove
   it, or a `verified_by` gate), `covers`, `requirements` (each covered id's full
   statement), `files_to_touch`, `out_of_scope`, and `implementation_notes` — plus, on
   an e2e task, `system_tests`, and when the task introduces a component or widens a
   shared seam, `interface_contract`: the exact signature/struct/endpoint shape to
   implement. Deviating from it is a contract problem (Step 3b), not a judgement call.
   `files_to_touch` is the **expected write set** — your starting pointers, not a fence.
   Write beyond it when the task genuinely needs it (a consumer that won't compile
   otherwise, a test-file home, a regenerated file); what bounds your work is `covers`,
   the acceptance criteria, and `out_of_scope`, which is binding.
2. Read only the source the contract points at — the files in `files_to_touch` and any
   path named in `implementation_notes`. No wider exploration.
3. A completed `depends_on` task is already merged into the branch your worktree was cut
   from, so its work is present — do not re-verify it.
4. When an implementation note tells you to follow a dependency task's pattern, recover
   it from that task's merge diff — the contract's `dependency_commits` maps each merged
   `depends_on` task to its commit hash. `git show <sha> --stat` lists what it changed;
   `git show <sha> -- <file>` shows the pattern itself. Read those diffs, never the whole
   files they touch, and do not go hunting for the pattern elsewhere in the tree.

## Step 2 — Derive the test oracles

From the contract alone — never re-derive from prose elsewhere:

- Each `acceptance_criteria[].check` is a behaviour you must prove; its `check` names the
  inputs and expected output a failing test is written from.
- Each criterion's `tests[]` entry says where that proof lives: `level: unit` against its
  `target`, or `level: integration` across the real `seam` it names — exercise the seam
  for real, never a mock. Write one test per entry, deriving the assertions from the
  `check`. An AC carrying `verified_by` instead of `tests` is proven by that gate running
  green in Step 4, not by a test you write.

An AC you cannot turn into a test from the contract (it contradicts the source code) is a
contract problem → Step 3b.

## Step 3 — TDD

Announce each phase.

### Red

1. Write the test for every AC `tests` entry: set up specific inputs, invoke the
   code under test, assert specific outputs.
2. **On an e2e task, stamp the proving tag.** In each end-to-end test, place a plain
   comment — any comment style — carrying `[SYS-TC:<id>] <description>` for each
   `system_tests[].id`, the description taken verbatim from that entry: the test proving
   `SYS-TC-1` carries `[SYS-TC:SYS-TC-1] <its system_tests[].description>`. That tag
   line is the durable proof record the reviewer verifies. A component task's tests
   carry **no** tag of any kind — requirement ids live only in the contract.
3. Check each test against the `wf-testing-anti-patterns` table. A match means restructure
   it before continuing.
4. Run the project's test command for what you changed and **confirm the tests FAIL** for
   the right reason — an assertion or missing-symbol failure, not a compile error
   elsewhere. When an AC is a pure type/compile-time guarantee the test runner does not
   enforce (a transpile-only runner strips types without checking them, so the test passes
   both before and after the change), the Red oracle is the project's **type-checker/compiler**
   failing — run it and confirm it reports the gap. Runtime assertions on such an AC still
   need a vacuity check: temporarily break the assertion, confirm it fails, then restore it.

A test that passes before any implementation exists is testing the wrong thing —
investigate, do not proceed. **Exception — e2e task over merged dependencies:** when the
task is an e2e/system-test task and the behaviour under test is built by already-merged
`depends_on` tasks, a new test may correctly pass on first run. Do not treat that pass as
a Red failure — instead prove the test is not vacuous: temporarily break the asserted
behaviour (or mutate the expected value), confirm the test fails, then restore it and
confirm it passes. A test you cannot make fail this way is vacuous — restructure it.

### Green

1. Write the implementation to make the tests pass.
2. Run the tests and fix failures under retry discipline: **max 3 attempts per failure**,
   a different approach each time. On the 2nd, stop and trace the root cause before the
   3rd. On the 3rd, HALT with the exact output and the three approaches tried.

### Refactor

With tests green: no dead code, no debug output, no `TODO`/`HACK`/`FIXME`, no
commented-out code, no suppression directive, no narrative comment blocks — a comment
states a constraint the code cannot. Never paraphrase a requirement's statement or id
into a comment — tests carry no spec prose; the only spec reference in any test is an
e2e task's `[SYS-TC:]` tag line.

### Step 3b — Design issue (contract or merged code)

If the blocker is **not your own code** — an AC contradicts the source code, a requirement
is self-inconsistent, the contract asks for something the source code makes impossible, or an AC
fails because **already-merged code** (a dependency task's work, not this task's diff) is
defective — do not retry or work around it. Write `paths.design_issues` from
`assets/design_issues.yaml.tmpl`: one open entry, `task_id` your task, and a `summary` of what
is unbuildable and why — when the defect is in already-merged code, name which merged behaviour
violates which requirement.

Remove any stale `paths.review_ready`, then HALT and report. The return inspector reads
the open entry and parks the task — you never go on to review.

## Step 4 — Gate

Run `commands.preflight` (pipe to `/tmp/wf-build-<task-id>-preflight.log`; read the outcome
per `wf-agent-preamble`, not the whole log). It must exit clean. A gate that cannot run
because its environment is unavailable is a HALT, not a pass — do not write `review_ready`.

Then run the hygiene ratchet **from your worktree root** — before Step 5's commit, so
`HEAD` is still the fork point. It lints the tree you are standing in:

```
python3 <paths.tools>/cli/wf hygiene check --diff-base HEAD --format json
```

An `empty` verdict means the ratchet saw no changes at all: you ran it from the wrong
tree. Re-run it from the worktree — never treat `empty` as a pass.

A `fail` verdict lists `regressions` your own diff introduced — a too-long new function,
comment block, or new file. Fix each (shorten, split, cut the narrative) and re-run until
it passes. Findings outside `regressions` are pre-existing debt: leave them. A regression
you cannot fix without restructuring beyond the contract is a design issue (Step 3b).

## Step 5 — Hand off

1. Run the `wf-verification` checklist — every applicable item, with evidence.
2. Commit the task's work, staging everything in the worktree:
   ```
   git add -A && git commit -m "<task-id> <title>"
   ```
   Do not push — the orchestrator merges at the stage boundary.
3. Write `paths.review_ready` from `assets/review_ready.yaml.tmpl` — a presence marker. Its
   presence is the ready-for-review signal; the reviewer judges the committed diff
   against the contract, never a build self-report, so the file carries nothing but the
   task id.

## Fix mode (`paths.feedback` present)

1. Read `FEEDBACK` — address only its listed failures, each with the minimal change. Do
   not rewrite, and do not touch anything it does not name.
2. If a fix reveals a contract problem (Step 3b criteria), write the design issue and HALT.
3. Re-run the gate (Step 4) and the verification checklist, re-commit, delete
   `FEEDBACK`, then write `paths.review_ready` — in that order.

## Halt conditions

- A test fails 3 times with no identified root cause.
- The contract is contradictory or unbuildable as specified → Step 3b.
- `commands.preflight` is unset, or a mandatory gate cannot run (environment down).

Report per the `wf-agent-preamble` halt-report format.

## Telemetry (REQUIRED)

Your final action, always — committed, halted, or escalated: record one session line per
`wf-basics` §2 with `--agent wf-build` and your `--outcome`. If the recorder errors,
continue; telemetry never blocks.
