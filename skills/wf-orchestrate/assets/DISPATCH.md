# Dispatch envelopes

Each sub-agent loads its own SKILL.md (the harness does this when dispatched by name).
You supply **only** the envelope below — the minimum context for that one task. Never
pass another agent's definition, pipeline internals, or out-of-scope files. Sub-agent
output goes to `/tmp/wf-orch-<task>.log`; you keep only the verdict.

All paths are worktree-relative (resolve from the worktree's `.wf/config.yaml`); the agent
runs **in its worktree**.

## Build — `wf-build`

```
task_id:      <id>
worktree:     <path>          # the agent's working directory
contract:     <paths.current_task>   # already written by `sprint task --write`
attempt:      <n>
mode:         build | fix     # fix when paths.feedback is present (a prior review rejected)
```

The build agent reads its contract, writes code + tests, runs `commands.preflight`, and
on success writes `paths.review_ready`. On a scope gap it writes `paths.build_blocked`; on
a design problem, `paths.design_issues`.

## Review pass — `review.passes[k]` (e.g. `wf-review`, later `wf-security-review`)

```
mode:          review
task_id:       <id>
worktree:      <path>
sprint_branch: <name>         # review's diff base; the worktree cannot read host pipeline_state
pass:          <agent-name>   # which pass this is
```

The review agent resolves the contract (`paths.current_task`) from its worktree config and
diffs the task branch against `sprint_branch`. It either advances the chain (an approval
commit) or writes `paths.feedback` (reject) / `paths.design_issues` (design problem). The
next pass in `review.passes` reviews on top of the prior — each reads the same contract and
the current worktree HEAD.

## Fix — `wf-swa` / `wf-sa` (from `dispatch-fix`)

Pass the envelope `dispatch-fix` emits verbatim:

```
mode:            fix
di_id:           <id>
task_id:         <id>
di_artifact:     <path>       # the design issue to resolve
sprint_artifact: <paths.sprint>
```

The fix agent amends in isolation (contract for `wf-swa`, requirement/ADR/slice for
`wf-sa`), commits, and signals fix-resolved. You then reset the task to `pending` and
re-dispatch it.

## Closeout — `wf-retrospective` (and any future `wf-*` closeout agent)

```
pipeline_state: <paths.pipeline_state>
sprint:         <paths.sprint>
design_issues:  <paths.design_issues>
```

The retrospective reads the run's state + git log and writes its report. A future
closeout agent (e.g. docs) receives the analogous read-only pointers it needs.
