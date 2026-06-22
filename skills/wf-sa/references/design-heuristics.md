# Design heuristics — the architecture-fitness lens

Apply these while shaping a change's architecture: each is a **question to ask at
the point of a decision** and the **smell that should fire it**. They inform the
call you make.

Judge only the **change's surface** — the components the change touches and any it
introduces — never the whole repo.

Contents:
- Boundary & responsibility
- Ownership
- Dependency direction
- Interface stability
- Extension over modification
- Allocation completeness
- Move discipline

## Boundary & responsibility (SRP)

**Ask:** can you name this component's single responsibility in one line? **Smell:**
the one-liner needs an "and", or the change makes the component do a second
unrelated thing. → The boundary is wrong; consider a split. A component you cannot
describe crisply is one whose contents will keep growing.

## Ownership

**Ask:** for each concept the change touches, exactly one component owns it?
**Smells:** two components both claim it (overlap — pick an owner, the other depends
on it) or none does (orphan — a new home, or assign it to the best-fit existing
component). A requirement with no clean owner is the loudest version of this smell.

## Dependency direction (DIP, acyclicity)

**Ask:** do the dependencies this change adds point one way, from the more volatile
toward the more stable? **Smells:** a new cycle between components; a stable,
widely-used component made to depend on a volatile detail. → Invert the dependency
(depend on an abstraction the volatile side implements) or move the concept.

## Interface stability (ISP)

**Ask:** for a component many others depend on, is its exposed surface narrow and
stable? **Smell:** the change widens a high-fan-in component's surface, or churns an
existing one. → A change there ripples to every dependent; prefer a new, focused
seam over bloating the shared one.

## Extension over modification (OCP)

**Ask:** can the new behaviour be added by extending a seam rather than modifying a
widely-depended-on component? **Smell:** the change edits the internals of something
many components rely on. → Prefer adding a component or a well-defined extension
point; reserve in-place modification for when extension genuinely doesn't fit.

## Allocation completeness

**Ask:** does every component the change **traverses** own at least one requirement — the
core logic, its orchestration (the coordinating handler), and its composition root (where
dependencies are wired)? **Smell:** a behaviour that must be observable end-to-end carries
requirements only on its core-logic component, leaving the wiring unallocated. → An
untouched composition root is the gap that ships a feature half-built — a `nil`-wired
dependency that compiles and silently does nothing. Allocate the full delivery path, the
composition root included.

## Move discipline

A split, merge, new component, or dependency change must **improve a fitness measure
above** — sharper responsibility, lower coupling, a cleaner dependency direction. If
a proposed move doesn't measurably improve one of these, don't make it: churn
without a fitness gain is cost with no benefit. Every move you do make is a
load-bearing decision (candidate ADR) and a reason to re-run discover afterwards.
