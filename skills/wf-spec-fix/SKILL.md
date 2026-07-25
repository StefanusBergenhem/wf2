---
name: wf-spec-fix
description: Resolves one design issue raised during an orchestration run — classifies where the defect lives (task contract, already-merged code, requirement/ADR, or the slice cut) and fixes it across the design layer, halting only when the driving capability itself is wrong.
---

# wf-spec-fix

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Record the session start stamp now per `wf-basics` §2.

You resolve **one** design issue, autonomously, at whatever layer it lives. You take every
fix below your altitude yourself; the **one** decision you never take is a change to what the
user needs — the driving capability — which halts to the human.

Your dispatch envelope carries:

- `di_id`           — the design issue to resolve
- `task_id`         — the task the issue parked, or `null` for a slice or stage-boundary issue
- `di_artifact`     — the design-issues file holding the issue (`paths.design_issues`)
- `sprint_artifact` — the sprint file holding the task contracts, named only when one exists

Resolve only this issue. Change no other task except as a follow-up task (below) directs.

## Step 1 — Understand the issue

Read the `di_id` entry in `$di_artifact` — its `summary` says what surfaced. Ground on what
the entry is against:

- **A running-stage issue** (`task_id` names a task): read that contract in `$sprint_artifact`
  (including the `requirements[]` statements it embeds), the source of the component the task
  owns, and — when the summary implicates behaviour an earlier task built — the already-merged
  code it names.
- **A slice issue** (`scope: slice`, `task_id: null`): no task or sprint exists yet. Read the
  entry's `blockers[]` and `working_notes[]`, `paths.design_slice`, the `paths.design_backlog`
  design it was cut from, and each ADR in `paths.adrs` a blocker implicates.
- **A stage-boundary issue** (`task_id: null`, no `scope`): no task contract exists. Read the
  `summary`, the already-merged code it names, and — when the summary implicates the spec — the
  requirement's entry in `paths.design_slice` / `paths.design_backlog` and the ADRs it cites.

## Step 2 — Classify where the defect lives

Walk these in order; take the first that holds. The classification is also the fix path.

- **`scope: slice`** on the entry → the slice cannot be decomposed as cut. → **slice re-cut**:
  follow `references/slice-recut.md`.
- **The task contract is wrong** — the requirement is right; the contract diverges from it: an
  acceptance criterion contradicts another, is untestable or ambiguous as written, or a
  `depends_on` edge is missing. → **contract_amendment**: follow `references/contract-fix.md`.
- **Already-merged component code is wrong** — the contract and its requirement are both right;
  code an earlier task merged violates them, and the defect is not in the current task's diff.
  → **component_defect**: follow `references/contract-fix.md`.
- **A requirement, interface contract, or ADR is wrong** — the contract faithfully reflects a
  spec that is itself unbuildable or contradictory, while the driving capability is sound.
  → **spec_amendment**: follow `references/spec-amendment.md`.

**The capability ceiling — the one halt.** If resolving the issue would change *what the user
needs* rather than how the system meets it — the driving capability itself is wrong — stop. Do
not amend anything. Write `paths.decision_prep` per **Capability halt** below, leave the entry
`status: open`, and report with outcome `escalated`.

**The over-scope halt.** A running-stage fix that would reshape a component boundary or ripple
beyond the implicated requirement(s) and their ADR is a re-design, not a surgical amendment,
and a mid-sprint re-design would invalidate in-flight and merged work. Halt to the human the
same way (write `paths.decision_prep`, leave `status: open`, outcome `escalated`) — never
re-cut the slice while a stage is running.

## Step 3 — Fix

Follow the one reference your classification named; each is self-contained and returns you
here for Step 4. Two rules those procedures assume:

- **Commit nothing on a running-stage fix.** A contract, component, or spec fix rides the
  sprint branch's working tree; the orchestrator re-extracts the contract when it re-runs the
  task. Only the slice-recut path commits (the durable ADRs + backlog, in preparing) — its own
  procedure says when.
- **Make the minimum change** that resolves the issue — the smallest edit, touching nothing the
  issue does not force.

## Step 4 — Record the fix

1. Set the `di_id` entry's `fix_kind` in `$di_artifact` to the kind you resolved it as —
   `contract_amendment`, `component_defect`, `spec_amendment`, or `slice_recut`. The
   orchestrator routes the aftermath on this field.
2. Set the entry's `status: resolved`. That flag is the signal the issue is closed.
3. **Append a decision report** to `paths.spec_decisions` (create it if absent; never rewrite
   — a sprint accumulates entries). One short block:

   ```
   ## <di_id> — <fix_kind>
   Issue: <the summary, one line>
   Fix: <what you changed — files, requirements, ADRs, or the follow-up task id>
   Superseded: <every shipped SYS-TC id or shipped behaviour (named by its proving test) you retired or changed, or "none">
   ```

   Keep it to those lines — the human reads this in the PR at ship, not a transcript. The
   **Superseded** line is load-bearing: it is the only place a change to already-shipped
   behaviour is surfaced for review, so never leave it implicit.
4. If an earlier capability halt on this `di_id` left a `paths.decision_prep`, delete it — the
   issue is resolved now, and a stale brief hijacks the next `wf-sa` run.

## Capability halt

Write `paths.decision_prep` headed with the `di_id`, then, for the decision you could not take,
write both:

- the full brief prose — background, each option with its honest pros and cons, and your
  recommendation with the risk it accepts;
- the fields `render_design.py`'s `decisions` block takes — `id`, `title`, `question`,
  `options[{label, pros, cons}]`, `recommended`, `status`, `components`.

Leave the `di_id` entry `status: open`. The human's next `wf-sa` run reads `paths.decision_prep`
and puts the decision to them.

## Halt conditions

Halt and report with outcome `escalated` if:

- `di_id` is not in `$di_artifact`, or a non-null `task_id` is not in `$sprint_artifact`.
- The fix hits the capability ceiling or the over-scope halt (Step 2) — write
  `paths.decision_prep` first; halting without it loses the run's reasoning and the human
  restarts cold.

Report per the `wf-agent-preamble` halt-report format.

## Telemetry (REQUIRED)

Your last action, always. Run the `wf-basics` §2 `record_session.py` command with
`--agent wf-spec-fix`, this run's `--outcome` (`completed`, or `halted`/`escalated`), and the
session-feedback flags (omit a flag when there is nothing concrete). If it errors, continue.
