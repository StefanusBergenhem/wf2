---
name: wf-basics
description: The basics every wf skill assumes — where config and workspace live, and how to record session telemetry.
---

# wf basics

Shared reference. Other skills cite this instead of restating it.

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

**START — run this NOW**, before any other work (skip only if `TS_START` is
already set this session):

```sh
TS_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

**END — every role skill runs this as its REQUIRED final action**, whether it
completed, halted, or escalated. This is the canonical command; a role skill
triggers it at its end with its own `--agent` and `--outcome`. Resolve the
recorder from `paths.tools` and the sink from `paths.telemetry`:

```sh
python3 <paths.tools>/telemetry/record_session.py \
  --agent            <skill-name> \
  --started-at       "$TS_START" \
  --ended-at         "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --outcome          <completed|halted|escalated> \
  --wf-friction      "<see below, or omit>" \
  --repo-observation "<see below, or omit>" \
  --sink             <paths.telemetry>
```

The sink file is created by `wf-init` at install, so this only ever appends.

### Session feedback — the two questions

These seed the continuous-improvement loop; a later retrospective distils them
into durable lessons. Answer from what you actually did this session.

**"None" is the expected answer for a clean session — never invent friction or an
observation to look useful.** Report only a *concrete, specific* item that points
at a real artifact, field, or step; a vague "could be clearer" is noise — omit it
(leave the flag off or pass `""`).

- **`--wf-friction`** — Did any wf instruction, input, or output you were given
  contradict itself, mislead you, or leave you guessing? Name the exact skill,
  field, or step. *(Feeds wf-toolkit improvement.)*
- **`--repo-observation`** — In the code you actually touched, did you hit a
  blocker, a surprise, or a smell a future task should address? Tie it to what you
  worked on. *(Feeds the project backlog.)*
