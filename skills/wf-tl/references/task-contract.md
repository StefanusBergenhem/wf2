# Task contract

The unit the build pipeline executes against. It must be complete enough that a developer
who has read only the contract (plus the source it points to) can build the task correctly,
and a reviewer can judge the diff against it alone.

Contents:
- The four sections
- Materialized fields
- Story
- Acceptance
- Tests on criteria
- Boundaries
- Grounding
- Ground every claim about existing code
- Sizing
- End-to-end tasks

## The four sections

Each fact lives in exactly one section. A fact repeated in two of them is a contradiction
waiting to happen.

```yaml
- id: T3
  increment: 2
  depends_on: [T1]           # task ids that must land first
  covers: [CAP-024]          # and/or L-ids — the driver this task serves
  story: |
    <2–4 paragraphs, FIRST field: what this task builds and why, how the change flows
     through the code, what is new against what already exists.>
  acceptance:
    - id: AC-1
      criterion: <one behaviour: trigger → observable response>
      tests:
        - {level: integration, seam: "HTTP + postgres", target: TestZonesPatch}
    - id: AC-2
      criterion: <a failure or boundary behaviour of the same change>
      tests:
        - {level: unit, target: "internal/zones/patch.go:applyPatch"}
    - id: AC-3
      criterion: <a fact only a gate can observe>
      verified_by: inspection   # name the source fact in the criterion itself
  boundaries: |
    <ONE merged section: what is out of scope, which files are read-only reference,
     which interfaces are fixed. Binding — the build may not cross it, and a reviewer
     rejects a diff that does.>
  grounding:
    - "backend/internal/handlers/zones.go:88 — current mount point"
    - dependency_commits: {T1: <sha>}
  system_tests: [SYS-TC-44]  # e2e tasks only
```

## Materialized fields

Author the fields above only. After writing, and after **every** later edit to the sprint
file, run:

```
python3 <paths.tools>/cli/wf sprint materialize
```

It inlines the increment's narrative into each task envelope and each `system_tests`
entry's scenario text. Never write those by hand and never paraphrase a scenario — hand
copies drift, and `wf sprint check` fails a sprint the materializer has not filled.

## Story

The first field, and the one the build reads first. Two to four paragraphs covering:

- **What this task builds, and why** — the behaviour it delivers, in the increment's terms.
- **How the change flows through the code** — the path a request, event, or call takes
  through the files this task touches, in order.
- **What is new against what exists** — name the current behaviour you are extending,
  replacing, or wiring into.

Write it so the build needs no other prose. A story that only restates the task title is
not a story; `wf sprint check` fails a trivial one.

## Acceptance

The acceptance criteria are the task's requirement layer — there is no separate statement
above them. Write the criteria that prove the task's part of the increment:

- **Complete for the task** — cover the failure and boundary behaviour, not just the happy
  path. Happy-path-only acceptance is an incomplete set.
- **Within the task** — do not write criteria for behaviour the increment does not call
  for. Behaviour you cannot make testable from the source is a slice defect you raise, never
  a guess.
- **Ids are `AC-<n>`, scoped to the task** — the reviewer's and the build feedback loop's
  stable handle.

Phrase every criterion per `references/criterion-syntax.md`.

## Tests on criteria

Each criterion carries either `tests` or `verified_by` — never neither, never both.

- **`level: unit`** — proven against a single target in isolation; `target:
  "<file>:<function>"` names it. Every criterion provable in isolation gets one.
- **`level: integration`** — proven across a real seam; `seam:` names the external
  dependency (database, network, filesystem, queue, cache), the new exposed interface, or
  the cross-component wiring exercised, and `target:` names the test function.
  **Required** on at least one criterion whenever the task crosses such a seam — the seam
  is exercised for real, never mocked.
- A criterion may carry both levels when it needs proving in isolation *and* across the seam.
- **`verified_by: inspection`** — only when no test can observe the criterion: a lint rule,
  a CI gate, a build or config fact. The criterion itself names the source fact
  (*"`.golangci.yml` carries a depguard rule denying `internal/repository` → `internal/compliance`"*)
  so the reviewer can check it. Never use it for something a test could prove — the
  reviewer treats that as an unmet criterion.

**Every `target` must be a test function name that does not already exist in its package.**
Grep the tree before you name one: a duplicate name collides at merge with a sibling task's
test and one of the two silently disappears.

## Boundaries

One prose section holding everything that bounds the build:

- **Out of scope** — what a diligent developer might otherwise absorb: an adjacent behaviour
  belonging to a later increment, a refactor the task does not need, a consumer deliberately
  left untouched (with the reason).
- **Read-only** — files the task reads for reference but must not edit.
- **Fixed interfaces** — a shape from the slice's Interface contracts, or an existing
  signature this task must build to rather than change.

Nothing here may be contradicted by the story, a criterion, or a grounding pointer. Never
waste a line restating that some file is merely unrelated.

## Grounding

Pointers only — never prose that re-explains the source. The build reads the code itself,
and a paraphrase that drifts ships a defect straight into the contract.

- a `file:line` reference with a few words on what is there
  (`membership.go:41 — the deletedEntities CTE exclusion`);
- an ADR id plus the one-clause constraint from its `constraint:` frontmatter line, lifted
  verbatim, never paraphrased from the body;
- the name of a pattern to follow and where it lives;
- `dependency_commits: {T1: <sha>}` — filled at extraction, so the build reads a
  dependency's merged diff directly. When the pattern to follow lives in a `depends_on`
  task's not-yet-built work, no `file:line` exists at cut time: name the task **and** the
  concrete files it will create or edit, never a bare "same pattern as T12".

If a pointer needs more than one sentence, what it holds is behaviour (a criterion), a shape
(the boundaries section), or not the contract's to say.

## Ground every claim about existing code

A contract that asserts how the current system behaves — and is wrong — ships a defect the
build cannot fix in scope. Before a task leaves your hands, resolve every claim in its
story, criteria, boundaries, and grounding to source:

- **a write or read surface** ("changed through the API", "the write path sets X") — open
  the handler *and* the repository and confirm something writes that field. A criterion that
  requires a surface no code exposes cannot be satisfied.
- **a mechanism** ("X aggregates Y", "the evaluator resolves Z from W") — open the function.
- **a fixture reaching a mechanism** ("seed via <file>") — confirm the seeded rows reach the
  path the criterion exercises, not merely a similar one.
- **a gate command's environment** — a `verified_by` gate reads whatever default it is
  pointed at; state the one the task's worktree provides, or it reports drift on a correct
  change.
- **a gate command's invocation** — a committed script runs through its interpreter
  (`bash <script>`, `python3 <script>`), never a bare or `./` path: a script checked in mode
  100644 dies "Permission denied" before it runs.
- **a helper the task must reuse** — open both files and confirm the language lets the
  target see it: a Go test file in the internal package cannot call a helper compiled into
  the external `_test` package. A reuse instruction the compiler forbids leaves the build
  choosing between a duplicate and a halt.
- **retiring or consolidating a helper** — run `wf impact files --symbol <name>` for the
  helper *and* each of its twins (a build-tag variant, a separately-compiled `_test` copy)
  and take the union as the floor for what the task changes.

Cite what you verified with a `<path>:<symbol>` pointer in `grounding`; `wf sprint check`
re-resolves them and errors when the symbol is not there.

## Sizing

Keep a task to roughly **≤ 5 files** and **≤ 250 lines** of change — larger hides gaps and
costs the build/review cycle its leverage. The exception is a **mechanical sweep** (a
rename, a deletion, a signature change fanning out to consumers) that must land in one
atomic commit: keep it whole and say so in `boundaries` (`mechanical: <what makes it one
commit>`). A task needing judgement in more than 5 files is not that exception — split it.
A one-line change standing alone usually belongs merged with an adjacent task; per-task
dispatch overhead is roughly fixed.

## End-to-end tasks

One per `SYS-TC-<n>` the increment completes. Its `system_tests` names the case id (the
materializer fills the scenario text), its `acceptance` is empty — the scenario *is* the
acceptance — and its `depends_on` names the tasks that assemble the path it exercises. It
imports the components without owning them; the build stamps `[SYS-TC:SYS-TC-<n>]` in the
test. This is the level that catches a `nil`-wired dependency that compiles and silently
does nothing; per-component tests, which mock what they wire, never substitute for it.
