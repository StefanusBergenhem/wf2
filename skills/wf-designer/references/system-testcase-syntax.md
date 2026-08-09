# System test case syntax (Gherkin-light + black-box)

Contents:
- The Gherkin-light form
- Where a scenario lives, and the duplication rule
- Cover the whole promise
- The end-to-end rule
- Verifiable assertions
- The system-test checklist
- What not to write

A **system test case** is a capability-level, black-box scenario that proves a capability
end-to-end by exercising the assembled path across components. It answers directly to the
capability. Write the human-readable scenario and give it a repo-unique `SYS-TC-<n>` id;
a later stage plans it as its own e2e task and the build writes the executable test tagged
`[SYS-TC:SYS-TC-<n>] <description>` — the durable proof record the register derives on
demand. The shipped scenario set is the capability's **proof**: a scenario the set is
missing keeps the capability open.

## The Gherkin-light form

Write each case as a behaviour scenario from the perspective of an external actor or
interface — not conversational prose. Write it in exactly this YAML shape:

```yaml
    - id: SYS-TC-<n>
      title: "<clear, unique title of the end-to-end behaviour>"
      given: "<the initial system state / preconditions>"
      when: "<the external trigger or action>"
      then: "<the observable, verifiable end state or output>"
```

Filled in:

```yaml
    - id: SYS-TC-7
      title: "Operator downloads yesterday's export"
      given: "an operator has a valid session and yesterday's export completed"
      when: "the operator requests yesterday's export"
      then: "the response carries a signed download URL that resolves to the export file"
```

All four fields are load-bearing — `wf workset check` and `wf stage materialize` read them,
and a scenario missing one is rejected. The title and its three steps are joined into the
one-line description the build stamps onto the `[SYS-TC:]` tag, so end every step on a
complete clause: a step trailing off on a comma or a word like *the*, *and*, *with* fails
materialization.

## Where a scenario lives, and the duplication rule

A scenario is **nested inside the capability or learning entry it proves**, under that
entry's `system_tests:` key. There is no `covers:` field — the nesting names what it proves.

- One capability may need several cases (distinct end-to-end behaviours).
- A case that proves a **second** capability is **duplicated under that entry too, with the
  same `SYS-TC-<n>` id and byte-identical `title`/`given`/`when`/`then`**. One id means one
  test and one tag, satisfying both entries' sets. `wf workset check` errors when two copies
  of an id disagree, so copy the text, never re-word it.
- A learning-driven case with no capability above it nests under the `L-<n>` that drove it.

## Cover the whole promise

Write the **set** against the capability's **entire statement**, not against the work you
are about to cut. The decomposition is what a narrow set inherits its blind spots from.
**Load `{{WF_SKILLS_DIR}}/wf-designer/references/promise-sweep.md` and sweep every class it
lists before calling the set done** — a class you skip is a scenario the adequacy gate will
name as a residual.

Scenarios already shipped for this entry belong in the set too: derive them from the
register and seed them in before writing anything new. A set that omits what already
shipped can never empty against the register, so the entry can never drain.

## The end-to-end rule

A system test runs the **real assembled path** — it exercises the components a capability
traverses together, with **no mocks at the component seams**.

- If proving the behaviour needs the interaction between two components (e.g. auth
  validates, then storage fetches), the test must run both; it cannot stub one out. This is
  the level that catches a `nil`-wired dependency that compiles and silently does nothing.
- If a behaviour can only be verified by mocking an internal seam, it is a component unit or
  integration test, not a system test — drop it from this layer.

## Verifiable assertions (the `then` clause)

The `then` clause asserts a state change or output observable from **outside** the system
boundary (API, database, event bus, UI).

- **State changes** name the verifiable location (e.g. *the user record has
  `is_active = false`*).
- **NFR checks** mirror the metric and threshold from the driving capability (e.g. *the
  response is HTTP 200 within 200 ms p95*).

## The system-test checklist

Run each case through these gates before writing it into the work-set:

- **Black-box.** Interacts only via public interfaces. Smell: the scenario names internal
  classes, private methods, or functions.
- **Deterministic.** No ambient dependency — no hard-coded timestamps, live third-party
  calls, or state left by another test.
- **Single pathway.** One core behaviour per case. Smell: multiple `when`s or unrelated
  actions grouped under one scenario — split them.
- **Traceable.** The entry it nests under is actually exercised by the steps.
- **Complete preconditions.** The `given` accounts for everything the `when` needs to
  succeed.
- **Grounded in a surface that exists.** A `when`/`then` naming a surface — "changed through
  the API", "the write path updates X" — commits every downstream task to it. Open the
  handler and the repository (or drill) before writing the step; when the surface is
  missing, either a stage builds it or the step exercises a lever that does exist.

## What not to write

- **Implementation detail.** Never say *how* code processes data — stick to inputs and
  observable outputs.
- **Mock setups.** No *"given a mocked auth service"* — a mock at the system boundary means
  the boundary is wrong.
- **UI mechanics.** Use abstract actions (*"the user submits valid credentials"*), not
  click-by-click steps, unless the capability is itself about a UI control.
- **Happy-path only.** If a driving capability has an error or unwanted behaviour in its
  promise, write the corresponding error-path case too.
