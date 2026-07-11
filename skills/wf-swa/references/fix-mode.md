# wf-swa — fix mode

The orchestrator dispatched you to resolve **one** design issue. Your envelope carries:

- `di_id`           — the design issue to resolve
- `task_id`         — the task the issue parked
- `di_artifact`     — the design-issues file holding the issue
- `sprint_artifact` — the sprint file holding the task contracts

Resolve only this issue.

## Step 1 — Understand the issue

Read the `di_id` entry in `$di_artifact` — its `summary` says what surfaced. Read the
`task_id` contract in `$sprint_artifact` (including the `requirements[]` statements it
embeds), the source of the component that task owns, and — when the summary implicates
behaviour built by an earlier task — the already-merged code it names. Do not change any
other task except as Step 4 directs.

## Step 2 — Classify

Decide where the defect lives — walk these checks in order and take the first that holds:

- **The contract is wrong** (the requirement is right; the contract diverges from it) — an
  acceptance criterion contradicts another, is untestable or ambiguous as written,
  `files_to_touch` omits a file the task genuinely needs, or a `depends_on` edge is
  missing. → `fix_kind: contract_amendment`; go to Step 3.
- **Already-merged component code is wrong** (the contract and its requirement are both
  right; code merged by an earlier task violates them, and the defect is not in the
  current task's diff) — e.g. a persistence call dropping a field, wiring that was never
  connected. → `fix_kind: component_defect`; go to Step 4.
- **The requirement or an ADR is wrong** (the contract faithfully reflects a spec that is
  itself unbuildable or contradictory) — a SPEC defect, above your altitude. Do **not**
  amend anything: set the `di_id` entry's `fix_kind: spec_amendment` in `$di_artifact`,
  **leave its `status: open`**, and report the one-line reason — the orchestrator
  re-routes the issue to `wf-sa`. Go to Telemetry with outcome `escalated`.
- **None of the above** fits inside one contract plus one follow-up task → leave the issue
  open, halt and report (Halt conditions).

If the entry's recorded `fix_kind` disagrees with your classification, correct it in
`$di_artifact` before acting — the orchestrator routes the aftermath on that field.

## Step 3 — Amend (contract defect only)

Make the **minimum** change to the `task_id` contract in `$sprint_artifact` that makes it
buildable — the smallest edit that resolves the issue. Never touch another task, a
requirement, an acceptance criterion's traced requirement, or a component boundary. Do not
commit: `$sprint_artifact` is transient, and the orchestrator re-extracts the amended
contract when it re-runs the task. Then Step 5.

## Step 4 — Author a follow-up task (component defect only)

The defective code is already merged, so no existing contract can honestly absorb the fix
— author a **new task** that repairs the component, and gate the parked task behind it:

1. Append one task to `$sprint_artifact` with a complete contract per
   `references/task-contract.md`: the next unused id in the sprint's id scheme; `covers` /
   `requirements` naming the requirement id(s) the merged code violates (statements
   verbatim from the task that built it); acceptance criteria that name the defective
   behaviour and the required one; `files_to_touch` limited to the defective component's
   files plus the mandated tests' homes; a `testing_mandate` that proves the fix;
   `depends_on` only what the fix genuinely needs (usually nothing — the code it repairs
   is already merged).
2. Add the new task's id to the **`task_id` task's `depends_on`** — the parked task may
   only re-run after the fix has merged.
3. Do not commit: `$sprint_artifact` is transient.

Then Step 5.

## Step 5 — Mark resolved

Set the `di_id` entry's `status: resolved` in `$di_artifact`. That flag is the signal the
issue is closed.

## Halt conditions

Halt and report with outcome `escalated` if:

- `di_id` is not in `$di_artifact`, or `task_id` is not in `$sprint_artifact`.
- The defect is a spec defect (Step 2) — reclassify it there; do not resolve it.
- Resolving it would require changing more than the one `task_id` contract plus, for a
  component defect, the one follow-up task.
