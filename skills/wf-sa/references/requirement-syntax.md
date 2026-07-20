# Requirement syntax (EARS-light + INCOSE)

Contents:
- The five EARS forms
- One owning component
- Complementary-pair rule
- Measurable NFRs (five elements)
- Where a cross-cutting requirement lives
- The INCOSE checklist
- What not to write

Each requirement is a **component requirement**: an EARS statement that one named
component owns, tracing to the capability or learning that drove it. Give each a
**repo-unique id** (`REQ-<n>`, monotonic over the whole repo, never reused) — a proving
test tags it `[REQ:REQ-<n>]` and reconcile matches the tag to confirm it is built, so a
design-local id would collide with a retired design's lingering tag. You write the
requirement; the acceptance criteria that operationalize it are the Tech Lead's.

## The five EARS forms

Easy Approach to Requirements Syntax. Use the forms; don't enforce verbose template
variants.

1. **Ubiquitous** — always applies.
   > The system shall `<response>`.
   *The system shall persist credentials in encrypted form.*

2. **Event-driven** — fires on a trigger.
   > When `<trigger>`, the system shall `<response>`.
   *When the user submits the login form, the system shall validate the credentials within 200ms p95.*

3. **State-driven** — holds while a state is active.
   > While `<state>`, the system shall `<response>`.
   *While a pipeline is running, the system shall reject new dispatch requests.*

4. **Unwanted-behavior** — handles an abnormal condition.
   > If `<unwanted-condition>`, then the system shall `<response>`.
   *If the validation service is unreachable, then the system shall return AUTH_UPSTREAM_UNAVAILABLE and not retry automatically.*

5. **Optional-feature** — applies only when a feature is enabled.
   > Where `<feature-enabled>`, the system shall `<response>`.
   *Where audit-logging is enabled, the system shall emit an event for every authentication attempt.*

## One owning component

Every requirement names exactly one component as its **owner** — the component
responsible for satisfying it end-to-end. The owner is part of the requirement, not
a later allocation step. Record the owner and the driver alongside the statement:

```
REQ-2  (owner: auth · CAP-003)
  When an operator submits credentials, the system shall return a session token
  within 200ms p95.
```

If you cannot name a single owner, the boundary is wrong — return to the architecture
(that is the ownership smell in `design-heuristics.md`). A behavior that genuinely
spans two components is a sign to split it into one requirement per component, each
owning its part.

## Complementary-pair rule

For every event- or state-driven requirement, ask: what if the trigger never fires,
or the state never holds? If "nothing happens" — fine. If there is a real unwanted
condition, write its unwanted-behavior counterpart. Missing pairs are the most
common coverage gap.

## Measurable NFRs (five elements)

A non-functional requirement (performance, security, reliability) is only verifiable
if it carries all five:

1. **Subject** — what is measured (`response time`).
2. **Metric** — the unit (`milliseconds, p95`).
3. **Threshold** — the bound (`≤ 200`).
4. **Condition** — the operating point (`at 200 concurrent requests`).
5. **Source** — where the number came from (`SLA`, `engineering judgment`).

If a number is genuinely unknown, discuss with human.

## Where a cross-cutting requirement lives

An NFR with no single component owner is **not** a system-level requirement — that
altitude is the capability. Place it by kind:

- A **testable cross-cutting behavior** (end-to-end latency, an availability target
  observed at the boundary) → the component that owns that boundary, the entry point
  that carries it. It owns the number even though others contribute.
- A **budget or constraint** (a latency budget split across components, a technology
  bound) → an ADR. A budget is a decision, not a behavior; record it where decisions
  live.

## The INCOSE checklist

Run each requirement past these before it ships (INCOSE *Guide for Writing
Requirements*, the individual-statement characteristics). Each is a question and the
smell that fails it:

- **C1 Necessary** — a driver (capability/learning) actually needs it? Smell: no
  traceable driver — cut it.
- **C2 Appropriate** — at component altitude? Smell: it restates a capability (too
  high) or names files/functions (too low).
- **C3 Unambiguous** — one reading only? Smell: "fast", "as needed", a pronoun with
  two possible referents.
- **C4 Complete** — stands alone? Smell: a dangling "TBD", a missing trigger or
  condition.
- **C5 Singular** — one requirement? Smell: an "and"/"also" joining two behaviors —
  split them.
- **C6 Feasible** — the owning component can do it within known constraints?
- **C7 Verifiable** — worded so a test could demonstrate it? Smell: no observable
  outcome. A well-formed, observable statement is verifiable; you do not need the
  acceptance criteria written to satisfy this — those are the Tech Lead's.
- **C8 Correct** — states what the driver actually needs, not an adjacent guess?
- **C9 Conforming** — uses one of the five EARS forms?

For the **set**, check it is complete (every in-scope driver covered), consistent (no
two requirements conflict), and free of duplication.

## What not to write

- **Smuggled design.** No requirement names a library, framework, algorithm, or
  technology. *"The system shall use PostgreSQL"* is a decision (an ADR), not a
  requirement.
- **Subjective adjectives.** No "fast", "scalable", "secure", "user-friendly" without
  a measurable handle.
- **Rationale prose.** Why a requirement exists belongs in the ADR it traces to, not
  inline.
- **A capability restated.** If the statement reads at the same altitude as its
  driving capability, it is the wrong level — decompose it to what a *component* must
  do, or drop it.
- **Conventions disguised as requirements.** *"shall route every call through X"*,
  *"shall not call Y directly"* describes how code is organized, not a behavior the
  system promises a caller. That is a convention or an ADR, not a requirement.
