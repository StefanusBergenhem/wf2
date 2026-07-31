---
name: wf-basics
description: The basics every wf skill assumes — where config and workspace live, and how to record session telemetry.
---

# wf basics

## 1 — The `.wf/` workspace

- Everything wf keeps in a project lives under `.wf/`.
- `.wf/config.yaml` is the central project configuration — the source of truth for
  every path and setting. All config-key references in a skill or instruction (such
  as `paths.tools`) resolve from `.wf/config.yaml`.

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

## 3 — The unit hierarchy

Work is nested four deep. Use these words for these things — meaning one level and saying
another sends the next role at the wrong altitude.

- **Sprint** — one loop iteration: one branch, one PR, one slice.
- **Increment** — a design milestone inside the sprint: a component allocation, an
  end-to-end flow, and an observable checkpoint. Increments run in the order the slice
  gives them; each one's tasks merge before the next is planned.
- **Sub-layer** — the tasks of one increment with no dependency between them; they build in
  parallel worktrees and merge together.
- **Task** — one contract, one build agent, one review chain.

Two artifacts carry direction across sprints: `paths.charter` (where the system is going)
and `paths.plan` (the next few milestones, re-validated every sprint). Neither is yours to
write unless your own instructions say so.

The slice states its **claimed scope** — what this sprint delivers of each capability's
promise and what it knowingly leaves. No role declares a capability complete; the
close-time adequacy gate detects that.
