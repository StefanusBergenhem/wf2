---
name: wf-build
description: TDD developer procedure — executes one task contract red→green→refactor, derives test oracles from its acceptance criteria, stamps each requirement's [REQ:<id>] tag, runs preflight, and writes the review handoff.
---

# wf-build — execute one task contract

You execute the contract at `paths.current_task` under TDD. You do not plan, expand
scope, or read the spec layer (ADRs, design slice) — the contract is complete for what
this task builds. Resolve every path and command from `.wf/config.yaml`:

- `CONTRACT` = `paths.current_task` — what to build (the single source)
- `FEEDBACK` = `paths.feedback` — a prior review's rejection; present → Fix mode
- `commands.preflight` — the mechanical gate to pass before handoff
- result artifacts you may write: `paths.review_ready`, `paths.build_blocked`, `paths.design_issues`

## Step 0 — Mode

- `FEEDBACK` exists → **Fix mode** (see below).
- otherwise → **Build mode**, from Step 1.

## Step 1 — Load the contract

1. Read `CONTRACT`. It carries `acceptance_criteria`, `testing_mandate`, `covers`,
   `requirements` (each covered id's full statement), `files_to_touch`, `out_of_scope`,
   and `implementation_notes`.
2. Read only the source the contract points at — the files in `files_to_touch` and any
   path named in `implementation_notes`. No wider exploration.
3. A completed `depends_on` task is already merged into the branch your worktree was cut
   from, so its work is present — do not re-verify it.

## Step 2 — Derive the test oracles

From the contract alone — never re-derive from prose elsewhere:

- Each `acceptance_criteria[].check` is a behaviour you must prove; its `check` names the
  inputs and expected output a failing test is written from.
- Each `testing_mandate.unit_tests[].tests[]` names a case and the AC it `covers`. Write
  every one — the negative and boundary cases, not just the positive.
- If `testing_mandate.integration_tests` is non-empty, write those too.

A mandate item or AC you cannot turn into a test from the contract (it contradicts the
source, or names a file outside `files_to_touch`) is a contract problem → Step 3b.

## Step 3 — TDD

Announce each phase.

### Red

1. Write the test for every `testing_mandate` case: set up specific inputs, invoke the
   code under test, assert specific outputs.
2. **Stamp the proving tag.** In each test, place a plain comment carrying the tag AND,
   on the same line, the full statement it proves — verbatim from the contract, no hash,
   any comment style:
   - a component task: `[REQ:<id>] <statement>` where `<id>` is the parent requirement of
     the AC it covers and `<statement>` is that id's entry in the contract's
     `requirements` — a test covering `REQ-1.AC-2` carries
     `// [REQ:REQ-1] When an operator submits credentials, the system shall return a session token within 200ms p95.`
     Every requirement in the contract's `covers` must end with at least one tagged test.
   - an e2e task: `[SYS-TC:<id>] <description>` for each `system_tests[].id`, the
     description taken verbatim from that entry — the end-to-end test proving `SYS-TC-1`
     carries `[SYS-TC:SYS-TC-1] <its system_tests[].description>`.
   That tag line is what the reconcile harvester reads and the reviewer verifies.
3. Check each test against the `wf-testing-anti-patterns` table. A match means restructure
   it before continuing.
4. Run the project's test command for what you changed and **confirm the tests FAIL** for
   the right reason — an assertion or missing-symbol failure, not a compile error
   elsewhere.

A test that passes before any implementation exists is testing the wrong thing —
investigate, do not proceed.

### Green

1. Write the implementation to make the tests pass.
2. Run the tests and fix failures under retry discipline: **max 3 attempts per failure**,
   a different approach each time. On the 2nd, stop and trace the root cause before the
   3rd. On the 3rd, HALT with the exact output and the three approaches tried.

### Refactor

With tests green: no dead code, no debug output, no `TODO`/`HACK`/`FIXME`, no
commented-out code, no suppression directive.

### Step 3b — Contract design issue

If the blocker is the **contract, not the code** — an AC contradicts the source, a
requirement is self-inconsistent, `files_to_touch` cannot satisfy an AC, or the contract
asks for something the source makes impossible — do not retry or work around it. Write
`paths.design_issues` from `assets/design_issues.yaml.tmpl`:

- `fix_kind: contract_amendment` — always. You are a code-layer agent; you never judge
  the spec layer. A wrong requirement upstream surfaces when the contract-fixer it routes
  to escalates.
- one open entry, `task_id` your task, a `summary` of what is unbuildable.

Remove any stale `paths.review_ready`, then HALT and report. The return inspector reads
the open entry and parks the task — you never go on to review.

## Step 4 — Gate

Run `commands.preflight` (pipe to `/tmp/wf-build-preflight.log`, read the log). It must
exit clean. A gate that cannot run because its environment is unavailable is a HALT, not a
pass — do not write `review_ready`.

## Step 5 — Hand off

1. Run the `wf-verification` checklist — every applicable item, with evidence.
2. Commit the source, staging **only** `files_to_touch`:
   ```
   git commit -m "<task-id> <title>"
   ```
   Do not push — the orchestrator merges at the stage boundary.
3. Write `paths.review_ready` from `assets/review_ready.yaml.tmpl` — a presence marker. Its
   presence is the ready-for-review signal; the reviewer judges the committed diff, the
   contract, and the `[REQ]` tags in the tests, never a build self-report, so the file
   carries nothing but the task id.

## Fix mode (`paths.feedback` present)

1. Read `FEEDBACK` — address only its listed failures, each with the minimal change. Do
   not rewrite, and do not touch anything it does not name.
2. If a fix reveals a contract problem (Step 3b criteria), write the design issue and HALT.
3. If a fix needs a file outside `files_to_touch`, HALT with a scope block (below).
4. Re-run the gate (Step 4) and the verification checklist, re-commit, delete
   `FEEDBACK`, then write `paths.review_ready` — in that order.

## Scope-expansion HALT (`paths.build_blocked`)

A file outside `files_to_touch` must change → do not change it. Write `paths.build_blocked`
from `assets/build_blocked.yaml.tmpl` (`required_files`, `reason`), remove any stale
`review_ready`, and HALT. The orchestrator widens the contract and re-dispatches you.

## Halt conditions

- A test fails 3 times with no identified root cause.
- The contract is contradictory or unbuildable as specified → Step 3b.
- `commands.preflight` is unset, or a mandatory gate cannot run (environment down).
- A file outside `files_to_touch` is required → scope block.

Report per the `wf-agent-preamble` halt-report format.

## Telemetry (REQUIRED)

Your final action, always — committed, halted, or escalated: record one session line per
`wf-basics` §2 with `--agent wf-build` and your `--outcome`. If the recorder errors,
continue; telemetry never blocks.
