---
name: wf-basics
description: The basics every wf skill assumes — where config and workspace live, and how to record session telemetry.
---

# wf basics

## 1 — The `.wf/` workspace

- Everything wf keeps in a project lives under `.wf/`.
- Every `paths.<x>` and `commands.<x>` a skill names resolves to a value you are
  **given**, never one you go and read:
  - **Dispatched into a worktree** — your prompt carries the resolved block already
    (`paths.current_task: …`, `commands.preflight: …`, one per line), plus `role_dir`,
    the directory your own `assets/` templates live in — which is not always the
    directory the file you were told to read sits in. Read the value off the prompt.
  - **Running in a live session with a human** — you have no envelope, so bootstrap one.
    `.wf/config.yaml` is the only path anything may hard-code; pull the single key that
    locates the CLI out of it, then let the CLI print the rest:

    ```sh
    TOOLS="$(grep -E '^\s+tools:' .wf/config.yaml | head -1 | sed 's/.*: *//; s/ *#.*//; s/"//g')"
    python3 "$TOOLS/cli/wf" envelope show
    ```
- **Never read `.wf/config.yaml` whole.** It is written for the human who edits it and is
  mostly comments, so reading it costs about twelve times what its values do. If a key
  a skill names is missing from your block, say so and stop — do not go looking for it
  in the config.
- The one file under `.wf/` a role may **write** that is not an artifact of its own work
  is `paths.repo_state`, and only to bump an id high-water mark it minted from.

## 2 — Session telemetry

One telemetry line per session. Invoking it is **mandatory** — but if the
recorder command itself errors, continue anyway (telemetry is observability, not
correctness).

Below, `<root>` is the `worktree` path from your dispatch envelope when you were
dispatched into one, and the repo root otherwise. Root every path at it: concurrent
sessions share the repo's ambient `.wf/transient`, so an unrooted stamp is clobbered
by whichever session writes next.

**START — run this NOW**, before any other work. Write the start stamp to a file
— never to an environment variable. `<agent>` is the skill/agent name you will
pass as `--agent` at END. Overwrite an existing file — a stale stamp from a
crashed session must not survive:

```sh
mkdir -p <root>/<paths.transient>
date -u +%Y-%m-%dT%H:%M:%SZ > <root>/<paths.transient>/ts-start-<agent>
```

**END — every role skill runs this as its REQUIRED final action**, whether it
completed, halted, or escalated. This is the canonical command; a role skill
triggers it at its end with its own `--agent` and `--outcome`. Resolve the
recorder from `paths.tools` and the sink from `paths.telemetry`. Read
`--started-at` from the start-stamp file — if it is missing, pass the end stamp
as start (degraded, never blocked) — then delete the file:

```sh
TS_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 <paths.tools>/telemetry/record_session.py \
  --agent            <agent> \
  --started-at       "$(cat <root>/<paths.transient>/ts-start-<agent> 2>/dev/null || echo "$TS_END")" \
  --ended-at         "$TS_END" \
  --outcome          <completed|halted|escalated> \
  --wf-friction      "<see below, or omit>" \
  --friction-kind    <contract_defect|skill_gap|tooling_bug|env_setup|none — omit when no friction> \
  --repo-observation "<see below, or omit>" \
  --gotcha           "<see below, or omit>" \
  --sink             <paths.telemetry>
rm -f <root>/<paths.transient>/ts-start-<agent>
```

### Session feedback — the questions

These seed the continuous-improvement loop; a later retrospective distils them
into durable lessons. Answer from what you actually did this session.

**"None" is the expected answer for a clean session — never invent friction or an
observation to look useful.** Report only a *concrete, specific* item that points
at a real artifact, field, or step; a vague "could be clearer" is noise — omit it
(leave the flag off or pass `""`).

- **`--wf-friction`** — Did any wf instruction, input, or output you were given
  contradict itself, mislead you, or leave you guessing? Name the exact skill,
  field, or step. *(Feeds wf-toolkit improvement.)*
- **`--friction-kind`** — Whenever you pass `--wf-friction`, also classify it with
  exactly one value: `contract_defect` (a handover/contract field was wrong,
  missing, or contradictory), `skill_gap` (a skill instruction misled you or was
  absent), `tooling_bug` (a wf script or command misbehaved), `env_setup`
  (environment or setup blocked the work). No friction → omit the flag (it
  defaults to `none`).
- **`--repo-observation`** — In the code you actually touched, did you hit a
  blocker, a surprise, or a smell a future task should address? Tie it to what you
  worked on. *(Feeds the project's learnings log.)*
- **`--gotcha`** — Did you hit a non-obvious trap in *working with* this repo —
  an env, setup, or convention snag a future agent will hit again (e.g. a port
  collision unless a variable is pinned)? State it as one self-contained sentence
  with the exact fix. Code smells belong in `--repo-observation`, not here.
  *(Feeds a proposed AGENTS.md edit.)*

## 3 — The units of work

Use these words for these things — meaning one unit and saying another sends the next role
at the wrong altitude.

- **Sprint** — packaging only: one branch, one PR, one closeout. It carries no design
  meaning. It ends when a stage lands a SYS-TC scenario, or at
  `driver.max_stages_per_sprint`, whichever comes first.
- **Stage** — the design unit: one cut, made against the merged tree, holding the tasks
  with no dependency between them. They build in parallel worktrees and merge together.
  Exactly one stage is designed ahead; nothing beyond it is forecast.
- **Task** — one contract, one build agent, one review chain.

Four durable artifacts carry direction: `paths.charter` (where the system is going),
`paths.architecture` (the structure the repo has not reached yet — what exists is derived
from discover, never listed there), `paths.plan` (the next few milestones, re-validated at
every cut), and `paths.capabilities` (each open promise, carrying the SYS-TC scenario set
that would prove it once the capability is taken up). None is yours to write unless your
own instructions say so.

No role declares a capability complete; the close-time adequacy gate detects that.

## 4 — You are running headless

Unless a human is typing to you in a live session, you were launched **headlessly**: one
turn, no notifications, no follow-up. When your turn ends your session is over, every
background task you started is killed with it, and the only thing anyone reads is the
artifacts your role skill told you to write — never your prose.

- **Run every command whose result you need in the foreground** and wait for its exit code
  — gates, test suites, builds, installs. Never a shell `&`, never a background tool mode,
  never a monitor or notification you expect to wake you. A long gate is not a reason to
  background it; let it run.
- **Never end a turn intending to continue when something finishes.** There is no next
  turn. The driver inspects the artifacts, finds none, and spends the entire cycle again —
  with the work you did left uncommitted in your worktree.
- Finish inside this turn: run the gate, write the artifact, record telemetry, exit.
