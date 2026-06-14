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

Each skill records one telemetry line per session. The write soft-fails — if it
errors, continue the main flow. Skip the whole step when `telemetry.enabled:
false` in config.

At the **start** of the session, capture the start stamp:

```sh
TS_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Before **exit** — whether the session completed, halted, or escalated — append the
record. Resolve the recorder from `paths.tools` and the sink from
`paths.telemetry`:

```sh
python3 <paths.tools>/telemetry/record_session.py \
  --agent      <skill-name> \
  --started-at "$TS_START" \
  --ended-at   "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --outcome    <completed|halted|escalated> \
  --notes      "<one-liner>" \
  --sink       <paths.telemetry>
```
