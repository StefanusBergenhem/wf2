# wf-sa — fix mode

Your envelope carries:

- `di_id`           — the design issue to resolve
- `di_artifact`     — the design-issues file holding the issue (the same file as `paths.design_issues`)
- `task_id`         — the task the issue parked, or `null` when the issue is slice-scoped or a stage-boundary DI
- `sprint_artifact` — the sprint file holding the task contracts, when a sprint exists

Read the `di_id` entry in `$di_artifact` — its `summary` says what is unbuildable. Resolve
only that issue, on the path its `fix_kind` names:

- **`spec_amendment`** — one task's requirement or ADR is wrong. Steps 1–4 below.
- **`slice_defect`** — the slice cannot be decomposed as written. **The slice-defect path**.

On a **`spec_amendment`**, commit nothing — the sprint branch already exists and the durable
spec files you touch ride its working tree; list every amended file in your report instead.
On a **`slice_defect`** you run before that branch exists, and its creation gates on a clean
working tree — leave your amendments uncommitted and the sprint can never be cut. Commit
them, per the slice-defect path below. The slice is transient either way: never commit it.

## Step 1 — Understand the issue

Read the `task_id` contract in `$sprint_artifact` (its `requirements[]` carries the statements
at issue), the implicated requirement's entry in `paths.design_slice` and in its
`paths.design_backlog` design, and any ADR in `paths.adrs` the contract or slice cites for it.
When `task_id` is `null` (a stage-boundary DI), there is no task contract — ground in the
implicated requirement's `paths.design_slice`/`paths.design_backlog` entries and the ADRs they cite.

## Step 2 — Verify it IS a spec defect

Walk these checks in order and take the first that holds:

- **The spec is right and the contract diverges from it** — the requirement/ADR is
  buildable as written; the defect is in an acceptance criterion or a
  `depends_on` edge. Below your altitude: do **not** amend anything. Set the `di_id`
  entry's `fix_kind: contract_amendment` in `$di_artifact`, **leave its `status: open`**,
  and report the one-line reason — the orchestrator re-routes the issue to `wf-swa`. Go to
  Telemetry (SKILL.md Phase 7) with outcome `escalated`.
- **The spec is right and already-merged code diverges from it** — the requirement/ADR is
  buildable as written; a regression in code an earlier task merged breaks it, and the fix
  is a **source** change (not a reworded statement). Not a spec defect, and never yours to
  write — you do not touch source. Set the `di_id` entry's `fix_kind: component_defect` in
  `$di_artifact`, **leave its `status: open`**, and report the one-line reason — the
  orchestrator re-routes the issue to `wf-swa`, which authors a follow-up build task at
  component altitude. Go to Telemetry (SKILL.md Phase 7) with outcome `escalated`.
- **A requirement or ADR is wrong** — unbuildable, self-contradictory, or contradicting
  the capability that drives it, while that capability itself is sound. A spec defect:
  go to Step 3.
- **The driving capability is wrong** — the amendment would change *what the user needs*,
  not how the system meets it. Above this issue's scope: leave the entry open, halt and
  report to the human (Halt conditions).

## Step 3 — Minimum-amend the spec

**Load `references/requirement-syntax.md` before rewording any requirement**, and
`references/adr-rules.md` before touching an ADR. Make the **smallest** change that
resolves the issue, in every artifact that carries the defective statement:

- the requirement's entry in `paths.design_slice` **and** the same requirement in its
  `paths.design_backlog` design — amend both; the two must not diverge;
- the ADR in `paths.adrs`, when the defect is a recorded decision;
- where the amended statement appears verbatim in the `task_id` contract's
  `requirements[]` (skip when `task_id` is `null` — a stage-boundary DI has no such
  contract), update that copy to match — a stale copy reproduces the defect.
  Change nothing else in the contract: its acceptance criteria and task shape are
  `wf-swa`'s.

Never reshape boundaries, add requirements, or redesign beyond the defect — a fix that
needs re-design is a halt (below), not a bigger amendment.

## Step 4 — Mark resolved

Set the `di_id` entry's `status: resolved` in `$di_artifact`. That flag is the signal the
issue is closed.

## The slice-defect path

The entry's `blockers[]` are the whole set to resolve, and they interact — resolve **all**
of them in one run, never one at a time.

Run default mode (SKILL.md **Process**) with Phase 1 replaced by the grounding below and
**Phase 4 skipped entirely**:

1. **Ground in the blockers.** Read each blocker's `requirement`, `summary`, `evidence`,
   and `needs`; the `paths.design_backlog` design the slice was cut from; `paths.design_slice`; and
   each ADR in `paths.adrs` whose `governs_components` names a component a blocker implicates.
   Reuse what the entry's `working_notes[]` already settle rather than re-deriving them.
   Drill what the blockers implicate (SKILL.md **Scouting & the drill-cache**). **Derive the
   requirement register and read its in-scope entries**, as Phase 1's grounding does — Phase 3
   triages every requirement against it, and the supersession halt below reads it. Reconcile
   nothing, and drain nothing.
2. **Phases 2, 3, and 5** as written. Reshape boundaries and mint requirements as the
   blockers demand — Step 3's limits govern the `spec_amendment` path, not this one. Take
   each non-obvious decision that stays **below** `references/adr-rules.md`'s ADR threshold
   yourself and record it in your report — there is no Phase 4 to present it at.
3. **Phase 6 steps 1–3** — finalize the ADRs, amend the design in `paths.design_backlog`
   in place, and re-cut `paths.design_slice`, including its `wf slice check` gate and, once that
   gate passes, step 3's closing of the `di_id` entry. A gate **failure** means your re-cut
   rests on an assumption nobody ratified — that is the assumption halt below: write `paths.decision_prep`
   and halt. SKILL.md's "return to Phase 4" does not apply here; Phase 4 does not run in
   this mode.
4. **Phase 6 steps 5–6, skipping step 4's human confirm** — no human ran, so there is no
   go-ahead to ask for and step 5's "On approval" does not gate you. Commit unasked: stage
   and commit exactly `paths.adrs` + `paths.design_backlog`. Leave them
   uncommitted and `wf-orchestrate` cannot cut the sprint branch: it gates on a clean working
   tree, and these are committed paths. Step 4's forbidden-environment carve-out still
   applies — report what is left uncommitted and stop, a clean outcome.
5. Report every file you amended. **Delete `paths.decision_prep` if an earlier escalation of this
   `di_id` left one on disk** — the issue is resolved, so its prepared decisions are dead,
   and a leftover file hijacks the next default `wf-sa` run.

## Halt conditions

Halt and report with outcome `escalated` if:

- `di_id` is not in `$di_artifact`.
- The driving capability itself is wrong, too big to cover whole (SKILL.md Phase 3), or its
  meaning is at stake — the human owns the *why*.

On a **`spec_amendment`**, also halt if:

- The defect is contract-level or a merged-code source regression (Step 2) — reclassify it
  there; do not resolve it. Never reclassify a `slice_defect`: its `task_id` is `null`, so
  the kind you reclassify it to has no task to park and no route — it becomes an open entry
  nothing can dispatch and no sweep can clear. A slice blocker that is really wf-swa's is
  still yours to re-cut around.
- a non-null `task_id` is not in `$sprint_artifact` (a `null` `task_id` is a slice-scoped or
  stage-boundary DI, not a halt here).
- The minimum fix would reshape a component boundary or ripple beyond the implicated
  requirement(s) and ADR — that is a re-design, not a surgical amendment.

On a **`slice_defect`**, `task_id` is `null` and no sprint exists — never halt on their
absence. Beyond the driving-capability halt above, also halt if:

- A decision meets `references/adr-rules.md`'s three-condition ADR threshold — the human
  ratifies load-bearing architecture.
- You would need to record an assumption (Phase 3: a driver's wording admits more than one
  reading and you must pick one). Only Phase 4 ratifies an assumption and `wf slice check`
  fails the slice on an unratified one, so needing an assumption is itself the halt. Never
  mark one CONFIRMED to clear the gate.
- Resolving a blocker would **supersede a shipped requirement** — one the requirement
  register (step 1) carries a `[REQ:<id>]` / `[SYS-TC:<id>]` tag for. The human ratifies
  retiring or changing
  shipped behaviour. No gate catches this one for you: `wf slice check` reads assumptions
  only, so a supersession you write here reaches the build unratified and silent.

**Halting a `slice_defect` on any of those four — the driving capability, an ADR-threshold
decision, an assumption, or a supersession — write `paths.decision_prep` first.** Halt without it
and the run's reasoning is lost and the human restarts it cold. Head it with the `di_id`, then, for
**each** decision you prepared but could not take, write both:

- the full brief prose, parts 1–3 of SKILL.md **Decision brief** — background, every option
  with its honest pros and cons, and your recommendation with the risk it accepts; and
- the fields Phase 4a's `render_design.py` `decisions` block takes — `id`, `title`,
  `question`, `options[{label, pros, cons}]`, `recommended`, `status`, `components`.

Leave the `di_id` entry `status: open`.
