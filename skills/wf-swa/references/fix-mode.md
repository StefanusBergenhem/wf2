# wf-swa — fix mode

The orchestrator dispatched you to resolve **one** contract design issue. Your envelope
carries:

- `di_id`           — the design issue to resolve
- `task_id`         — the task whose contract is implicated
- `di_artifact`     — the design-issues file holding the issue
- `sprint_artifact` — the sprint file holding the task contracts

Resolve only this issue, and touch only the `task_id` contract.

## Step 1 — Understand the issue

Read the `di_id` entry in `$di_artifact` — its `summary` says what about the contract is
unbuildable. Read the `task_id` contract in `$sprint_artifact`, and the source of the
component that task owns. Do not read or change any other task.

## Step 2 — Classify

Decide where the defect lives:

- **The contract is wrong** — an acceptance criterion contradicts another, is untestable or
  ambiguous as written, `files_to_touch` omits a file the task genuinely needs, or a
  `depends_on` edge is missing. A CONTRACT defect: go to Step 3.
- **The requirement or an ADR is wrong** — the contract faithfully reflects a requirement
  that is itself unbuildable or contradictory. A SPEC defect, above your altitude. Do **not**
  amend the contract. **Halt and escalate to the human:** report that `di_id` needs `wf-sa`
  (a spec amendment) and the one-line reason. Then go to Telemetry with outcome `escalated`.

## Step 3 — Amend (contract defect only)

Make the **minimum** change to the `task_id` contract in `$sprint_artifact` that makes it
buildable — the smallest edit that resolves the issue. Never touch another task, a
requirement, an acceptance criterion's traced requirement, or a component boundary. Do not
commit: `$sprint_artifact` is transient, and the orchestrator re-extracts the amended
contract when it re-runs the task.

## Step 4 — Mark resolved

Set the `di_id` entry's `status: resolved` in `$di_artifact`. That flag is the signal the
issue is closed.

## Halt conditions

Stop and surface to the user if:

- `di_id` is not in `$di_artifact`, or `task_id` is not in `$sprint_artifact`.
- The defect is a spec defect (Step 2) — escalate to the human for `wf-sa`.
- Resolving it would require changing more than the one `task_id` contract.
