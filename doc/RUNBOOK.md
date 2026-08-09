# Operating the loop — from product input to shipped PRs

The human's manual for a wf-equipped repo. Everything below runs from the target
repo's root (for the pilot: `~/repos/dems`). Interactive roles run as slash
commands inside a Claude Code session; the loop runs as a plain terminal command.

The Python the driver and CLI need: `python3` with PyYAML on the path — use a venv
(`python3 -m venv .venv && .venv/bin/pip install pyyaml`, then invoke
`.venv/bin/python` below wherever `python3` appears).

## 0. One-time setup (already done for dems)

```sh
~/repos/wf2/install.sh --target claude .   # render skills/agents + .wf/tools
# then, in Claude Code:  /wf-init          # scaffold .wf/ + capture commands
```

## 1. Capture the *why* — PO session (interactive)

```
/wf-po
```

Turns your unstructured product input (+ the discover brief) into user-voice
capabilities in `.wf/CAPABILITIES.yaml`. Also where **parked** capabilities
(3 consecutive inadequate verdicts) get re-scoped and un-parked.

## 2. Set direction & structure — SA session (interactive)

```
/wf-sa
```

Phases: ground → prepare → present & align → record/commit. Outputs, all
human-ratified before write:

- `.wf/charter.md` — direction: target shape, ranked forces, domain language,
  sequencing, no-go zones.
- `.wf/architecture.md` — the structural delta: components/subsystems being
  added or changed, 1–2 sentence intent each, depends-on. The autonomous
  designer **cannot** invent structure beyond this map (stage check A12).
- `.wf/adrs/` — decisions passing the ADR threshold.

This same session is where you **rule on escalations** (step 5 below).

## 3. Run the loop

```sh
python3 .wf/tools/driver/wf-driver --dry-run   # preview the planned dispatches
python3 .wf/tools/driver/wf-driver --once      # bring-up: exactly one sprint
python3 .wf/tools/driver/wf-driver             # continuous, sprint after sprint
```

One **stage** = wf-designer cuts it against the merged tree (plan revision, the
design, and that stage's task contracts, in one dispatch; soundness + `wf stage
check`) → its tasks build and review in parallel worktrees, since a stage is the
set with no dependency between them → they merge together → heavy checks and
repair at the close → any capability whose scenario set is now fully shipped gets
its close-time adequacy review (adequate = the capability drains).

One **sprint** = the stages from branching until one lands a system test, or
`driver.max_stages_per_sprint`, whichever comes first → closeout: retrospective →
the sprint ships as a **stacked PR**. Then the next sprint, until a stop rule
fires.

Exit `0` = clean stop, exit `1` = halt needing you. The position is on disk —
re-running the same command resumes wherever it stopped. Stop reasons:

| stop | meaning | your move |
|---|---|---|
| `escalation` | designer wrote `.wf/transient/decision-prep.md` | step 5 |
| `work_exhaustion` | no open, unparked capability/learning left | step 1 (refill) |
| `no_stage_cut` | the designer ran its rounds and cut no stage — but the counts in the message say work is still open. **Not** a drained backlog | read the designer log the stop names. Usually the capability it is on is too wide to cut against (step 5 / a PO session); resuming just gives it the same rounds again |
| `no_design_dispatch` | a halt: the loop reached that same no-stage ending with no designer dispatch behind it | a bug, not an operating condition — the run state and the log are the evidence |
| `stack_depth` | ≥ `driver.max_unmerged_sprints` PRs unmerged | step 4 (merge) |
| `stage_check_red` | the heavy checks stayed red after every repair attempt, so the sprint branch is broken | read the stage-check log the halt names, fix the cause, and resume |
| `launch_failed` | the harness never ran a role — expired login, no quota left | read the quoted line; it is the harness's own reason. A **rate limit** is waited out and retried automatically, so seeing this for one means the limit outlasts `driver.rate_limit_max_wait_s` (a weekly cap): resume after it lifts |
| `launch_timeout` | a role hit `driver.agent_timeout_s` with nothing written | raise the budget if the role legitimately needs longer, then resume |
| stop file | you asked it to stop | nothing |
| `dirty_tree` / other halts | something needs a look | read the message |

To stop it deliberately: `touch .wf/transient/STOP` — it finishes the sprint in
flight, ships it, and exits. Ctrl-C is also safe (resume from disk).

## 4. Review the PRs (the brake, not a gate)

Each sprint ships its own PR, stacked on the previous unmerged one; merging
bottom-up retargets automatically. `.wf/plan.md` rides in every PR — that is
your direction-drift check. The PR body carries the sprint's decision log:
everything the designer decided below the escalation gate.

- **Merge** feeds the loop (frees stack depth).
- **Comments** → bring them into the next PO/SA session (capability edits,
  learnings) — the next sprint consumes them.
- **Request changes** → the driver stops after the sprint in flight; fix
  forward through a session + next sprint. The driver never rewrites the stack.

## 5. Rule on an escalation

The loop paused with `.wf/transient/decision-prep.md` holding the designer's
brief (options + recommendation). In Claude Code:

```
/wf-sa
```

It presents each prepared decision, records your ruling in the same file, and
writes any ADR the ruling earns. Then:

```sh
python3 .wf/tools/driver/wf-driver
```

The designer resumes from the ruling and the sprint continues.

## 6. Watching it run

```sh
python3 .wf/tools/cli/../cli/wf telemetry roles      # per-role context/cost
cat .wf/transient/driver-state.yaml                  # where the loop is
cat .wf/plan.md                                      # what it thinks is next
```

(Invoke the CLI as `python3 .wf/tools/cli/wf <noun> <verb>`.)

The rhythm in steady state: capabilities in (PO) → direction/structure ratified
(SA) → loop runs unattended → you merge PRs and answer escalations → capabilities
drain as adequacy proves them → work exhaustion → refill. Re-opening a drained
capability later is normal, not a failure.
