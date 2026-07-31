# wf-driver — the loop program

The Python program that executes wf's continuous delivery loop. One invocation runs
sprint after sprint — design, build, review, merge, close, ship — until a stop rule
fires. It replaces the `wf-orchestrate` skill: orchestration is mechanical, so a
script does it.

```sh
python3 .wf/tools/driver/wf-driver              # run continuously
python3 .wf/tools/driver/wf-driver --once       # exactly one sprint (bring-up)
python3 .wf/tools/driver/wf-driver --dry-run    # print the planned dispatches only
python3 .wf/tools/driver/wf-driver --config path/to/.wf/config.yaml --max-sprints 3
```

Exit code `0` = a clean stop (a stop rule, or the sprint limit). `1` = a halt: the
loop hit something it must not decide on its own. Both leave the position on disk;
re-running resumes it.

## The process contract

A role is launched by the config template `driver.agent_cmd`, with `{prompt}`
substituted (escaped for whatever quoting the template puts around it). The prompt
names the role's installed file and its envelope fields:

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

## Phases

State lives in `driver.state_file`; each phase is entered (and written) before it
acts, so a restart re-enters it.

| phase | what runs | leaves |
|---|---|---|
| `sprint_start` | clean-tree gate; branch `sprint/s<N>` off the stack tip; `sweep-transients` + `reclaim-stale`; `wf-discover` | `designing` |
| `designing` | `wf-designer` (originate) → `wf slice check`; a red gate records a design issue and routes `wf-designer` (repair) | `increment_loop`, or a pause |
| `increment_loop` | per increment: `wf-tl` → `sprint materialize` + `sprint check` → `compute-stages` → sub-layers → boundary | `closeout` |
| `closeout` | the `closeout` config steps; before `ship`: close-time `wf-adequacy` per served capability → drain/park; then `complete-sprint`, commit, push, `gh pr create` | `sprint_start` |
| `awaiting_ruling` | on restart: `wf-designer` (resume) once `paths.decision_prep` carries a ruling | `increment_loop` |

Inside an increment, one sub-layer at a time: `pipeline next` gives the frontier,
each task gets a worktree + its envelope (`sprint task --write`) + `wf-build`, then
the `review.passes` chain; the approved set batch-merges into the sprint branch; the
boundary runs `commands.stage_check` (repairing through `wf-stage-repair`, whose
envelope carries the increment's observable checkpoint) and clears design issues.

Sprint ids and stack depth are **derived from git**, never stored: the next id is one
past the highest `sprint/s<N>` branch, and the stack is the sprint branches not yet
merged into `project.base_branch`. A sprint PR targets the branch below it in the
stack, else the base branch.

## Stop rules

| rule | trigger | effect |
|---|---|---|
| escalation | `paths.decision_prep` exists | pause in `awaiting_ruling`; a recorded ruling resumes |
| work exhaustion | no capability or learning entry left that is not `status: parked` | clean exit |
| manual stop | `driver.stop_file` exists | seen at an increment boundary: finish the sprint, ship, exit |
| stack depth | unmerged sprint branches ≥ `driver.max_unmerged_sprints` | pause until PRs merge |
| request-changes | `driver.review_state_cmd` output contains `CHANGES_REQUESTED` | same as manual stop |

## Config keys

Read from `.wf/config.yaml` through the CLI's `common` module — the driver has no
defaults of its own; a missing key is a named, fatal error.

- `driver.agent_cmd`, `driver.max_parallel`, `driver.max_unmerged_sprints`,
  `driver.stop_file` — as before.
- `driver.state_file` — the driver's own position (new).
- `driver.review_state_cmd` — `gh` template, `{branch}` substituted (new).
- `driver.agent_timeout_s`, `driver.command_timeout_s` — hard bounds on a role
  dispatch and on a project command (new). Every other subprocess is bounded by the
  constants in `procs.py`; nothing runs unbounded (L-090).
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
| `config.py`, `state.py`, `events.py`, `procs.py`, `runtime.py` | config, state file, telemetry rows, bounded subprocesses, the injected bundle |

## Decisions worth knowing

- **Merge conflicts are never auto-resolved.** A conflicted batch merge is aborted so
  the rest of the batch can proceed, a design issue naming the conflicting paths is
  recorded (which parks the task), and the repair ladder decides — mechanical
  detection replacing the old write-set overlap check. `wf-stage-repair`'s `merge`
  mode is therefore unused by the driver.
- **The park count is derived, not stored.** Three consecutive `inadequate`
  full-promise digests under `paths.drill_cache` park the capability
  (`status: parked`, written as a text-surgical edit that preserves comments and
  unicode, L-106). Only full-promise digests count; an iteration-claim review judges
  one slice's claim. If the drill cache is ever cleared, the count restarts — the
  park is simply delayed, never wrong.
- **Close-time adequacy runs inside closeout, immediately before `ship`.** It is not
  a `closeout` config entry: the drain must happen before `complete-sprint` archives
  the slice, and both are part of shipping.
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
