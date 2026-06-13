---
name: wf-basics
description: wf2 runtime basics shared by every wf skill — where config and state live (.wf/), and the durable-vs-derived rule. Pure reference, cited by other skills instead of restated.
---

# wf-basics — wf2 runtime basics

Pure reference. Owns no procedure and produces no artifacts. It is the single
source of truth for what every wf2 skill assumes about a project's workspace, so
the rules are stated once here instead of copied into each role skill.

## 1 — The `.wf/` workspace

Everything wf2 keeps in a project lives under `.wf/`:

- **`.wf/config.yaml`** — committed. Project configuration. **Always** resolve a
  path or setting through it; never hard-code a location a config key already
  defines. The schema is owned by the `wf-init` skill and grows one field at a
  time — no field exists that some skill does not read.
- **`.wf/transient/`** — gitignored. Derived, machine-owned, disposable output:
  read-views, models, plans, role-to-role handovers. Regenerated on demand;
  **never hand-edited and never committed.**
- **`.wf/tools/`** — committed. The toolkit machinery `install.sh` copied in
  (extractors, scripts). Versioned with the project so it rides into worktrees.

## 2 — Durable vs derived (the governor)

The one rule under everything: **never durably store what a script can re-derive
from code.** Structure (modules, signatures, dependency edges) is extracted fresh
each time, so it cannot rot. The durable set is small and lives outside
`.wf/transient/` — capabilities, ADRs, `AGENTS.md` files, requirement tags in
tests. If a skill is about to write a system summary that the toolchain could
regenerate, that is the signal it belongs in `transient/`, not in a kept file.

## 3 — Skills are dispatched by name

wf2 skills run inside a deterministic workflow, invoked by name. A skill's
`description` is therefore not a routing input — it exists only to leak a little wf
knowledge into any agent reading the repo, and is kept to 1–2 sentences. Do not
expect an LLM to "choose" a skill from its description; the workflow names it.

Skills are installed under `{{WF_SKILLS_DIR}}` for this project's harness.
