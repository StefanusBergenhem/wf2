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

---

## C2 — `brief.md` vs the text map overlap (discover)

**Date:** 2026-06-14
**Context:** discover can emit two transient text views of the system — the
agent-facing `brief.md` and a plain text system map (from `spine.py`). They
overlap on the component list + LOC + a description field, but each carries
something the other lacks: the text map uniquely has **per-component dependency
adjacency**; `brief.md` uniquely has **subsystems + scout descriptions +
cross-cutting couplings**.

**Observation:** having two near-duplicate agent text views invites drift and
forces a downstream planner to choose. Likely the right move is to fold a terse
dependency-adjacency view into `brief.md` so it is the *single* agent file, and
demote the text map to a debug / no-scout fallback.

**Trigger to act:** the first real dogfood where a planning agent (PO→SA→SWA)
consumes `brief.md`. If it reaches for adjacency the brief doesn't carry, fold it
in; if it never misses it, leave the split. Do not pre-decide before that run —
this is exactly what dogfooding the brief→planner contract is meant to settle.
