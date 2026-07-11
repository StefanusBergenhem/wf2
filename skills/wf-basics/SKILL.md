---
name: wf-basics
description: The basics every wf skill assumes — where config and workspace live, and how to record session telemetry.
---

# wf basics

## 1 — The `.wf/` workspace

Everything wf keeps in a project lives under `.wf/`. **Always resolve a path or
setting through `.wf/config.yaml`** — never hard-code a location a config key
already defines.

| config key | holds | tracked |
|---|---|---|
| (`.wf/config.yaml` itself) | project configuration — the source of truth for every path and setting | committed |
| `paths.tools` | installed toolkit machinery (extractors, scripts) | committed |
| `paths.transient` | derived, disposable output; regenerated on demand, never hand-edited | gitignored |
| `paths.telemetry` | append-only session log | committed |

Resolve each from config; the defaults live only in the config template.

## 2 — Session telemetry

One telemetry line per session. Invoking it is **mandatory** — but if the
recorder command itself errors, continue anyway (telemetry is observability, not
correctness). Skip the step entirely only when `telemetry.enabled: false` in
config.

**START — run this NOW**, before any other work. Write the start stamp to a file
— never to an environment variable. `<agent>` is the skill/agent name you will
pass as `--agent` at END. Overwrite an existing file — a stale stamp from a
crashed session must not survive:

```sh
mkdir -p <paths.transient>
date -u +%Y-%m-%dT%H:%M:%SZ > <paths.transient>/ts-start-<agent>
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
  --started-at       "$(cat <paths.transient>/ts-start-<agent> 2>/dev/null || echo "$TS_END")" \
  --ended-at         "$TS_END" \
  --outcome          <completed|halted|escalated> \
  --wf-friction      "<see below, or omit>" \
  --friction-kind    <contract_defect|skill_gap|tooling_bug|env_setup|none — omit when no friction> \
  --repo-observation "<see below, or omit>" \
  --gotcha           "<see below, or omit>" \
  --sink             <paths.telemetry>
rm -f <paths.transient>/ts-start-<agent>
```

The sink file is created by `wf-init` at install, so this only ever appends. The
recorder anchors a relative sink to the main checkout root, so run the command
unchanged inside a task worktree — never rewrite the sink path.

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
  worked on. *(Feeds the project backlog.)*
- **`--gotcha`** — Did you hit a non-obvious trap in *working with* this repo —
  an env, setup, or convention snag a future agent will hit again (e.g. a port
  collision unless a variable is pinned)? State it as one self-contained sentence
  with the exact fix. Code smells belong in `--repo-observation`, not here.
  *(Feeds a proposed AGENTS.md edit.)*
