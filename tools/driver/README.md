# wf-driver — the loop program

The Python program that executes wf's continuous delivery loop. One invocation runs
sprint after sprint — design, build, review, merge, close, ship — until a stop rule
fires. Orchestration is mechanical, so a script does it rather than a role.

```sh
python3 .wf/tools/driver/wf-driver              # run continuously
python3 .wf/tools/driver/wf-driver --once       # exactly one sprint (bring-up)
python3 .wf/tools/driver/wf-driver --dry-run    # print the planned dispatches only
python3 .wf/tools/driver/wf-driver --verbose    # also print every `wf` verb and its rc
python3 .wf/tools/driver/wf-driver --quiet      # drop the still-alive heartbeat
python3 .wf/tools/driver/wf-driver --config path/to/.wf/config.yaml --max-sprints 3
```

Exit code `0` = a clean stop (a stop rule, or the sprint limit). `1` = a halt: the
loop hit something it must not decide on its own. `130` = interrupted. All three
leave the position on disk; re-running resumes it.

## Following a run

A dispatch writes nothing to the terminal — its output goes to a log the driver
never reads — so the loop narrates itself instead (`progress.py`). Every line carries
elapsed-since-start; `──` marks a position in the phase machine, `▶`/`✔`/`✖` open and
close something long-running, and `·` is a heartbeat saying it is still alive:

```
[wf-driver 0:02:11] ── designing — the design role cuts the next stage
[wf-driver 0:02:11]   ▶ wf-designer
[wf-driver 0:03:11]     · wf-designer still running — 1m00s / 2h00m budget
[wf-driver 0:14:52]   ✔ wf-designer — 12m41s
[wf-driver 0:14:53]   ✔ stage check green · stage 7 · serves CAP-004
```

The heartbeat's budget is the timeout that will kill the dispatch
(`driver.agent_timeout_s`), so a wedged role is visible long before it is reaped.
`^C` prints what was in flight and how long it had been running, then exits `130`.

## The process contract

A role is launched by the config template `driver.agent_cmd` — or by its own entry in
`driver.agent_cmd_overrides`, which is how one role gets a different model — with
`{prompt}` substituted (escaped for whatever quoting the template puts around it). The
prompt names the role's installed file and its envelope fields:

```
Read /repo/.claude/agents/wf-build.md and follow it.
task_id: S7-T3
worktree: /repo/.wf/transient/worktrees/s1-S7-T3
contract: .wf/transient/current-task.yaml
attempt: 0
```

**The contract is exit code + artifacts on disk.** Agent stdout is streamed to
`<paths.transient>/driver-logs/` and never read. Every verdict comes from a `wf`
verb's JSON (`pipeline next`, `stage check`, `pipeline capability-complete`,
`orchestrate inspect-*`) or from an artifact (`paths.design_issues`, an adequacy
digest, `paths.decision_prep`). Nothing routes on prose.

Each dispatch, routing decision and stop appends one row to `paths.telemetry`:
`{kind: driver_event, agent, role, event, mode, sprint, stage, task, rc,
duration_s, started_at, ended_at, ts}`. `agent` mirrors `role` because that is the
field the telemetry reader classifies on, and every row carries an ISO-8601 UTC span
(zero-length for an instantaneous event) so the usage-row join is exact, not fuzzy.
The `stage_done` row also carries `width` (the stage's task count) and `merged`:
width cannot be gated — a lower bound would be wrong — so it is watched, and a trend
toward one task per stage means design dispatches are compounding against a chain the
role is not cutting around.

## Phases

State lives in `driver.state_file`; each phase is entered (and written) before it
acts, so a restart re-enters it.

| phase | what runs | leaves |
|---|---|---|
| `sprint_start` | clean-tree gate; branch `sprint/s<N>` off the stack tip; carry the telemetry rows onto it; reset `paths.pr_body`; `sweep-transients` + `reclaim-stale`; `wf-discover` | `designing` |
| `designing` | `wf-designer` (no mode — one role, one sitting, one artifact) → `wf stage materialize` + `wf stage check`; a red gate records the findings as a design issue and sends the role back in; a dispatch that grew the work-set's scenario count without cutting is dispatched again, bounded by `SCENARIO_ROUNDS` | `stage_run`, or a pause |
| `stage_run` | `pipeline load-stage` → the frontier in parallel worktrees, `driver.max_parallel` at a time with each slot refilled as its task finishes → build→review per task → batch merge → the close | `designing` (cut the next) or `closeout` |
| `closeout` | the `closeout` config steps, each banked on disk as it finishes: `wf-*` roles, then `ship` (`complete-sprint`, commit, push, `gh pr create`) | `sprint_start` |
| `awaiting_ruling` | on restart: `wf-designer` (resume) once `paths.decision_prep` carries a ruling, then `resolve-design-issue` for the brief's `di_id` (the resume run closes only the host entry; the run-state twin is what parks the task). A brief that is *already gone* means the ruling was consumed by a round that stopped before the phase moved — it cuts rather than concluding | `designing`, which runs its rounds and its gate: the ruling round is one design dispatch, not the whole cut |

`designing` and `stage_run` alternate until the close says the PR is ready. They stay
separate and named because a resume has to say which of them it was suspended in, and
because `designing` is where the escalation gate and the work-exhaustion exit live — a
compound state cannot answer either question.

**The stage close is where the sprint ends.** After the heavy checks it appends the
stage's block to `paths.pr_body`, archives and deletes `paths.stage`, fires the
completion gate, prunes the drill cache, then decides: ship when the merged tree crossed
an end-to-end checkpoint (a stage that landed at least one SYS-TC — measured on what
*merged*, not on what the cut intended), or at `driver.max_stages_per_sprint`, whichever
comes first. Otherwise it goes back to `designing` for the next cut.

**A blocked task dooms nothing.** There are no edges inside a stage, so the stage closes
with the tasks that merged and the blocked one re-enters at the next cut: both block
sites raise a design issue naming the task's branch, the design role drains it by
authoring the successor and naming it (`task:`), and the driver then cuts that
successor's worktree from the old branch and rebases it onto the sprint tip — passing
`--prior-attempt` so the build's Red phase does not read already-passing code as a
vacuous test. On a rebase conflict it falls back to a fresh worktree and passes nothing:
carrying the old work is opportunistic, never required.

The clean-tree gate has exactly one carve-out: a tree whose *only* dirt is
`paths.telemetry`. That file is committed and every role and Stop hook appends to it
after the sprint's last commit, so its rows are carried into the new sprint instead of
halting the loop. Any other path is still `dirty_tree`, and the gate runs *before* the
branch is cut so a halt leaves no stray `sprint/s<N>` behind to burn the next ordinal.
The carry-over commit ("telemetry: carry rows into sprint N") runs *after* `checkout -b`,
which takes the uncommitted sink with it: HEAD is still on the previous sprint's branch
until then, and a commit there un-merges an already-shipped sprint, strands it on the
stack, and points the next PR at a base the forge has deleted. `ship` stages the sink
with the close, so the sprint's own rows go out with its PR.

Each loop that re-runs a role owns its own budget, as a constant in the module that
runs it: `phases.STAGE_GATE_ATTEMPTS`, `stages.STAGE_REPAIR_ATTEMPTS`,
`stages.REDISPATCH_ATTEMPTS`. `review.max_attempts` bounds one task's build→review
chain and nothing else.

Inside a stage there is one pass: `pipeline next` gives the frontier, each task gets a
worktree + its envelope (`stage task --write`) + `wf-build`, then the `review.passes`
chain; the approved set batch-merges into the sprint branch. `driver.max_parallel` is a
live ceiling on the tasks in flight, not a batch size — the frontier is re-read the
moment one finishes and its slot is refilled at once, so a wide stage never runs its
tail one task wide with the rest of the slots idle. The driver counts the free slots
against what *it* has running, not against the pipeline's in-flight set: a task enters
that set only when its thread reaches `pipeline dispatch`, a worktree and a provision
after it was picked up, so a frontier read in that window still offers it. The close
then runs
`commands.stage_check` over the integrated tree — at **every** stage close, so an
integration break is caught at the stage that caused it — repairing through
`wf-stage-repair`, whose envelope carries the stage number and its observable
checkpoint. A check that stays red after
`STAGE_REPAIR_ATTEMPTS` is a true halt naming its log: the stage has already merged, so
red means the sprint branch is broken, and under the PR cadence the branch may already
be pushed.

**Drill-cache staleness is derived, not judged.** Each `wf-drill` digest header carries
`**Taken at:** <sha>` and `**Targets:** <paths>`; at every stage close the driver drops
any digest whose targets changed since that commit. A digest naming no targets, or one
whose commit git cannot reach, is stale by definition — it cannot be checked at all.
Adequacy digests share the directory and are never swept: they are verdicts, and the
park count is derived by counting them.

Sprint ids and stack depth are **derived from git**, never stored: the next id is one
past the highest `sprint/s<N>` branch, and the stack is the sprint branches not yet
merged into `project.base_branch` *or its fetched remote twin*. A sprint PR targets the
branch below it in the stack, else the base branch.

Every loop iteration begins with `git fetch --prune origin <base>` — PRs merge on the
forge, so without it the local base never learns, the stack never drains and the depth
cap pauses forever. An unreachable origin is a `warn` row, not a stop: the loop then
derives from local state. When the stack is empty the next sprint branches from
`origin/<base>` if the fetch left it ahead of the local branch.

## Stop rules

| rule | trigger | effect |
|---|---|---|
| escalation | `paths.decision_prep` exists — written by the design role at a cut | pause in `awaiting_ruling`, with the phase it interrupted recorded; a ruling resumes the cut, and nothing consumes the brief before then |
| work exhaustion | no capability or learning entry left that is not `status: parked` | clean exit — but whatever stages already merged ship as a PR first, so reviewed green work never strands on a branch nobody is looking at |
| no stage cut | the design role ran its rounds and wrote no `paths.stage`, while the work-set still holds open, unparked entries | same clean exit, same ship-first. A separate reason because "nothing is in scope" is a claim about the *work-set*: reported as work exhaustion it reads as a drained backlog, and the counts it names say otherwise |
| manual stop | `driver.stop_file` exists | seen at a stage close: ship the sprint in flight, exit |
| stack depth | unmerged sprint branches ≥ `driver.max_unmerged_sprints` | pause until PRs merge |
| request-changes | `driver.review_state_cmd` output contains `CHANGES_REQUESTED` | same as manual stop |
| launch failed | a role exited non-zero **and** left nothing to route on | pause, quoting the harness's own last log line; re-run to continue from the same position |

## Resuming

The position is on disk (`driver.state_file`), so re-running after any interruption —
`^C`, a crash, a session limit — re-enters at the recorded phase. Two things make that
safe, and both run on **every** re-entry past `sprint_start`, which is where they were
missing:

- **The position is verified against git.** A recorded `sprint_branch` git does not have
  is a fiction, and every commit, worktree and merge would silently land on whatever
  HEAD happens to be. That halts as `sprint_branch_missing`, naming both ways out.
  Correspondingly, `sprint_start` cuts the branch *before* recording the position, so
  the window that produces such a state never opens.
- **The hygiene runs.** `sweep-transients` and `pipeline reclaim-stale` used to live
  only inside `sprint_start` — but an interruption inside a stage is exactly where a
  task is left holding a slot no one will release, and that re-entry never passes
  through `sprint_start`.
- **A stage already closed resumes into the next cut.** The close archives and deletes
  `paths.stage`, so a run interrupted between that and the phase write comes back to
  `stage_run` with nothing to load. Its work is merged; what is missing is the next cut,
  not a halt.

**A non-zero exit is not, on its own, a failure** and is deliberately never checked as
one: a role can exit badly having already written a perfectly good artifact. It is
consulted only where the caller has established that the role produced *nothing* — there
it is the difference between "the design role cut nothing" and "the design role never
ran". The one exception is `_repair_merge`, where a non-zero exit already *is* the
"did not resolve" signal. The stronger form of the same rule guards the no-stage ending
itself: reaching it with *no dispatch at all* behind it halts as `no_design_dispatch`,
because a verdict about the work needs a role to have produced it — a resume that
launched nothing and concluded from the empty disk is how the loop once wedged.

**A rate limit is a wait, not a verdict.** A harness that refuses the launch leaves the
same empty worktree as a role that mis-stepped, so every caller reading "nothing to
route on" would otherwise have to tell the two apart — and the one that didn't spent a
task's whole review chain in 49 seconds and blocked it for "not settling". The refusal
is absorbed in `Dispatcher.launch` instead, below every caller: the reset time is read
out of the harness's own log, the wait runs to two minutes past it
(`RATE_LIMIT_MARGIN_S` — landing *on* the reset races the window roll), and the same
command goes again. Bounds: `RATE_LIMIT_WAITS` per dispatch, each capped by
`driver.rate_limit_max_wait_s`; a limit that outlasts the cap (a weekly one) is not
slept at all but handed back, so a human sees it instead of the loop burning hours to
reach the same refusal. `driver.stop_file` is polled every `RATE_LIMIT_POLL_S` while
waiting — the wait is the longest thing the loop does, and a stop must not queue behind
it. Each attempt keeps its own log and its own telemetry row; the wait itself is a
`rate_limit_wait` row.

## Config keys

Read from `.wf/config.yaml` through the CLI's `common` module — the driver has no
defaults of its own; a missing key is a named, fatal error.

- `driver.agent_cmd`, `driver.max_parallel`, `driver.max_unmerged_sprints`,
  `driver.stop_file` — as before.
- `driver.agent_cmd_overrides` — optional map, role → launch template, for a role that
  needs a different model or harness flag; a role with no entry uses `agent_cmd`.
- `driver.state_file` — the driver's own position (new).
- `driver.review_state_cmd` — `gh` template, `{branch}` substituted (new).
- `driver.agent_timeout_s`, `driver.command_timeout_s` — hard bounds on a role
  dispatch and on a project command (new). Every other subprocess is bounded by the
  constants in `procs.py`; nothing runs unbounded (L-090).
- `driver.rate_limit_max_wait_s` — the longest one dispatch sleeps out a harness rate
  limit before handing the refusal back (new).

## Timeouts

| what | bound | where |
|---|---|---|
| one role dispatch | `driver.agent_timeout_s` | config |
| `commands.stage_check`, `driver.review_state_cmd` | `driver.command_timeout_s` | config |
| waiting out a harness rate limit | `driver.rate_limit_max_wait_s` | config |
| a `wf` verb | `procs.CLI_TIMEOUT_S` (300s) | constant |
| git plumbing / commit+merge / push+`gh` | `procs.GIT_TIMEOUT_S` (120s), `GIT_WRITE_TIMEOUT_S` (900s), `NETWORK_TIMEOUT_S` (600s) | constants |

Every command runs in **its own process group**. A role is launched through a shell, so
the agent is a grandchild — killing only the direct child leaves the agent alive, still
holding its worktree and still writing into a repo the driver has already given up on
and moved past. Hitting the bound (or a `^C` on the way through) sends SIGTERM to the
group, then SIGKILL, then sweeps the group again for grandchildren that outlived their
parent, and reaps the pipes.

A dispatch killed at its bound pauses as `launch_timeout` naming the budget to raise —
distinct from `launch_failed`, so "it needed longer" is never reported as "it never ran".
- also consumed: `paths.*`, `project.base_branch`, `parallel.worktree_base`,
  `review.passes` / `review.max_attempts`, `closeout`, `limits.*`,
  `commands.stage_check`, `orchestrate.history_cap`.

## Modules

| file | holds |
|---|---|
| `wf-driver`, `driver_main.py` | entrypoint and flags |
| `loop.py` | the continuous loop and per-sprint entry by phase |
| `phases.py` | sprint_start, designing + the stage gate, the completion gate, closeout, ship |
| `stages.py` | one stage: load, frontier, build→review, merges, the close and the ship-or-cut decision |
| `issues.py` | design-issue promotion/recording, twin closing, and the blocked-branch salvage lookup |
| `drillcache.py` | derived digest staleness (taken-at sha × targets), swept at every stage close |
| `adequacy.py` | digest reading, consecutive-inadequate count, parking |
| `stoprules.py` | the five stop signals |
| `gitops.py` | branches, worktrees, merges, stack derivation, push/PR |
| `dispatch.py` | prompt construction and the launch template |
| `cliverbs.py` | the `wf` CLI wrapper (mutations serialized) |
| `progress.py` | the run's commentary: step lines, heartbeat, in-flight on `^C` |
| `config.py`, `state.py`, `events.py`, `procs.py`, `runtime.py` | config, state file, telemetry rows, bounded subprocesses, the injected bundle |

## Decisions worth knowing

- **A merge conflict goes to the rung that can resolve it.** The conflicted merge is
  left in the tree and `wf-stage-repair` is dispatched in `merge` mode against it; the
  verdict is read from disk (its exit code, no merge in progress, and the task branch an
  ancestor of the sprint branch — those two prove the merge landed; tree cleanliness says
  nothing about it, and the repair's own telemetry row leaves the tree dirty anyway). Only
  a repair that could not resolve it
  aborts the merge and records a design issue naming the conflicting paths, which parks
  the task and is what the next cut reads.
- **The park count is derived, not stored.** Three consecutive `inadequate`
  full-promise digests under `paths.drill_cache` park the capability
  (`status: parked`, written as a text-surgical edit that preserves comments and
  unicode, L-106). Only full-promise digests count; a proposed-set review judges a
  scenario set at authoring time, not the promise as shipped. If the drill cache is ever
  cleared, the count restarts — the park is simply delayed, never wrong.
- **An inadequate verdict is not a dead end.** Before the park count is consulted,
  `wf pipeline append-residuals` carries the digest's residual paths onto the
  capability's `notes:` — stamped with the digest's own timestamp, so re-running a
  review never doubles them. That is what the next plan revision reads.
- **Adequacy is not a `closeout` step.** It fires per capability at every stage close,
  on `wf pipeline capability-complete`'s mechanical set-difference of a scenario set
  against the shipped `[SYS-TC:]` tags — pure mechanism with no ordering to configure, so
  it carries no knob and naming it in `closeout` is an `unknown_closeout_step` halt. What
  the config still owns is the per-sprint half: the `wf-*` roles and the terminal `ship`.
  Finished steps are banked in the driver state, so a restart inside closeout does not
  re-dispatch one.
- **The PR body is an accumulator, not a read of one artifact.** Each stage close appends
  its `serves`, `checkpoint` and `decisions` to `paths.pr_body` (`wf pipeline
  append-pr-body`) as it merges, because the stage artifact is archived and deleted right
  after; `ship` folds the close's drain and the plan in around those blocks, in the same
  file. The PR title comes from what the close says the sprint served.
- **`--dry-run` stops after the design dispatch.** It prints the dispatches, git
  writes and CLI mutations it would make; with no stage on disk there is nothing
  further to plan. It is the smoke-test surface: config, path resolution, role
  resolution, prompt and command construction, stack derivation.

## Tests

```sh
bash tools/driver/tests/run_all.sh        # or: python3 tools/driver/tests/<name>_test.py
```

Stdlib `unittest`, no LLM anywhere: the phase machine runs against scripted CLI/git/
agent stand-ins (`tests/fakes.py`), git and the CLI are exercised for real against
scratch repos, and `e2e_test.py` runs one whole sprint through the real `wf` verbs,
real git and a shell-script agent that leaves the artifacts a real role would leave.
Tests are wf2-source-only — the installer strips them.
