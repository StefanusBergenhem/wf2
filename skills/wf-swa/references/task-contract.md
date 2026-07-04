# Task contract

A task contract is the unit the build pipeline executes against. It must be complete
enough that a developer who has read only the contract (plus the source it points to)
can build the task correctly and a reviewer can judge it mechanically.

Contents:
- The fields
- Acceptance criteria
- Testing mandate
- Scope consistency
- Behavior-level wording

## The fields

```yaml
- id: T1
  title: <imperative, one line>
  component: <the component this task belongs to>
  depends_on: []                 # task ids that must land first
  covers: [REQ-1, REQ-2]         # the slice requirements this task satisfies
  requirements:                  # one entry per id in covers — the requirement's text
    - id: REQ-1
      statement: "<the requirement's full EARS statement>"
    - id: REQ-2
      statement: "<...>"
  serves: CAP-NNN                # or L-NNN — the driver behind those requirements
  files_to_touch: [path, path]   # every file the build phase may write
  acceptance_criteria:           # YOU author these — one testable condition per entry
    - id: REQ-1.AC-1
      check: <testable statement with named inputs and expected outputs>
    - id: REQ-1.AC-2
      check: <...>
  testing_mandate:
    unit_tests: [...]            # see below
    integration_tests: [...]     # required when the task touches an external dep
  out_of_scope:
    - <boundary the developer might be tempted to cross>
  implementation_notes:
    - <code patterns found in the source; governing ADR-NNN to respect>
```

`requirements` statements are copied **verbatim** from the design slice — never
paraphrased, never summarized.

## Acceptance criteria

You author the acceptance criteria — the slice gives you requirements, not criteria.
For each requirement a task `covers`, write the testable conditions that prove it:

- **Testable from the source.** Name specific inputs and expected outputs; a build
  agent must be able to write a failing test from the `check` alone, without
  re-reading the requirement.
- **Identified and traced.** Give each criterion an id `REQ-N.AC-M` scoped to its
  requirement, so the testing mandate and the build's test tags reference it and the
  trace AC → requirement → driver stays intact.
- **Complete for the requirement.** Cover the requirement's failure and boundary
  behaviour, not just its happy path — a requirement with only happy-path criteria is
  an incomplete set.
- **Within the requirement.** Do not invent criteria for behaviour the requirement
  doesn't call for — that is scope creep. A requirement you cannot make testable from
  the source is a flag to the SA, not a guess.

Good: *"For 1,000 concurrent users, median response ≤ 180ms and p99 ≤ 250ms over a 5-minute window."*
Bad: *"The system performs well under load."*

## Testing mandate

State the tests the build phase must write — not "tests pass," but *which* tests, at the
right level. Mandate each level where it applies:

- **Unit** — a single target in isolation. **Group by target** when a task touches 2+
  distinct functions/files (a flat list hides which function lacks coverage). **Every
  target needs a positive and a negative case** — happy-path-only is incomplete.
- **Integration** — a real seam the unit tests mock out. Required when `files_to_touch`
  crosses an **external** boundary (database, network, filesystem, queue, cache), **exposes
  a new interface**, or **wires in another component** (an orchestration / composition-root
  task). Exercise the real seam, not a mock; at least one per distinct seam.
- **System (end-to-end)** — comes from the **SA's `SYS-TC-<n>` system test case(s) in the
  design slice**; you do not derive them. Plan each case as its **own e2e task**. The case
  `Covers` a **capability** (`CAP-<n>`), not requirements — so the task `depends_on` the tasks
  building **the requirements whose driver is that capability** (read the drivers off the
  slice's component requirements), which puts it downstream of the assembled path. Its
  `system_tests` is the case; its `files_to_touch` is the e2e test (it exercises — imports —
  the components without owning them); the build stamps `[SYS-TC:SYS-TC-<n>]` in that test.
  This is the level that catches a `nil`-wired dependency that compiles and silently does
  nothing — per-component unit tests, which mock what they wire, never substitute for it.

If a level genuinely does not apply, leave its list empty with a one-line justification in
`implementation_notes` rather than an empty list with no reason.

```yaml
testing_mandate:
  unit_tests:
    - target: "<file>:<function>"
      tests:
        - description: "<input> → <expected output> [positive]"
          covers: REQ-1.AC-1
        - description: "<error input> → <expected error> [negative]"
          covers: REQ-1.AC-2
  integration_tests:
    - description: "<real seam exercised — external dep or cross-component wiring> → <expected>"
      covers: REQ-1.AC-3
  system_tests:                 # an e2e task only: the SA's system test case from the slice
    - id: SYS-TC-1              # the case's repo-unique id; build stamps [SYS-TC:SYS-TC-1]
      description: "<the SA's end-to-end scenario, over the real path> → <expected>"
      covers: [CAP-NNN]         # the capability the case proves — never a component requirement
```

## Scope consistency

Every file a `testing_mandate` item names — as a `target`, in its body, or as the
canonical home for that test type — MUST appear in `files_to_touch`. The build phase
can only write files the contract declares in scope. This includes derived artifacts
(snapshots, generated schemas, golden outputs) when the task changes the source they
capture.

## Behavior-level wording

A `testing_mandate` item describes the **observable behavior or output**, never the
underlying API call or library function — coupling a test to a method name makes it
break on refactors that preserve behavior.

- Bad: *"value is formatted via toLocaleString"* — names the mechanism.
- Good: *"value renders as a non-empty locale-formatted date string"* — names the observable.
- Bad: *"persists via an UPDATE statement"*.
- Good: *"after save, a subsequent read returns the new value"*.

If you find yourself naming a function, method, or library API in a mandate item,
rewrite it to describe what the caller or user observes.
