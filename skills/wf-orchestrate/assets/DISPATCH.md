# Dispatch envelopes

Each sub-agent loads its own SKILL.md (the harness does this when dispatched by name).
You supply **only** the envelope below — the minimum context for that one task. Never
pass another agent's definition, pipeline internals, or out-of-scope files. Sub-agent
output goes to `/tmp/wf-orch-<task>.log`; you keep only the verdict.

Build and review run **in a worktree** (paths resolve from the worktree's `.wf/config.yaml`);
preparing, fix, and closeout run **host-side** (paths resolve from the host config).

## Build — `wf-build`

```
task_id:      <id>
worktree:     <path>          # the agent's working directory
contract:     <paths.current_task>   # already written by `sprint task --write`
attempt:      <n>
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

## Preparing — `wf-swa` (build the sprint)

```
mode: default
```

The swa agent resolves its inputs from config (design slice, ADRs, and the components'
source), authors the acceptance criteria, decomposes them into the task DAG, and writes
`paths.sprint`. It runs
autonomously; an unbuildable input (absent slice, untestable requirement) halts with outcome
`escalated` instead of guessing.

## Fix — `wf-swa` / `wf-sa` (from `dispatch-fix`)

Pass the envelope `dispatch-fix` emits verbatim:

```
mode:            fix
di_id:           <id>
task_id:         <id>
di_artifact:     <path>       # the design issue to resolve
sprint_artifact: <paths.sprint>
```

The fix agent amends in isolation — the task contract for `wf-swa` (no commit; the
sprint file is transient), the requirement/ADR for `wf-sa` — and flips the issue's
`status` to `resolved` in the DI artifact. Re-read that entry and route per SKILL.md §2b.

## Closeout — `wf-retrospective` (and any future `wf-*` closeout agent)

```
mode:           <step-name>
sprint_branch:  <name>
```

The retrospective resolves its inputs from config — `paths.telemetry` (session feedback) and
`paths.pipeline_state` (the run's cross-task execution) — distils them into the learnings
streams, and returns a transient run summary (nothing durable beyond the streams). A future
closeout agent (e.g. docs) resolves the analogous inputs it needs.
