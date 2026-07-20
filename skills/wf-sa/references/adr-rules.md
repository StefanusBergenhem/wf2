# ADR rules

An ADR is the persistent record of a load-bearing decision — the choice made, the
alternatives rejected, and why. The threshold below keeps the set high-signal: a
reader can trust that anything in `paths.adrs` was worth preserving the reasoning
for.

Contents:
- The three-condition threshold
- ADR shape
- Status
- governs_components
- Anti-patterns
- Expected density

## The three-condition threshold

A decision earns an ADR **only if all three hold**. Otherwise it is a routine choice
— leave it to the code and an AGENTS.md note.

1. **Load-bearing.** Reversing it ripples beyond local scope — affects multiple
   components or downstream consumers, or locks in a vendor/protocol whose
   replacement is a multi-week project. Swapping two equivalent, easily-replaced
   libraries is *not* load-bearing.

2. **≥ 2 real options existed.** A genuine alternative with real trade-offs was on
   the table. "Use Python" in a Python project is not a decision — no alternative
   existed. "Do nothing" counts only if it was seriously considered.

3. **Contingent on changeable assumptions.** It rests on something that could
   plausibly change — a current scale point, cost structure, vendor, or team skill.
   A decision forced by physics or a hard product constraint is a fact, not an ADR.

## ADR shape

One file per decision at `<paths.adrs>/ADR-NNN-short-slug.md`, from
`assets/adr.md.tmpl`. Frontmatter:

```yaml
---
id: ADR-007
status: accepted            # proposed | accepted | superseded | deprecated
date: 2026-06-14
title: <short noun-phrase>
governs_components: [auth, gateway]   # brief-named components this ADR shapes
traces_to: [CAP-003]                  # the capability, learning, or constraint that drove it
supersedes: null
superseded_by: null
---
```

Body (all sections required):

```markdown
## Context
<The driver — a specific capability, learning, NFR, or external constraint.
 Cited, not generic.>

## Decision
<One crisp sentence: what was chosen.>

## Alternatives
- <Option A> — <one-paragraph trade-off>
- <Option B> — <one-paragraph trade-off>

## Consequences
### Positive
- <Concrete>
### Negative
- <Concrete — at least one>

## Reversibility
<Rollback path with its cost, or the named sign-off authority.>
```

## Status

- `proposed` — the decision is made but the human gate has not approved it, or it is
  contingent on a future condition.
- `accepted` — human-approved and the change is going ahead.
- `accepted → superseded` — a later ADR replaces it. The new ADR's `supersedes`
  names this one; this one's `superseded_by` is filled.
- `accepted → deprecated` — no longer applicable, not replaced (feature removed).

ADRs are **immutable once accepted** — supersede, don't edit.

## governs_components

Each ADR lists the brief-named components it shapes. This is a one-way pointer: it
lets any role about to touch a component find the ADRs governing it
(grep the ADR set by component name). There is no durable reverse index — the
structure record is discover's, and ADRs point *into* it, never the other way.
When a component is renamed or removed, supersede or update the ADRs that named it.

## Anti-patterns

- **ADR for a routine choice.** "We use JSON, not XML" when JSON was the default and
  XML was never a contender.
- **Generic rationale.** "This is clean / scalable / idiomatic." Cite the driver. If
  you can't, it isn't load-bearing or you haven't finished thinking.
- **One alternative.** "Option A vs do nothing" where nobody considered doing
  nothing. Add real alternatives or drop the ADR.
- **Missing Reversibility.** Without an explicit rollback path or sign-off authority,
  reversibility is undetermined.
- **Convention-as-ADR.** A one-line organization rule (*"all calls go through X"*,
  *"no down migrations"*) wrapped in the ADR template. If reversal doesn't ripple,
  it belongs in an AGENTS.md note, not an ADR.
- **Structure-as-ADR.** "The system is built from these modules" is discover's map,
  not a decision. An ADR records the *choice* that shaped the structure (why a
  boundary sits where it does), never the structure itself.

## Expected density

Order of magnitude: a small project 2–6 ADRs; mid-size 10–30; large or long-running
30–80. If you author 10+ in one session, the threshold is being applied too loosely
— most of those "decisions" were defaults or implementation details. Tighten.
