# System test case syntax (Gherkin-light + black-box)

Contents:
- The Gherkin-light form
- Covering capabilities (`Covers:`)
- Cover what the iteration claims
- The end-to-end rule
- Verifiable assertions
- The system-test checklist
- What not to write

A **system test case** is a capability-level, black-box scenario that proves a capability
end-to-end by exercising the assembled path across components. It answers directly to the
capability. Write the human-readable scenario and give it a repo-unique `SYS-TC-<n>` id;
the Tech Lead plans it as its own e2e task and the build writes the executable test tagged
`[SYS-TC:SYS-TC-<n>] <description>` — the durable proof record the register derives on
demand. The shipped scenario set is the capability's **proof**: a scenario the set is
missing keeps the capability open.

## The Gherkin-light form

Write each case as a behaviour scenario from the perspective of an external actor or
interface — not conversational prose.

```gherkin
SYS-TC-<n>: <clear, unique title of the end-to-end behaviour>
Covers: CAP-<n>[, CAP-<m>]

Given <the initial system state / preconditions>
When  <the external trigger or action>
Then  <the observable, verifiable end state or output>
```

- **Given** — the starting state (e.g. *Given an operator has a valid session*).
- **When** — the single external event that drives the behaviour (e.g. *When the operator
  requests yesterday's export*).
- **Then** — the observable assertion (e.g. *Then the system returns a signed download URL
  within 500 ms*).

## Covering capabilities (`Covers:`)

Every case declares the capabilities it proves on a `Covers:` line.

- A case covers **at least one `CAP-<n>`**. One capability may need several cases (distinct
  end-to-end behaviours); one case may cover several capabilities.
- A learning-driven case with no capability above it covers the `L-<n>` that drove it.

## Cover what the iteration claims

Write the **set** against the slice's **claimed scope** for each capability — the promise
this iteration takes on — not against the increments you just cut. The decomposition is
what a narrow set inherits its blind spots from. **Load
`{{WF_SKILLS_DIR}}/wf-designer/references/promise-sweep.md` and sweep every class it lists
before calling the set done** — a class you skip is a scenario the adequacy gate will name
as a residual, at the cost of a re-cut.

A path the claimed scope deliberately leaves for a later iteration gets **no scenario** —
it is stated as left, not silently missing.

## The end-to-end rule

A system test runs the **real assembled path** — it exercises the components a capability
traverses together, with **no mocks at the component seams**.

- If proving the behaviour needs the interaction between two components (e.g. auth
  validates, then storage fetches), the test must run both; it cannot stub one out. This is
  the level that catches a `nil`-wired dependency that compiles and silently does nothing.
- If a behaviour can only be verified by mocking an internal seam, it is a component unit or
  integration test, not a system test — drop it from this layer.

## Verifiable assertions (the `Then` clause)

The `Then` clause asserts a state change or output observable from **outside** the system
boundary (API, database, event bus, UI).

- **State changes** name the verifiable location (e.g. *Then the user record has
  `is_active = false`*).
- **NFR checks** mirror the metric and threshold from the driving capability (e.g. *Then the
  response is HTTP 200 within 200 ms p95*).

## The system-test checklist

Run each case through these gates before putting it in the slice:

- **Black-box.** Interacts only via public interfaces. Smell: the scenario names internal
  classes, private methods, or functions.
- **Deterministic.** No ambient dependency — no hard-coded timestamps, live third-party
  calls, or state left by another test.
- **Single pathway.** One core behaviour per case. Smell: multiple `When`s or unrelated
  actions grouped under one scenario — split them.
- **Traceable.** Every id in `Covers:` is actually exercised by the steps.
- **Complete preconditions.** The `Given` accounts for everything the `When` needs to
  succeed.
- **Grounded in a surface that exists.** A `When`/`Then` naming a surface — "changed through
  the API", "the write path updates X" — commits every downstream task to it. Open the
  handler and the repository (or drill) before writing the step; when the surface is
  missing, either an increment builds it or the step exercises a lever that does exist.

## What not to write

- **Implementation detail.** Never say *how* code processes data — stick to inputs and
  observable outputs.
- **Mock setups.** No *"Given a mocked auth service"* — a mock at the system boundary means
  the boundary is wrong.
- **UI mechanics.** Use abstract actions (*"When the user submits valid credentials"*), not
  click-by-click steps, unless the capability is itself about a UI control.
- **Happy-path only.** If a driving capability has an error / unwanted behaviour inside the
  claimed scope, write the corresponding error-path case too.
