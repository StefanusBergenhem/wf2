# Pipeline state & artifact shapes

The orchestrator never edits these by hand — the brain (`wf pipeline …`) and the
helpers (`wf orchestrate …`) read and write them. This is the shape they maintain, so
you can read state when reporting or resuming.

## `paths.pipeline_state` — the live run state (transient, gitignored)

```yaml
current_phase: running_stage        # idle | preparing | running_stage | end_of_sprint
sprint_id: "<id>"
sprint_branch: "<branch>"
sprint_branch_base_sha: "<sha>"

stages:                             # written by compute-stages; current walked by advance-stage
  definitions: [[T1, T2], [T3], [T4]]   # ordered; index 0 = stage 1
  current: 1                        # 1-based
  total: 3

task_states:
  T1:
    status: building                # pending|building|reviewing|dispatching|approved|completed|design_issue|escalated|blocked
    attempt_counter: 0              # build→review cycles spent (cap: review.max_attempts)
    scope_amendment_count: 0        # feature amendments spent (cap: review.max_scope_amendments)
    pass_index: 0                   # which review.passes entry the task is on
    branch: "<task-branch>"
    worktree_path: "<path>"
    build_commit: "<sha>"           # set on approve-task
    merge_commit: "<sha>"           # set on complete-task

blocked_tasks: {T4: {blocked_by: T1}}
design_issues: {DI-1: {issue_id: DI-1, task_id: T2, severity: high, fix_kind: spec_amendment, status: open}}

stage_summaries:
  1:
    timing: {started_at: "...", completed_at: "...", duration_seconds: 120}
    completed: [T1, T2]
    approved: []
    escalated: []
    design_issue: []
    merged: [{task_id: T1, merge_commit: "<sha>"}]

history:                            # append-only; spilled past orchestrate.history_cap to paths.pipeline_history
  - {ts: "...", event: "transition", from_phase: preparing, to_phase: running_stage}
```

**Status meaning for `next`:** `pending` → dispatchable; `building`/`reviewing`/
`dispatching` → occupies a slot; `approved` → passed every review pass, awaiting the
end_of_stage batch merge (settled, not re-dispatched); `design_issue` → parked, resolved
at the stage boundary; `escalated`/`blocked` → doomed. A stage is `stage_done` when none
of its tasks is `pending` or occupying a slot.

## Per-task artifacts — written by the build/review agents in their worktree

Each resolves from `.wf/config.yaml`, written inside the task's worktree, read by the
return-inspection helpers (which resolve them against the worktree root):

| config key | writer | the helper reads it as |
|---|---|---|
| `paths.current_task`  | orchestrator (`sprint task --write`) | the contract the build agent executes |
| `paths.review_ready`  | build | build done, gates green → ready for review |
| `paths.build_blocked` | build | build halted, needs a scope amendment |
| `paths.feedback`      | review | rejection feedback → build fix mode |
| `paths.design_issues` | build/review | a design issue (with `fix_kind`) → `design_issue` verdict → `dispatch-fix` |

The inspect helpers turn the presence/content of these into the verdicts the Return
protocols route on — you never parse them yourself.
