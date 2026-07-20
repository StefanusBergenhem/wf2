# wf2

> This file is the agent-facing guide for **developing wf2 itself** (the repo is
> built with Claude Code).

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

- **Capabilities** — the *why*, kept as an **open work-set** of *un-shipped* demand:
  user-voice needs the PO has raised whose solution is not yet built. The SA drains a
  capability when the backlog design serving it drains — i.e. when `reconcile` finds its
  work shipped and its essence now lives in the code's `[REQ]` tags + any ADR — so the set
  never becomes an accumulating catalog of shipped features. The drain is keyed on *shipped
  evidence*, never on the SA's own "I designed it" — like every other drain here, it derives
  from a fact rather than a role's self-report. So a slice that gets rejected or re-cut
  leaves the *why* intact for the re-design to reason from, including fix mode's judgement
  of whether the driving capability is itself wrong.
- **Design backlog** — the SA's *committed but draining* record of designed-but-unbuilt
  work. The SA appends a design and removes it as `reconcile` shows it shipped; it empties
  to nothing, so it is working state, not a durable spec (its load-bearing decisions live
  in the ADRs).
- **ADRs** — deliberate architecture decisions: the choice made, the
  alternatives rejected, and why. The durable record of *how* the system is
  shaped, separate from the *why* (capabilities) and the local *how-to*
  (AGENTS.md).
- **`AGENTS.md` files, in a directory hierarchy** — borrowing the industry's
  best practice. Hold commands, gotchas, and conventions, co-located with the
  code they govern.
- **Requirement tags in test cases** — every test proving a requirement carries its
  greppable tag plus the full statement on the tag line. A script harvests ids,
  statements, and coverage on demand into a fresh summary; nothing is maintained.
- **Tooling** — config + scripts/CLI. Machinery, kept separate from intent.

Everything else (the repo map, component descriptions, plans, contracts) is
derived on demand or discarded after the step that produced it.

**The maintainer archive is outside all of this.** As each transient drains from the
working set (capabilities/learnings when the SA drains their shipped design; slice, sprint,
and backlog snapshot at sprint close), a copy is written to `paths.archive` — a
**write-only** sink for the wf2 maintainer to study run quality offline. It is *not* part of the durable working set and
**no wf role is ever instructed to read it**. The governor forbids storing what code can
re-derive because a role would consume it as truth and it would rot; the archive is exempt
because nothing consumes it — it is research exhaust, peer to the telemetry log, never a
source of truth. Do not wire any role to read it, and do not "governor-police" it as a
stored-what-code-reports violation.

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
- **The behavior test (the rule behind the two below).** Every line in a rendered
  skill or agent must change what the executing agent *does*. The agent reads only
  that file — so a line it cannot act on is pure context cost. Cut: architecture
  facts ("component X depends on Y"), rationale ("this keeps context lean"), and
  caller/consumer framing ("a planning role dispatched you", "the SwA consumes
  this"). The test: *would the agent behave differently if this line were absent?*
  No → it is authoring-context that belongs here or in the design docs, not in the
  file. The recurring leak is writing with the whole system in mind and letting that
  frame bleed into the file.
- **Direct address.** Skill bodies and references are read *by* the executing agent
  — write them in the imperative / second person ("Load X", "you decide"). Refer to
  a role in the third person only when it is a genuinely *different* agent (the
  downstream build phase, the Software Architect). `/skill-builder`'s "third person"
  rule governs the **description only** — do not apply it to the body.
- **Loading a shared skill.** A wf skill or agent that needs another skill's content
  (e.g. `wf-basics`) instructs a direct file-`Read` of its `SKILL.md` — *not* the
  subagent `skills:` preload field, *not* the `Skill` tool. Both of those are
  model-triggered or Claude-only; a file-`Read` is deterministic (the content is
  loaded, not discovered) and harness-portable, which is what our deterministic-dispatch
  rule requires. Do not "fix" a `SKILL.md` file-`Read` into a `Skill`-tool call.
- **Authoring.** Loading `/skill-builder` is a precondition for writing or editing
  any skill — not a suggestion. Skip it and the skill ships wrong frontmatter, soft
  redirections, or instructions repeated across phases; fix-on-review costs more
  than the load.
- **Gate-word mandatory preconditions.** State a required step as a gate at the
  point of action — imperative plus the consequence of skipping it — never as a
  declarative item in a preamble. A soft pointer gets read past even when it was
  read. Where a precondition can be enforced by a mechanism, prefer the mechanism
  over wording (mechanical-over-LLM applies to compliance too).

### Config & layout for wf when installed in target repo

- All configuration lives under **`.wf/`**: `.wf/config.yaml` (committed,
  intent) and `.wf/transient/` (gitignored — derived, machine-owned, disposable).
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
- **Config keys are whole file paths, not directories to join onto.** A specific
  file a skill reads or writes gets its own complete-path key
  (`paths.discover_brief: ".wf/transient/discover/brief.md"`) — never a directory
  the skill appends a filename to. Then moving the file (or having several skills
  share it) is one config edit, and every reference follows. A directory gets a key
  only when a tool needs the dir itself (e.g. an extractor's `--out` working dir).

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

- **Candidates are a parking lot, not a backlog.** Deferred observations live in
  `doc/CANDIDATES.md`: promote one to real work when its trigger fires, and **delete it
  when resolved** — once a candidate's change has shipped, remove the entry rather than
  leaving it as a resolved tombstone. The rationale survives in the commit and the code; a
  lingering "done" candidate is exactly the kind of stored-what-code-reports the governor
  forbids.
- **The agent keeps `doc/CANDIDATES.md` current.** When a change lands that makes an entry
  stale, update it in the same pass; when it resolves one, delete it. **Track recurrence:**
  when something you already parked surfaces again, note the repeat on its entry, and once it
  has recurred enough to be worth acting on, say so and recommend promoting it. You surface and
  recommend; the user prioritizes and decides when to act.

## Pilot Project - DEMS

As of now, wf2 is only located on my host, and the pilot project used to develop wf2 is
~/repos/dems/. Whenever there is mention of "dems" that is what is being refered to. 
But keep in mind that DEMS is not the only intended customer of dems, just the first adapter
for dogfooding. 

