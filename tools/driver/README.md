# wf-driver — the loop program

The Python program that executes wf's continuous delivery loop. One invocation runs
sprint after sprint — design, build, review, merge, close, ship — until a stop rule
fires. It replaces the `wf-orchestrate` skill: orchestration is mechanical, so a
script does it.

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
[wf-driver 0:02:11] ── designing — the design role cuts the slice
[wf-driver 0:02:11]   ▶ wf-designer (originate)
[wf-driver 0:03:11]     · wf-designer (originate) still running — 1m00s / 2h00m budget
[wf-driver 0:14:52]   ✔ wf-designer (originate) — 12m41s
[wf-driver 0:14:53]   ✔ slice check green · serves CAP-004
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
task_id: T3
worktree: /repo/.wf/transient/worktrees/s7-T3
contract: .wf/transient/current-task.yaml
attempt: 0
```

**The contract is exit code + artifacts on disk.** Agent stdout is streamed to
`<paths.transient>/driver-logs/` and never read. Every verdict comes from a `wf`
verb's JSON (`pipeline next`, `slice check`, `sprint check`,
`orchestrate inspect-*`) or from an artifact (`paths.design_issues`, an adequacy
digest, `paths.decision_prep`). Nothing routes on prose.

Each dispatch, routing decision and stop appends one row to `paths.telemetry`:
`{kind: driver_event, agent, role, event, mode, sprint, increment, task, rc,
duration_s, started_at, ended_at, ts}`. `agent` mirrors `role` because that is the
field the telemetry reader classifies on, and every row carries an ISO-8601 UTC span
(zero-length for an instantaneous event) so the usage-row join is exact, not fuzzy.

## Phases

State lives in `driver.state_file`; each phase is entered (and written) before it
acts, so a restart re-enters it.

| phase | what runs | leaves |
|---|---|---|
| `sprint_start` | clean-tree gate; branch `sprint/s<N>` off the stack tip; carry the telemetry rows onto it; `sweep-transients` + `reclaim-stale`; `wf-discover` | `designing` |
| `designing` | `wf-designer` (originate) → `wf slice check`; a red gate records a design issue and routes `wf-designer` (repair) | `increment_loop`, or a pause |
| `increment_loop` | per increment: `wf-tl` → `sprint materialize` + `sprint check` → `compute-stages` → sub-layers → boundary | `closeout` |
| `closeout` | the `closeout` config steps, each banked on disk as it finishes: `wf-*` roles, `adequacy` (per served capability → drain / residuals / park), `ship` (`complete-sprint`, commit, push, `gh pr create`) | `sprint_start` |
| `awaiting_ruling` | on restart: `wf-designer` (resume) once `paths.decision_prep` carries a ruling, then `resolve-design-issue` for the brief's `di_id` (the resume run closes only the host entry; the run-state twin is what parks the task); the brief's `mode:` header says where to pick up — `originate` carries on designing, `repair` returns to the increment loop | `designing` / `increment_loop` |

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
runs it: `phases.SLICE_GATE_ATTEMPTS`, `increments.CONTRACT_PREP_ATTEMPTS`,
`increments.STAGE_REPAIR_ATTEMPTS`. `review.max_attempts` bounds one task's
build→review chain and nothing else.

Inside an increment, one sub-layer at a time: `pipeline next` gives the frontier,
each task gets a worktree + its envelope (`sprint task --write`) + `wf-build`, then
the `review.passes` chain; the approved set batch-merges into the sprint branch; the
boundary runs `commands.stage_check` (repairing through `wf-stage-repair`, whose
envelope carries the increment's observable checkpoint) and clears design issues.

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
| escalation | `paths.decision_prep` exists — written by the design role in any mode | pause in `awaiting_ruling`, with the phase it interrupted recorded; a ruling resumes into that phase, and nothing consumes the brief before then |
| work exhaustion | no capability or learning entry left that is not `status: parked` | clean exit |
| manual stop | `driver.stop_file` exists | seen at an increment boundary: finish the sprint, ship, exit |
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
  only inside `sprint_start` — but an interruption inside the increment loop is exactly
  where a task is left holding a slot no one will release, and that re-entry never
  passes through `sprint_start`.

**A non-zero exit is not, on its own, a failure** and is deliberately never checked as
one: a role can exit badly having already written a perfectly good artifact. It is
consulted only where the caller has established that the role produced *nothing* — there
it is the difference between "the Tech Lead decomposed nothing" and "the Tech Lead never
ran". The one exception is `_repair_merge`, where a non-zero exit already *is* the
"did not resolve" signal.

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
| `phases.py` | sprint_start, designing, closeout, adequacy pass, ship |
| `increments.py` | increment loop, sub-layer loop, task pipeline, merges, boundary |
| `issues.py` | design-issue promotion/recording and the repair dispatch |
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
  aborts the merge, records a design issue naming the conflicting paths (which parks the
  task) and hands it to the repair ladder.
- **The park count is derived, not stored.** Three consecutive `inadequate`
  full-promise digests under `paths.drill_cache` park the capability
  (`status: parked`, written as a text-surgical edit that preserves comments and
  unicode, L-106). Only full-promise digests count; an iteration-claim review judges
  one slice's claim. If the drill cache is ever cleared, the count restarts — the
  park is simply delayed, never wrong.
- **An inadequate verdict is not a dead end.** Before the park count is consulted,
  `wf pipeline append-residuals` carries the digest's residual paths onto the
  capability's `notes:` — stamped with the digest's own timestamp, so re-running a
  review never doubles them. That is what the next plan revision reads.
- **`adequacy` is a named `closeout` step, not an implicit one.** It must appear before
  `ship`, which archives the slice and drains the working set it reviews; a list that
  ships without it, or after it, is a `closeout_order` halt, and a step the driver
  cannot run is `unknown_closeout_step`. Finished steps are banked in the driver state,
  so a restart inside closeout does not re-dispatch a review (a second one would shift
  the park counter).
- **The PR body's decision report comes from the slice's `## Decision log`,** read
  before `complete-sprint` archives the slice. There is no separate decisions file.
- **`--dry-run` stops after the design dispatch.** It prints the dispatches, git
  writes and CLI mutations it would make; with no slice on disk there is nothing
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
