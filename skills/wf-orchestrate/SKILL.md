---
name: wf-orchestrate
description: Executes a planned change as dependency stages — each stage runs its tasks through a configurable build→review chain in parallel worktrees, resolves design issues and merges at the boundary, then runs configurable closeout steps. Use to execute a design slice or sprint.
---

# wf-orchestrate

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Capture `TS_START` now. You run the whole sprint in one session. Run the CLI as
`python3 <paths.tools>/cli/wf <noun> <verb>`. Resolve these from `.wf/config.yaml`:

- `SPRINT`        = `paths.sprint`         — the task DAG (the brain reads it; you never hand-edit it)
- `CURRENT_TASK`  = `paths.current_task`   — the per-worktree contract you extract for each build
- `commands.preflight` — baseline + per-task gate; `commands.stage_check` — the **heavy checks** run only at a stage boundary (empty → skip)
- `review.passes`      — the ordered build→review chain (e.g. `[wf-review]`); `review.max_attempts`; `review.max_scope_amendments`
- `closeout`           — the ordered end-of-sprint steps (e.g. `[wf-retrospective, ship]`)
- `orchestrate.history_cap` — keep at most this many live history entries

You are the **pipeline controller** — a thin state-machine executor. You do NOT
plan, build, or review. The brain (`wf pipeline …`) decides what is next; the helpers
(`wf orchestrate …`) return a verdict from on-disk artifacts; you dispatch the right
agent and route that verdict. **Route on the helper's verdict, never on a sub-agent's
prose.** Pipe every sub-agent's output to `/tmp/wf-orch-<task>.log` and read only the
verdict the helper emits — never echo a diff or test output into your own context.

## The machine

```
idle
  └─ kickoff ─► preparing ─────► [ STAGE LOOP ] ─────► end_of_sprint ─► idle
                (swa→branch→         ▲      │ sprint_done   (closeout list)
                 compute-stages)     └──────┘ advance-stage

  STAGE LOOP, one iteration:
    start_of_stage ─► running_stage ─► end_of_stage
      (stage-start)    build→review.passes      resolve DIs ─► batch-merge approved
                       per task, parallel        ─► heavy checks ─► advance-stage
                       (DIs park here)
```

## Hard constraints

- **Thin controller.** Drive the loop and assemble envelopes — nothing else.
- **The CLI owns scheduling.** Never compute eligibility, order, or the cap yourself — read `wf pipeline next`.
- **Minimal context.** Each sub-agent gets only its envelope (see DISPATCH.md). Sub-agent output goes to `/tmp`; you retain only verdicts.
- **State is on disk.** You are stateless between dispatches; resume by re-entering the loop — the brain recomputes from `paths.pipeline_state`.
- **Concurrency cap is binding.** Dispatch exactly what `wf pipeline next` returns in `dispatch[]`.
- **Design issues never retry the task.** Park them; resolve at the stage boundary.
- **Merge only at the stage boundary, only the `approved` set.** Never auto-resolve a conflict — HALT.
- **Max `review.max_attempts` build→review cycles per task** — escalate beyond.
- **Append-only history; worktree cleanup is mandatory; one push, at ship.**

## Process

### 0 — Kickoff / resume

Resume safety, every kickoff — an interrupted run leaves orphan state:

```
python3 <paths.tools>/cli/wf orchestrate sweep-transients
python3 <paths.tools>/cli/wf pipeline reclaim-stale
```

`sweep-transients` deletes already-consumed handoffs; `reclaim-stale` flips orphaned
`building`/`reviewing` slots back to `pending` (no attempt bump — an interruption is not
a failure). Then read `wf pipeline current-phase` and resume from where it points:

- **`idle` / unset** → fresh start: `wf pipeline transition --from idle --to preparing`, then the preparing steps.
- **`preparing`** → re-run the preparing steps (all idempotent).
- **`running_stage`** → skip preparing; re-enter the stage loop (§1) — `reclaim-stale` + `next` reconstruct the position from disk.
- **`end_of_sprint`** → re-run the closeout (§2).

**Preparing steps:**

1. **No `$SPRINT`?** Dispatch the `wf-swa` agent (autonomous — it creates the sprint
   from the design slice). If it cannot (it raises a design issue or halts), **HALT and
   report** — the slice is resolved at the `wf-sa` level; re-run after.
2. **Ensure the sprint branch** — see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Sprint branch. Record it in state.
3. `wf pipeline compute-stages` (idempotent; HALTs on a dependency cycle).
4. `wf pipeline transition --from preparing --to running_stage`, then enter the stage loop (§1).

### 1 — The stage loop

On entering each stage, run `wf pipeline stage-start --stage <N>` once (N = the
`stage.index` from `next`). Then repeat:

```
python3 <paths.tools>/cli/wf pipeline next --format json
```

- **`terminal.halt` non-null** → HALT, report its `reason`, stop.
- **`terminal.stage_done` is false** → dispatch the frontier (§1a), apply returns (§ Return protocols), loop.
- **`terminal.stage_done` is true, `repairing` non-empty** → resolve design issues (§1b), loop.
- **`terminal.stage_done` is true, no open issues** → finalize the stage (§1c).

#### 1a — Dispatch the frontier (running_stage)

For each entry in `dispatch[]` (already capped to free slots — dispatch exactly these):

1. Ensure the entry's `worktree` exists — see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Worktree.
2. `wf sprint task <task_id> --write <worktree>/$CURRENT_TASK` — extract its contract.
3. Run `commands.preflight` in the worktree (baseline; pipe to `/tmp`).
4. `wf pipeline dispatch --agent wf-build --task <task_id> --attempt <n>`.
5. Spawn the `wf-build` agent with the **Build envelope** (DISPATCH.md).

Tasks run concurrently in their own worktrees. As each agent returns, apply the
matching Return protocol, then ask `wf pipeline next` again.

#### 1b — Resolve design issues (stage boundary)

For each open issue (`repairing` from `next`):

```
python3 <paths.tools>/cli/wf orchestrate dispatch-fix <di-id>
```

- **exit 0** → dispatch the emitted `subagent_type` (`wf-swa`/`wf-sa`) with the **Fix
  envelope** (DISPATCH.md). On its fix-resolved signal, reset the task to `pending` and
  re-dispatch it at its current attempt (it re-reads its possibly-amended contract). The
  task re-enters running_stage on the next `next`.
- **exit 1** → human gate: HALT, report the issue, stop.

#### 1c — Finalize the stage (end_of_stage)

1. `wf pipeline propagate-blocks` — an escalated task dooms its dependents in later stages.
2. **Batch-merge** the `approved` set (from `next`): for each, merge its worktree to the
   sprint branch (see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Merge), then
   `wf pipeline complete-task <id> --commit <build_sha> --merge <merge_sha>`, then remove
   the worktree. A merge conflict → HALT.
3. **Heavy checks** — if `commands.stage_check` is set, run it on the sprint branch (piped
   to `/tmp`). On failure, run a fix cycle on the sprint branch (the same build→review
   machinery, § Return protocols); escalate at `review.max_attempts`. Empty → skip.
4. `wf pipeline stage-summary --stage <N>`; `wf pipeline stage-end --stage <N>`; if history
   exceeds `orchestrate.history_cap`, `wf pipeline archive-history --cap <history_cap>`.
5. `wf pipeline advance-stage` — **advanced** → return to the stage loop for the next stage
   (run `stage-start` for it); **not advanced** (was the last stage) → § 2.

### 2 — End of sprint (closeout)

`wf pipeline transition --from running_stage --to end_of_sprint`, then run the
`closeout` steps in order. For each entry:

- a `wf-*` agent name → dispatch that agent (e.g. `wf-retrospective`).
- `ship` → the terminal publish: push the sprint branch and open a PR against the base
  branch — see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Ship — then
  `wf pipeline complete-sprint` (archives the plan + final state, resets to `idle`).

Report the sprint summary and the PR URL.

### Telemetry (REQUIRED)

Your last action, always: run the `wf-basics` §2 `record_session.py` command with
`--agent wf-orchestrate`, this run's `--outcome` (`completed`, or `halted`/`escalated`),
and the two feedback answers (omit a flag when there is nothing concrete). The dispatched
sub-agents record their own sessions. If the command errors, continue — telemetry never
blocks.

## Return protocols

After every sub-agent returns, run the helper in order and route on its verdict. The
verdicts and their exact meanings are the helper's contract; the routes are below.

### Build return

```
python3 <paths.tools>/cli/wf orchestrate preserve-uncommitted <worktree> <task-id>
python3 <paths.tools>/cli/wf orchestrate inspect-build-return <worktree> <task-id>
```

| verdict | action |
|:--|:--|
| `ready_for_review` | dispatch `review.passes[0]` (Review envelope); `wf pipeline dispatch --agent <pass> --task <id> --attempt <n> --pass 0`. |
| `design_issue` | park, don't review — record it (Design issues, below); the verdict carries `di_id`. |
| `build_blocked` | scope-amendment (below). |
| `escalate_no_artifacts` | escalate; `wf pipeline block-task <id> --reason <…>`. |

**Scope amendment** (`build_blocked`): read the artifact; classify it with
`wf orchestrate classify-amendment --task-id <id> --diff <diff> [--claim <kind>]`.
`mechanical_follow_on` is free; `feature` consumes one of `review.max_scope_amendments`;
`reject` escalates. Within budget and reasonable → add the files, `wf pipeline
scope-amendment <id> --added <files>`, delete the artifact, re-dispatch build at the
**same** attempt. Otherwise escalate.

### Review-pass return

```
python3 <paths.tools>/cli/wf orchestrate preserve-uncommitted <worktree> <task-id>
python3 <paths.tools>/cli/wf orchestrate inspect-review-return <worktree> <task-id> <build-commit-sha>
```

| verdict | action |
|:--|:--|
| `approved` | the chain advances. If a next pass exists in `review.passes`, dispatch it (`--pass <k+1>`). If this was the **last** pass, `wf pipeline approve-task <id> --commit <build-sha>` (merge happens at the stage boundary). |
| `design_issue` | park, don't retry — record it (Design issues, below); the verdict carries `di_id`. |
| `rejected` | `wf pipeline reject-task <id> --feedback <path>`; re-dispatch build in fix mode. Escalate at `review.max_attempts`. |
| `redispatch_same_attempt` | re-dispatch the same pass at the same attempt (recovery). |
| `defer_to_build_inspector` | fall back to the Build return protocol. |
| `escalate_ambiguous` | escalate; do not retry. |

### Design issues

A build or review writes its design issue to the worktree `design_issues` artifact;
the return inspector surfaces it directly as a **`design_issue` verdict** carrying its
`di_id` — a design-issued build never goes on to review. On that verdict do **not** retry
the task: `wf pipeline record-design-issue <di_id> --task <id> --severity <s> --fix_kind <k>`
(parks the task) and continue other tasks. The issue is resolved at the stage boundary (§1b).

## Dispatch & envelopes

See [DISPATCH.md](assets/DISPATCH.md) for the per-agent context envelopes. Each
sub-agent gets ONLY its envelope — never another agent's definition, pipeline internals,
or out-of-scope files. The pipeline-state shape these verbs read and write is in
[SCHEMAS.md](assets/SCHEMAS.md).

## Halt conditions

Stop and surface to the user when:

- `wf pipeline next` reports `terminal.halt` (dependency cycle, or all remaining work blocked).
- A sub-agent HALTs, or `dispatch-fix` returns the human gate (exit 1).
- A merge conflict, or a heavy-check fix cycle that exhausts `review.max_attempts`.
- `$SPRINT` is absent and `wf-swa` cannot produce one.
- The pipeline-state file is corrupt, or config cannot be resolved.
