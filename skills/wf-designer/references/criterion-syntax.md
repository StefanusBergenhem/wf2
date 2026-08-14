# Criterion syntax (EARS-disciplined)

How to phrase an acceptance criterion. Where a criterion goes, what proves it, and when it
is gate-verified is in `references/task-contract.md`.

Contents:
- The shape: trigger → observable response
- The five trigger forms
- One behaviour per criterion
- Named inputs and expected outputs
- Complementary-pair rule
- Measurable NFRs (five elements)
- The self-check
- What not to write

## The shape: trigger → observable response

Every criterion states **what happens** and **what is then observable** — nothing else. The
trigger is the condition or event; the response is a state change or output someone outside
the code can see.

> *When a zone patch omits `capacity`, the stored zone keeps its previous capacity and the
> response returns HTTP 200 with the unchanged value.*

A build agent must be able to write a failing test from the criterion alone.

## The five trigger forms

Use the form the behaviour actually has; do not force a template.

1. **Ubiquitous** — always holds.
   > The `<subject>` `<response>`.
   *Stored credentials are encrypted at rest.*
2. **Event-driven** — fires on a trigger.
   > When `<trigger>`, `<subject>` `<response>`.
   *When the operator submits the login form, the API returns a session token within 200 ms p95.*
3. **State-driven** — holds while a state is active.
   > While `<state>`, `<subject>` `<response>`.
   *While a pipeline is running, new dispatch requests are rejected with 409.*
4. **Unwanted behaviour** — handles an abnormal condition.
   > If `<unwanted condition>`, then `<subject>` `<response>`.
   *If the validation service is unreachable, the handler returns AUTH_UPSTREAM_UNAVAILABLE and does not retry.*
5. **Optional feature** — applies only when something is enabled.
   > Where `<feature enabled>`, `<subject>` `<response>`.
   *Where audit logging is enabled, every authentication attempt emits one audit event.*

## One behaviour per criterion

An "and" or "also" joining two behaviours is two criteria — split them. A criterion proving
two things fails ambiguously, and the feedback loop cannot tell the build which half broke.

The exception is a single behaviour with a compound observable: *"returns 200 **and** the
record is updated"* is one behaviour observed in two places, and stays one criterion.

## Named inputs and expected outputs

Name the concrete input and the concrete expected result. A criterion whose expectation is
"correctly", "appropriately", or "as configured" cannot be turned into an assertion.

- Bad: *"invalid input is handled correctly."*
- Good: *"a patch with `capacity: -1` is rejected with HTTP 422 and the stored zone is unchanged."*

## Complementary-pair rule

For every event- or state-driven criterion, ask what happens when the trigger never fires or
the state never holds. If nothing — fine. If there is a real unwanted condition, write its
unwanted-behaviour counterpart. Missing pairs are the most common coverage gap.

## Measurable NFRs (five elements)

A performance, security, or reliability criterion is verifiable only with all five:

1. **Subject** — what is measured (`response time`).
2. **Metric** — the unit (`milliseconds, p95`).
3. **Threshold** — the bound (`≤ 200`).
4. **Condition** — the operating point (`at 200 concurrent requests`).
5. **Source** — where the number came from (`the stage's NFR envelope`, `SLA`).

> *For 1,000 concurrent users, median response is ≤ 180 ms and p99 ≤ 250 ms over a 5-minute window.*

Not: *"the system performs well under load."*

## The self-check

Run each criterion past these before it ships:

- **Necessary** — the stage's goal or checkpoint actually needs it? No driver, cut it.
- **Unambiguous** — one reading only? Smell: "fast", "as needed", a pronoun with two
  possible referents.
- **Complete** — stands alone, with its trigger and its condition stated? Smell: a dangling
  "TBD".
- **Singular** — one behaviour?
- **Feasible** — the code this task touches can do it within its constraints?
- **Verifiable** — a test could demonstrate it, or a named gate observes it?
- **Correct** — states what the stage needs, not an adjacent guess?

For the **set**: every part of the task's story is covered, no two criteria conflict, and
nothing is stated twice.

## What not to write

- **Mechanism instead of behaviour.** *"value is formatted via toLocaleString"* names the
  implementation; the test then breaks on a refactor that preserves behaviour. Write *"value
  renders as a non-empty locale-formatted date string"*.
- **Smuggled design.** A criterion never names a library, framework, or algorithm as the
  requirement. That is a decision (an ADR), not a behaviour.
- **Subjective adjectives.** No "fast", "scalable", "secure", "user-friendly" without a
  measurable handle.
- **Rationale prose.** Why the behaviour exists belongs in the story, not the criterion.
- **A capability restated.** A criterion at the same altitude as the capability it serves is
  the wrong level — state what *this task's code* must do.
- **Conventions disguised as behaviour.** *"all calls route through X"*, *"never call Y
  directly"* describes how code is organized. That is an ADR constraint or a boundary, not a
  criterion — unless a gate observes it, in which case it is `verified_by: inspection`.
