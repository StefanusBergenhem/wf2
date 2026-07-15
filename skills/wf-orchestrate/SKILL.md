---
name: wf-orchestrate
description: Executes a planned change as dependency stages — each stage runs its tasks through a configurable build→review chain in parallel worktrees, merges at the boundary and resolves design issues, then runs configurable closeout steps. Use to execute a design slice or sprint.
---

# wf-orchestrate

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Record the session start stamp now per `wf-basics` §2. You run the whole sprint in one session. Run the CLI as
`python3 <paths.tools>/cli/wf <noun> <verb>`. Resolve these from `.wf/config.yaml`:

- `SPRINT`        = `paths.sprint`         — the task DAG (you never edit it)
- `CURRENT_TASK`  = `paths.current_task`   — the per-worktree contract you extract for each build
- `commands.stage_check` — the **heavy checks** run only at a stage boundary (empty → skip)
- `review.passes`      — the ordered build→review chain (e.g. `[wf-review]`); `review.max_attempts`; `review.max_scope_amendments`
- `closeout`           — the ordered end-of-sprint steps (e.g. `[wf-retrospective, ship]`)
- `orchestrate.history_cap` — keep at most this many live history entries

You are the **pipeline controller** — a thin state-machine executor. You do NOT
plan, build, or review. The brain (`wf pipeline …`) decides what is next; the helpers
(`wf orchestrate …`) return a verdict from on-disk artifacts; you dispatch the right
agent and route that verdict. **Route on the helper's verdict, never on a sub-agent's
prose.** Pipe every sub-agent's output to `/tmp/wf-orch-<task-id>.log` and read only the
verdict the helper emits — never echo a diff or test output into your own context.

## Hard constraints

- **The CLI decides; you route.** Never compute eligibility, order, or the concurrency
  cap yourself — dispatch exactly what `wf pipeline next` returns in `dispatch[]`, and
  re-ask `next` rather than tracking position yourself. Never read or edit
  `paths.pipeline_state` directly, and never re-inspect a worktree or `$SPRINT` to
  second-guess a helper's verdict — read an artifact only where a protocol below
  names the field, and treat the verbs' output as authoritative.
- **You never widen scope.** Never edit `$SPRINT`, a task's contract, or its
  `files_to_touch` on your own judgement — the single permitted contract edit is the
  classifier-gated § Scope amendment; every other scope or design problem routes through
  `dispatch-fix` (§2b). An inline widening bypasses classification and review — the
  amendment ships unaudited.
- **Minimal context.** Each sub-agent gets only its envelope (DISPATCH.md).

## Process

### 0 — Kickoff / resume

Run both, every kickoff:

```
python3 <paths.tools>/cli/wf orchestrate sweep-transients
python3 <paths.tools>/cli/wf pipeline reclaim-stale
```

Then read `wf pipeline current-phase` and resume from where it points:

- **`idle` / unset** → fresh start: `wf pipeline transition --to preparing`, then preparing (§1).
- **`preparing`** → re-run preparing (§1; all idempotent).
- **`running_stage`** → skip preparing; re-enter the stage loop (§2) — `reclaim-stale` + `next` reconstruct the position from disk.
- **`end_of_sprint`** → re-run the closeout (§3).

### 1 — Preparing

1. **Gate `$SPRINT`** before proceeding — presence is never the verdict. Route on the
   first case that matches:
   - `paths.design_issues` holds an `open` entry with `fix_kind: slice_defect` → §1a.
     Never dispatch `wf-swa` against a slice already rejected — it reproduces the same
     rejection and burns a re-design round.
   - `$SPRINT` present and `python3 <paths.tools>/cli/wf sprint check` reports
     `verdict: pass` (exit 0) → step 2.
   - otherwise → dispatch the `wf-swa` agent with the **Preparing envelope** (DISPATCH.md)
     to build the sprint from the design slice — it overwrites an unusable `$SPRINT` —
     then re-run this step **once**, dispatching no second `wf-swa`: `verdict: pass` →
     step 2; anything else → §1a.
2. **Ensure the sprint branch** — see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Sprint branch.
3. `wf pipeline compute-stages` (idempotent; HALTs on a dependency cycle).
4. `wf pipeline transition --to running_stage`, then enter the stage loop (§2).

#### 1a — A rejected slice

Read `paths.design_issues`. No `open` entry with `fix_kind: slice_defect` → **HALT and
report** that wf-swa produced no usable sprint and raised no slice defect; stop.

Otherwise take the **highest-numbered** such entry. **Gate: delete `$SPRINT` before you
route it** — wf-swa decomposed it from the rejected cut. Skip the delete and it survives
the re-cut and still passes `wf sprint check`, which compares ids, not statements — the
build then ships the old requirements. Then, for that entry's `id`:

```
python3 <paths.tools>/cli/wf orchestrate dispatch-fix <di-id>
```

- **exit 1** → human gate: **HALT**, report the emitted `reason`, stop.
- **exit 0** → dispatch `wf-sa` with the **Fix envelope** (DISPATCH.md). When it returns,
  re-read that entry in `paths.design_issues`:
  - `status: resolved` → wf-sa re-cut the slice: return to §1 step 1.
  - still `open` → wf-sa escalated: **HALT and report** that the slice needs a human
    ruling — run `/wf-sa`. Stop.

### 2 — The stage loop

On entering each stage, run `wf pipeline stage-start --stage <N>` once (N = the
`stage.index` from `next`). Then repeat:

```
python3 <paths.tools>/cli/wf pipeline next --format json
```

- **`terminal.halt` non-null** → HALT, report its `reason`, stop.
- **`terminal.stage_done` is false** → dispatch the frontier (§2a), apply returns (§ Return protocols), loop.
- **`terminal.stage_done` is true, `repairing` non-empty** → resolve design issues (§2b), loop.
- **`terminal.stage_done` is true, no open issues** → finalize the stage (§2c).

#### 2a — Dispatch the frontier (running_stage)

For each entry in `dispatch[]`:

1. Ensure the entry's `worktree` exists and is usable — see
   [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Worktree.
2. `wf sprint task <task_id> --write <worktree>/$CURRENT_TASK` — extract its contract.
3. `wf pipeline dispatch --agent wf-build --task <task_id> --attempt <n>`.
4. Spawn the `wf-build` agent with the **Build envelope** (DISPATCH.md).

Tasks run concurrently in their own worktrees. As each agent returns, apply the
matching Return protocol, then ask `wf pipeline next` again.

#### 2b — Resolve design issues (stage boundary)

List the open issues: `wf pipeline unresolved-design-issues --format json`. For each
`di_id` it returns:

```
python3 <paths.tools>/cli/wf orchestrate dispatch-fix <di-id>
```

- **exit 1** → human gate: HALT, report the issue, stop.
- **exit 0** → dispatch the emitted `subagent_type` (`wf-swa`/`wf-sa`) with the **Fix
  envelope** (DISPATCH.md). When the fix agent returns, re-read the issue's entry in
  `paths.design_issues`:
  - `status: resolved` → `wf pipeline resolve-design-issue <di-id>` (it also resets the
    parked task to `pending`), then delete any stale `paths.feedback`,
    `paths.review_ready`, `paths.build_blocked`, or `paths.design_issues` in the task's
    worktree. Then route on the entry's final `fix_kind`:
    - `component_defect` → the fixer appended a follow-up task the parked task now
      depends on: `wf pipeline compute-stages --force`, then return to the stage loop —
      the follow-up dispatches first; the repaired task re-enters in a later stage,
      after the follow-up merges.
    - anything else → re-dispatch the task at its current attempt (it re-reads its
      possibly-amended contract). The task re-enters running_stage on the next `next`.
  - still `open` with a **changed** `fix_kind` (the fixer reclassified the issue) →
    re-run `dispatch-fix <di-id>` and dispatch the route it emits — **at most one such
    re-route per issue**; if the issue is still open after it, treat that as escalated
    (below).
  - still `open`, `fix_kind` unchanged (the fixer escalated) → `wf pipeline block-task
    <task-id> --reason <…>` and report the escalation. Never re-run the task.

#### 2c — Finalize the stage (end_of_stage)

1. `wf pipeline propagate-blocks` — an escalated task dooms its dependents in later stages.
2. **Batch-merge** the `approved` set (from `next`): for each, merge its worktree to the
   sprint branch (see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Merge), then
   `wf pipeline complete-task <id> --commit <build_sha> --merge <merge_sha>`, then remove
   the worktree. A merge conflict → HALT.
3. **Heavy checks** — if `commands.stage_check` is empty, skip. Otherwise run it on the
   sprint branch (piped to `/tmp`); green → step 4. On failure, repair it in a worktree —
   never edit the sprint branch directly, and never judge the failure yourself:
   - **Set up the repair.** Use the synthetic task id `STAGE-FIX-<sprint-id>`; create its
     worktree off the sprint branch (see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) §
     Worktree). Write two artifacts into it: `$CURRENT_TASK` — a contract whose sole
     acceptance criterion is that `commands.stage_check` exits 0 on the sprint branch, with
     empty `covers`/`files_to_touch` and `out_of_scope: [any change not needed to make the
     stage check pass]`; and `paths.feedback` naming the failing command and that it exited
     non-zero (so build runs in fix mode).
   - **Run build → review.** `wf pipeline dispatch --agent wf-build --task
     STAGE-FIX-<sprint-id> --attempt <n>`, spawn `wf-build` (Build envelope), apply the Build
     return protocol; on `ready_for_review` run the review chain and apply the Review-pass
     return protocol. Route the verdict:
     - **`approved`** → merge the worktree to the sprint branch (§ Merge), remove it, re-run
       `commands.stage_check`. Green → step 4. Still red under `review.max_attempts` →
       re-dispatch build at the next attempt (same worktree); at the limit → escalate.
     - **`design_issue`** (build or review) → record it (§ Return protocols → Design issues)
       with `--task STAGE-FIX-<sprint-id>`, resolve it now via §2b, then set up the repair
       afresh (new worktree). Never hand-author a design-issue file.
     - **`build_blocked`, `escalate_no_artifacts`, or attempts exhausted** → remove the
       worktree and escalate the boundary.
4. `wf pipeline stage-summary --stage <N>`; `wf pipeline stage-end --stage <N>`; if history
   exceeds `orchestrate.history_cap`, `wf pipeline archive-history --cap <history_cap>`.
5. `wf pipeline advance-stage` — **advanced** → return to the stage loop for the next stage
   (run `stage-start` for it); **not advanced** (was the last stage) → § 3.

### 3 — End of sprint (closeout)

`wf pipeline transition --to end_of_sprint`, then run the
`closeout` steps in order. For each entry:

- a `wf-*` agent name → dispatch that agent (e.g. `wf-retrospective`).
- `ship` → the terminal publish: push the sprint branch and open a PR against the base
  branch — see [GIT_OPERATIONS.md](assets/GIT_OPERATIONS.md) § Ship — then
  `wf pipeline complete-sprint` (archives the plan + final state, resets to `idle`).

Report the sprint summary and the PR URL.

### Telemetry (REQUIRED)

Your last action, always: run the `wf-basics` §2 `record_session.py` command with
`--agent wf-orchestrate`, this run's `--outcome` (`completed`, or `halted`/`escalated`),
and the session-feedback flags (omit a flag when there is nothing concrete). The dispatched
sub-agents record their own sessions. If the command errors, continue — telemetry never
blocks.

## Return protocols

After every build/review agent returns, run the helpers below in order and route on the
JSON `verdict` they print — never on the sub-agent's prose or the worktree's contents.

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

**Scope amendment** (`build_blocked`) — the one contract edit you may ever make, and
only with the classifier's verdict; widening on your own judgement bypasses
classification and review, so the amendment ships unaudited. Read `required_files` from
the artifact; write the worktree diff (`git -C <worktree> diff HEAD`) to a file; run
`wf orchestrate classify-amendment --task-id <id> --diff <diff-file> [--claim <kind>]`
and route on the bare word it prints:

- `reject` → escalate: `wf pipeline block-task <id> --reason <…>`.
- `feature` → consumes one of `review.max_scope_amendments`; over budget → escalate.
  Within budget: append the `required_files` to `files_to_touch` in the **worktree's**
  `$CURRENT_TASK` (never in `$SPRINT`), `wf pipeline scope-amendment <id> --added
  <files>`, delete the artifact, re-dispatch build at the **same** attempt.
- `mechanical_follow_on` → free: same as `feature` but skip `wf pipeline scope-amendment`.

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

On a `design_issue` verdict (build or review), do **not** retry the task:

1. Copy the open entry the verdict's `di_id` names from the **worktree**
   `paths.design_issues` into the **host** `paths.design_issues` (append to its
   `issues:` list; create the file if absent) — `dispatch-fix` reads only the host file.
2. `wf pipeline record-design-issue <di_id> --task <id> --severity <s> --fix_kind <k>`
   using that entry's values (parks the task).

Continue the other tasks; the issue is resolved at the stage boundary (§2b).

## Dispatch & envelopes

Read [DISPATCH.md](assets/DISPATCH.md) before your first dispatch — it defines the
envelope each agent gets, and the envelope is all it gets.

## Halt conditions

Stop and surface to the user when:

- `wf pipeline next` reports `terminal.halt` (dependency cycle, or all remaining work blocked).
- A sub-agent HALTs, or `dispatch-fix` returns the human gate (exit 1).
- A merge conflict, or a heavy-check fix cycle that exhausts `review.max_attempts`.
- No usable `$SPRINT` and §1a cannot get one: wf-swa raised no slice defect, `dispatch-fix`
  gated the re-design to a human, or wf-sa escalated the rejection.
- The pipeline-state file is corrupt, or config cannot be resolved.
