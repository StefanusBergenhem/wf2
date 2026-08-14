# Escalation rulings

`paths.decision_prep` holds the designer's brief: the criterion it tripped, the background,
the options with their trade-offs, and the designer's recommendation. The loop is paused.

For each decision block in the file, present it to the human **one per message** in the
`references/decision-brief.md` format and **WAIT** for the answer before the next.

Then record the outcome in `paths.decision_prep` itself, under its `## Ruling` heading —
one block per decision id:

```markdown
## Ruling
### D-1 — <the option chosen>
<the reasoning the human gave, and any constraint they attached to it>
```

- **Do not delete `paths.decision_prep`.** The designer's resume mode consumes the ruling
  and deletes the file; deleting it here strands the paused sprint with no answer.
- **An ADR-threshold ruling gets its ADR now** — write it per **ADR-threshold decisions**
  above, and name it in the ruling block so the designer binds the slice to it.
- **A capability recast** — when the ruling changes what the user needs, apply the agreed
  wording to that capability's entry in `paths.capabilities` and say so in the ruling block.
  Never recast a capability the human did not ratify word-for-word.
- **A charter contradiction** — either the ruling changes the charter (edit it per
  **Charter** above) or it holds and the designer must design within it. Say which.