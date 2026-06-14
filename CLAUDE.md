# wf2

> This file is the agent-facing guide for **developing wf2 itself** (the repo is
> built with Claude Code). It is *not* the `AGENTS.md` that wf2 produces for a
> target repo. A public README can be split back out at graduation.

An **agentic development workflow** that works when you open *any* repo —
legacy-first, not greenfield-first.

wf2 orchestrates a chain of focused roles through the lifecycle of a change.
Each role does one thing, then hands off to the next. The handovers are mostly
**transient documents on disk**; only a small, deliberate set of things is kept
over time.

## Core promises

1. **Legacy-repo first.** The base assumption is that you point wf2 at an
   existing, unfamiliar codebase and it works. Greenfield is the easy case.
2. **Mechanical over LLM.** Anything a script can verify, a script verifies —
   spend LLM resources only on what genuinely needs judgement. Verdicts come
   from mechanical checks against artifacts on disk, not from re-reading prose.
3. **Context engineering.** The workflow is a pipeline of focused roles. Each
   gets a clean, scoped context, does its one job, writes a handover, and exits.
   No role carries the whole system in its head.
4. **Derive, don't store.** Structure (modules, signatures, dependency edges) is
   re-derived from the toolchain on demand — it is never stored, so it cannot
   rot. We durably save only what code cannot report on its own.
5. **Mechanical structure generation feeds planning.** A toolchain-driven
   extractor produces a compressed map of the repo as input for the planning
   roles — free, deterministic, always fresh.

## What gets saved over time

Most documents are transient handovers between roles. The durable set is small:

- **Capabilities** — the *why*: the user-voice needs that every feature
  implemented since wf2 was introduced traces back to.
- **ADRs** — deliberate architecture decisions: the choice made, the
  alternatives rejected, and why. The durable record of *how* the system is
  shaped, separate from the *why* (capabilities) and the local *how-to*
  (AGENTS.md).
- **`AGENTS.md` files, in a directory hierarchy** — borrowing the industry's
  best practice. Hold commands, gotchas, and conventions, co-located with the
  code they govern.
- **Requirement tags in test cases** — each requirement's text lives as a
  greppable tag inside the test that proves it. A script harvests them on demand
  to produce a fresh requirements-and-coverage summary; nothing is maintained.
- **Tooling** — config + scripts/CLI. Machinery, kept separate from intent.

Everything else (the repo map, component descriptions, plans, contracts) is
derived on demand or discarded after the step that produced it.

## Ground rules (for building wf2)

These govern how wf2 itself is built. They exist for one reason: keep wf2 from
re-becoming wf1. Two mechanisms enforce that — the **governor** (never durably
store what code can re-derive) and the **dogfood law** (no mechanism ships until a
real run proves its absence hurt).

### Skills & agents

- **Deterministic dispatch.** Every wf skill/agent runs inside a deterministic
  workflow, invoked by name — not chosen by an LLM reading its description. The
  LLM-facing `description:` is therefore *not* a routing input.
- **Description = passive knowledge injection, kept tiny.** We still write a
  description, because it is how wf knowledge leaks into any LLM working in a
  wf-equipped repo. Keep it to **1–2 sentences, max** — context is the budget.
- **Naming.** Every skill and agent is prefixed `wf-`.
- **Operational only.** A skill body is operational instructions an agent follows
  to do the task — *not* build-philosophy, rationale, or notes on how wf itself is
  built. That reasoning lives here in CLAUDE.md; skills stay focused and to the
  point, with config-derived paths and no editorializing.
- **Authoring.** Load the `/skill-builder` skill before writing any skill.

### Config & layout

- All configuration lives under **`.wf/`**: `.wf/config.yaml` (committed,
  intent) and `.wf/transient/` (gitignored — derived, machine-owned, disposable).
  This is the wf1 `.workflow/` concept, restarted clean and near-empty.
- **Every config field must be read by something that exists.** Start the config
  near-empty and add a field only when a skill or script consumes it. No
  speculative knobs.
- **One source of truth for paths & commands.** Skills and scripts never restate a
  default path or command — they resolve it from `.wf/config.yaml`. Defaults live
  in exactly one place: the config template (`wf-init`'s
  `assets/config.yaml.tmpl`), rendered into the project's config at init. A skill
  writes `paths.discover`, never "`paths.discover` (default `.wf/transient/discover`)".
  The only path anything may hard-code is `.wf/config.yaml` itself — the bootstrap
  anchor you need in order to read everything else.

### Install & rendering

- wf2 is **harness-agnostic**: skills/agents are authored once with neutral tokens
  and **rendered per target** (Claude · OpenCode · Pi) at install time, so the
  installed file an agent reads is single-target with no runtime branching.
  `wf-init` + the install script carry this, and the rendering itself is **TDD'd**.

### Scripts

- **All scripts are built TDD** (red → green → refactor). Tests live in the wf2
  source only — **only the script is rendered into an install target, never its
  tests.**

### Dogfooding (standing guardrail)

- The wf2 MVP (Discover → PO → SA → SWA → Orchestration) is built **before** the
  first real dogfood, because you cannot dogfood a planning run without the
  planning roles existing. The discipline that keeps this honest: **do not
  gold-plate any single skill** — no extra visualization, config knobs, or review
  passes — before that first dogfood. Build the thin version, dogfood it, then
  earn each addition with evidence its absence hurt.
