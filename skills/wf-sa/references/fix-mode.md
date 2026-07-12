# wf-sa — fix mode

The orchestrator dispatched you to resolve **one** spec design issue. Your envelope
carries:

- `di_id`           — the design issue to resolve
- `task_id`         — the task the issue parked
- `di_artifact`     — the design-issues file holding the issue
- `sprint_artifact` — the sprint file holding the task contracts

Resolve only this issue. Do not commit anything in this mode — the slice is transient,
and the durable spec files you touch ride the working tree; list every amended file in
your report instead.

## Step 1 — Understand the issue

Read the `di_id` entry in `$di_artifact` — its `summary` says what is unbuildable. Read
the `task_id` contract in `$sprint_artifact` (its `requirements[]` carries the statements
at issue), the implicated requirement's entry in `$DESIGN_SLICE` and in its
`$DESIGN_BACKLOG` design, and any ADR in `$ADRS` the contract or slice cites for it.

## Step 2 — Verify it IS a spec defect

Walk these checks in order and take the first that holds:

- **The spec is right and the contract diverges from it** — the requirement/ADR is
  buildable as written; the defect is in an acceptance criterion, `files_to_touch`, or a
  `depends_on` edge. Below your altitude: do **not** amend anything. Set the `di_id`
  entry's `fix_kind: contract_amendment` in `$di_artifact`, **leave its `status: open`**,
  and report the one-line reason — the orchestrator re-routes the issue to `wf-swa`. Go to
  Telemetry (SKILL.md Phase 7) with outcome `escalated`.
- **The spec is right and already-merged code diverges from it** — the requirement/ADR is
  buildable as written; a regression in code an earlier task merged breaks it, and the fix
  is a **source** change (not a reworded statement). Not a spec defect, and never yours to
  write — you do not touch source. Set the `di_id` entry's `fix_kind: component_defect` in
  `$di_artifact`, **leave its `status: open`**, and report the one-line reason — the
  orchestrator re-routes the issue to `wf-swa`, which authors a follow-up build task at
  component altitude. Go to Telemetry (SKILL.md Phase 7) with outcome `escalated`.
- **A requirement or ADR is wrong** — unbuildable, self-contradictory, or contradicting
  the capability that drives it, while that capability itself is sound. A spec defect:
  go to Step 3.
- **The driving capability is wrong** — the amendment would change *what the user needs*,
  not how the system meets it. Above this issue's scope: leave the entry open, halt and
  report to the human (Halt conditions).

## Step 3 — Minimum-amend the spec

**Load `references/requirement-syntax.md` before rewording any requirement**, and
`references/adr-rules.md` before touching an ADR. Make the **smallest** change that
resolves the issue, in every artifact that carries the defective statement:

- the requirement's entry in `$DESIGN_SLICE` **and** the same requirement in its
  `$DESIGN_BACKLOG` design — amend both; the two must not diverge;
- the ADR in `$ADRS`, when the defect is a recorded decision;
- where the amended statement appears verbatim in the `task_id` contract's
  `requirements[]`, update that copy to match — a stale copy reproduces the defect.
  Change nothing else in the contract: its acceptance criteria and task shape are
  `wf-swa`'s.

Never reshape boundaries, add requirements, or redesign beyond the defect — a fix that
needs re-design is a halt (below), not a bigger amendment.

## Step 4 — Mark resolved

Set the `di_id` entry's `status: resolved` in `$di_artifact`. That flag is the signal the
issue is closed.

## Halt conditions

Halt and report with outcome `escalated` if:

- `di_id` is not in `$di_artifact`, or `task_id` is not in `$sprint_artifact`.
- The defect is contract-level or a merged-code source regression (Step 2) — reclassify it
  there; do not resolve it.
- The driving capability itself is wrong — the human owns the *why*.
- The minimum fix would reshape a component boundary or ripple beyond the implicated
  requirement(s) and ADR — that is a re-design, not a surgical amendment.
