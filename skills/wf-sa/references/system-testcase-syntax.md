# System test case syntax (Gherkin-light + black-box)

Contents:
- The Gherkin-light form
- Covering capabilities (`Covers:`)
- The end-to-end rule
- Verifiable assertions
- The system-test checklist
- What not to write

A **system test case** is a capability-level, black-box scenario that proves a capability
end-to-end by exercising the assembled path across components. There is no requirement
above it — it answers directly to the capability. You write the human-readable scenario
and give it a repo-unique `SYS-TC-<n>` id; wf-tl plans it as its own e2e task and the
build writes the executable test tagged `[SYS-TC:SYS-TC-<n>]`, which `reconcile` harvests
to confirm the capability is proven. It **covers capabilities**, never component
requirements.

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

- A case covers **at least one `CAP-<n>`** and never lists a component requirement
  (`REQ-<n>`) — a system test proves the user-facing capability, not an internal component
  obligation.
- One capability may need several cases (distinct end-to-end behaviours); one case may
  cover several capabilities.

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

Run each case through these gates before appending it to the design slice:

- **Black-box.** Interacts only via public interfaces. Smell: the scenario names internal
  classes, private methods, or functions.
- **Deterministic.** No ambient dependency — no hard-coded timestamps, live third-party
  calls, or state left by another test.
- **Single pathway.** One core behaviour per case. Smell: multiple `When`s or unrelated
  actions grouped under one scenario — split them.
- **Traceable.** Every `CAP-<n>` in `Covers:` is actually exercised by the steps.
- **Complete preconditions.** The `Given` accounts for everything the `When` needs to
  succeed.

## What not to write

- **Implementation detail.** Never say *how* code processes data — stick to inputs and
  observable outputs.
- **Mock setups.** No *"Given a mocked auth service"* — a mock at the system boundary means
  the boundary is wrong.
- **UI mechanics.** Use abstract actions (*"When the user submits valid credentials"*), not
  click-by-click steps, unless the capability is itself about a UI control.
- **Happy-path only.** If a driving capability has an error / unwanted behaviour, write the
  corresponding error-path case too.
