# wf2 — Candidates

Deferred observations about the wf2 toolkit itself: improvements, risks, and
thresholds worth acting on later. Not scheduled work — a parking lot so a
forward-looking note isn't lost. Promote an entry to real work when its trigger
fires; delete it when resolved.

---

## C1 — A shared config reader (the `wf config get` threshold)

**Date:** 2026-06-14
**Context:** `skills/wf-init/scripts/scaffold.sh` now derives the transient dir,
telemetry sink, and gitignore line from `.wf/config.yaml` instead of hard-coding
them (the "config is the single source of truth" ground rule). It reads config
with a small awk helper, `cfg_path`.

**Observation:**
1. `cfg_path` is **not a real YAML parser.** It works only because the `paths:`
   block is flat, one level deep, with double-quoted values. If the config schema
   ever grows nested or multi-line path values, it breaks — silently. This
   constrains how the config schema may evolve while the awk reader is the only
   reader.
2. It is the **second** place config is read mechanically-ish (skills read it as
   LLM prose; scaffold now parses it). One hand-rolled reader is fine. The moment a
   **second script** needs to read config, two hand-rolled parsers will drift.

**Trigger to act:** a second script needs to read `.wf/config.yaml`. At that
point, fold config reads into one shared helper (e.g. `wf config get <key>`) — the
wf1 lesson that a read with ≥2 callers belongs in one tool. Until then, the single
awk reader is the right amount of machinery.
