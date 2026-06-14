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

| config key | default | holds | tracked |
|---|---|---|---|
| — | `.wf/config.yaml` | project configuration (source of truth) | committed |
| `paths.tools` | `.wf/tools/` | installed toolkit machinery (extractors, scripts) | committed |
| `paths.transient` | `.wf/transient/` | derived, disposable output; regenerated on demand, never hand-edited | gitignored |
| `paths.telemetry` | `.wf/telemetry/sessions.jsonl` | append-only session log | committed |

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
  --agent      <skill-name> \
  --started-at "$TS_START" \
  --ended-at   "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --outcome    <completed|halted|escalated> \
  --notes      "<one-liner>" \
  --sink       <paths.telemetry>
```

The sink file is created by `wf-init` at install, so this only ever appends.
