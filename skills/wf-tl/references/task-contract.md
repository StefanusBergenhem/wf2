# Task contract

A task contract is the unit the build pipeline executes against. It must be complete
enough that a developer who has read only the contract (plus the source it points to)
can build the task correctly and a reviewer can judge the diff against it alone.

Contents:
- The fields
- Materialized fields
- Acceptance criteria
- Tests on criteria
- Scope consistency
- Implementation notes are pointers
- Out of scope

## The fields

```yaml
- id: T1
  title: <imperative, one line>
  depends_on: []                 # task ids that must land first
  covers: [REQ-1, REQ-2]         # the slice requirements this task satisfies
  files_to_touch: [path, path]   # the expected write set — a planning signal, not a fence
  acceptance_criteria:           # YOU author these — one testable condition per entry
    - id: REQ-1.AC-1
      check: <testable statement with named inputs and expected outputs>
      tests:
        - level: unit            # unit | integration
          target: "<file>:<function>"   # unit: the target under test
    - id: REQ-1.AC-2
      check: <failure/boundary behaviour of the same requirement>
      tests:
        - level: integration
          seam: "<the real external dep or cross-component wiring exercised>"
    - id: REQ-1.AC-3
      check: <criterion an existing mechanical gate enforces>
      verified_by: <gate command>  # ONLY when a named gate, not a test, proves it
  system_tests:                  # an e2e task ONLY: the SA's case, by id
    - id: SYS-TC-1
  out_of_scope:
    - <a cross-slice deferral or adjacent behaviour no mechanism forbids>
  implementation_notes:
    - <pointer only: file:line · ADR-NNN + its one-clause constraint · named pattern>
  interface_contract_ref: "<contract name>"  # ONLY when the slice's Interface contracts
                                             # section fixes this task's shape; name the
                                             # slice entry (a list when several apply)
```

## Materialized fields

Author only the thin fields above. After writing (and after **every** later edit to
the sprint file), run:

```
python3 <paths.tools>/cli/wf sprint materialize
```

It inlines, verbatim from the slice: each covered requirement's `requirements[]`
entry (statement + its driver as `serves`), the task-level `serves` union, the
`interface_contract` block each `interface_contract_ref` names, and each
`system_tests` entry's `description` and `covers`. Never write those fields by
hand and never paraphrase a statement — hand-written copies drift, and
`wf sprint check` fails a sprint the materializer has not filled.

One exception: a follow-up task whose `covers` names a requirement **outside
the slice** (a defect in code shipped by an earlier sprint) carries its
`{id, statement, serves}` entry verbatim from the task that built it; the
materializer keeps a carried entry the slice cannot supply.

## Acceptance criteria

You author the acceptance criteria — the slice gives you requirements, not criteria.
For each requirement a task `covers`, write the testable conditions that prove it:

- **Testable from the source.** Name specific inputs and expected outputs; a build
  agent must be able to write a failing test from the `check` alone, without
  re-reading the requirement.
- **Identified and traced.** Give each criterion an id `REQ-N.AC-M` scoped to its
  requirement, so the build's test tags reference it and the trace
  AC → requirement → driver stays intact.
- **Complete for the requirement.** Cover the requirement's failure and boundary
  behaviour, not just its happy path — a requirement with only happy-path criteria is
  an incomplete set.
- **Within the requirement.** Do not invent criteria for behaviour the requirement
  doesn't call for — that is scope creep. A requirement you cannot make testable from
  the source is a design issue you raise per your mode's procedure, never a guess.
- **Behaviour-level wording.** A `check` describes the observable behaviour or
  output, never the underlying API call or library function — coupling a criterion
  to a method name makes its test break on refactors that preserve behaviour.
  - Bad: *"value is formatted via toLocaleString"* — names the mechanism.
  - Good: *"value renders as a non-empty locale-formatted date string"*.
- **Gate-verified, not test-provable.** When a criterion is enforced by an existing
  mechanical gate (a preflight command, a CI check) rather than a test — e.g. "generated
  code is never stale" enforced by a codegen-drift gate — add `verified_by: <the gate
  command>` and no `tests`; it then needs no covering test. Never use `verified_by` for
  a criterion a test could prove — that is dodging the mandate, and the reviewer will
  treat it as an unmet AC.

Good: *"For 1,000 concurrent users, median response ≤ 180ms and p99 ≤ 250ms over a 5-minute window."*
Bad: *"The system performs well under load."*

## Tests on criteria

Each criterion's `tests` entries say **where it is proven** — the level and
placement. The `check` already says what to prove; do not restate it as a test
description.

- **`level: unit`** — the criterion is proven against a single target in isolation;
  `target: "<file>:<function>"` names it. Every AC provable in isolation gets one.
- **`level: integration`** — the criterion is proven across a real seam;
  `seam:` names the external dependency (database, network, filesystem, queue,
  cache), the new exposed interface, or the cross-component wiring exercised.
  **Required** on at least one criterion whenever `files_to_touch` crosses such a
  seam — the seam is exercised for real, never mocked.
- An AC may carry both levels when its behaviour needs proving in isolation and
  across the seam.
- A task whose criteria mandate no test at all (every AC gate-verified) states why
  in `implementation_notes`.

**System (end-to-end)** tests come from the **SA's `SYS-TC-<n>` cases in the design
slice**; you do not derive them. Plan each case as its **own e2e task** whose
`system_tests` names the case id (the materializer fills its text). The case covers a
**capability**, so the task's `depends_on` names the tasks building the requirements
**driven by that capability** (read the drivers off the slice), putting it downstream
of the assembled path. Its `files_to_touch` is the e2e test file (it exercises —
imports — the components without owning them); the build stamps
`[SYS-TC:SYS-TC-<n>]` in that test. This is the level that catches a `nil`-wired
dependency that compiles and silently does nothing — per-component unit tests, which
mock what they wire, never substitute for it.

## Scope consistency

`files_to_touch` is the task's **expected write set** — a planning signal, not a fence.
It feeds two things: **stage ordering** (two tasks whose declared sets overlap need a
dependency edge, or they risk a merge conflict at the stage boundary — an honest set is
what makes the overlap visible) and the **build agent's starting pointers**. The build
may write beyond the set when the task genuinely needs it; what bounds the build is
`covers`, the acceptance criteria, and `out_of_scope`.

**Cut an honest expected set with the impact tool.** For every symbol, field, table, or
column the task changes, and every file it edits, run:

```
python3 <paths.tools>/cli/wf impact files --symbol <changed-symbol> [--symbol ...] [--file <edited-file> ...]
```

Fold the `candidates` entries you expect the build to write into `files_to_touch` — the
consumer files (source and test) plus the companion fan-out (a migration's `down.sql`
sibling, config-declared codegen outputs). The tool's consumer/companion output is what
surfaces cross-task overlaps for ordering edges. A candidate you deliberately exclude
from the task gets a one-line reason in `out_of_scope`.

**A signature or field change carries its consumers.** When a task changes a function
signature, renames or relocates a field, or otherwise alters a shape other code depends
on, declare the callers and fixtures that must update with it in `files_to_touch` — the
impact tool enumerates them. The atomic edit set is the origin file plus its dependents,
whatever component each sits in; declaring the consumers is what turns a cross-task
overlap into an ordering edge instead of a stage-boundary merge conflict.

**Declare each mandated test's file home.** For each AC `tests` entry, the test file it
lives in belongs in `files_to_touch`, placed per the package's test convention — a Go
unit test as `<file>_test.go` beside its target, a Go integration test as its own
`//go:build integration` file, a JS test as `*.test.*` / `*.spec.*` or under
`__tests__/`. Declare a unit `target`'s file too.

**Widening a seam: add alongside, or change in place.** When the slice's **Interface
contracts** section fixes the seam's shape, build to that shape. When it does not, you
decide:

- **Add a new method alongside the existing one** when that keeps the task inside the
  sizing guidance *and* the old method has consumers outside this slice's scope — those
  consumers stay untouched and out of `files_to_touch`.
- **Change the shape in place** when the old shape must die — every consumer then goes into
  `files_to_touch`, per the impact tool above.

A twin you add that must eventually be removed gets its removal as a task **in this sprint**,
carrying the old method's consumers in that task's `files_to_touch`.

## Implementation notes are pointers

A note is a pointer, never prose that re-explains the source:

- a `file:line` reference (`the deletedEntitiesCTE exclusion, membership.go:41`),
- an ADR id plus the one-clause constraint it imposes
  (`ADR-010: the repository must not import internal/compliance`),
- the name of a pattern to follow and where it lives.

Never describe how existing code works or what it will do — the build agent reads
the source itself, and a paraphrase that drifts from the source ships a defect
straight into the contract. Name in a note only files that are in `files_to_touch`
or explicitly read-only reference material — mark a read-only pointer with an attached
`read-only:` prefix (`read-only:core/requirement.go:227`) so `sprint check`'s C9 does not
flag it as a missing write target. If a note needs more than one sentence,
what it holds is either behaviour (an acceptance criterion), a shape (an
`interface_contract_ref`), or not the contract's to say.

## Out of scope

An entry earns its place when it forbids something a diligent developer might otherwise
do: a cross-slice deferral the build might reach for (*"REQ-58 is NOT in this slice"*),
an adjacent behaviour a developer would 'helpfully' absorb, or an impact-tool candidate
deliberately excluded (with the reason). `out_of_scope` is binding — the build may not
touch what it names, and the reviewer rejects a diff that does — so never waste an entry
restating that a file is merely absent from `files_to_touch`.
