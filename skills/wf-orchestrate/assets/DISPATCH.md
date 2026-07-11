# Dispatch envelopes

Each sub-agent loads its own SKILL.md (the harness does this when dispatched by name).
You supply **only** the envelope below — the minimum context for that one task. Never
pass another agent's definition, pipeline internals, or out-of-scope files.

Build and review run **in a worktree** (paths resolve from the worktree's `.wf/config.yaml`);
preparing, fix, and closeout run **host-side** (paths resolve from the host config).

## Build — `wf-build`

```
task_id:      <id>
worktree:     <path>          # the agent's working directory
contract:     <paths.current_task>   # already written by `sprint task --write`
attempt:      <n>
```

## Review pass — `review.passes[k]` (e.g. `wf-review`, later `wf-security-review`)

```
mode:          review
task_id:       <id>
worktree:      <path>
sprint_branch: <name>         # review's diff base; the worktree cannot read host pipeline_state
pass:          <agent-name>   # which pass this is
```

## Preparing — `wf-swa` (build the sprint)

```
mode: default
```

## Fix — `wf-swa` / `wf-sa` (from `dispatch-fix`)

Pass the envelope `dispatch-fix` emits verbatim:

```
mode:            fix
di_id:           <id>
task_id:         <id>
di_artifact:     <path>       # the design issue to resolve
sprint_artifact: <paths.sprint>
```

The fix agent amends in isolation — the task contract, or a follow-up task for a
merged-code defect, for `wf-swa` (no commit; the sprint file is transient), the
requirement/ADR for `wf-sa` — and flips the issue's `status` to `resolved` in the DI
artifact, or reclassifies it (new `fix_kind`, left `open`). Re-read that entry and route
per SKILL.md §2b.

## Closeout — `wf-retrospective` (and any future `wf-*` closeout agent)

```
mode:           <step-name>
sprint_branch:  <name>
```
