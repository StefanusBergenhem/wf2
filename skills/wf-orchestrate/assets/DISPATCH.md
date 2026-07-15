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
task_id:         <id>          # null on a slice rejection — no task exists in preparing
di_artifact:     <path>        # the design issue to resolve
sprint_artifact: <paths.sprint>  # named only when a sprint exists on disk; §1a deletes it
                                 # before routing a slice rejection, so that route omits it
```

When it returns, re-read its entry in the DI artifact and route on that entry per
SKILL.md §2b — or §1a for a slice rejection, where the fixer also commits the design it
re-cut, so §1 step 2 finds the clean tree it needs to branch from.

## Closeout — `wf-retrospective` (and any future `wf-*` closeout agent)

```
mode:           <step-name>
sprint_branch:  <name>
```
