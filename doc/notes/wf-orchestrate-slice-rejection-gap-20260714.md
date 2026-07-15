# wf-orchestrate — the slice-rejection dead-end (2026-07-14)

**Source:** dems, `/wf-orchestrate` run on 2026-07-14 against the compliance-core slice
(wf-sa commit `8b043d2`). The run halted in `preparing` having built nothing.
**Scope:** the `wf_friction` channel — the toolkit, not the dems code. The dems-side findings
(five spec blockers + a live data-loss bug) were transcribed by hand to
`dems/doc/design/compliance-slice-swa-escalation-20260714.md`; that transcription **is itself
finding F1 below.**

> **⚠ Superseded in urgency — F1–F4 all recurred 111 minutes later.** The wf-sa re-design this note
> triggered was itself rejected by wf-swa on the same day, reproducing every finding below verbatim
> and adding two more (a missing convergence check, and a rule collision inside wf-swa that caused
> the second rejection). Read
> [`wf-slice-rejection-loop-round2-20260714.md`](wf-slice-rejection-loop-round2-20260714.md) —
> it establishes this is a **cycle, not an incident**, and re-prioritises the fix list.

Legend: **[NEW]** not previously logged · **[DUP]** second data point on a CANDIDATES entry ·
**[OK]** working as intended, recorded so it isn't re-investigated.

---

## TL;DR

`wf-swa` rejected a design slice during `preparing`. That is the gate **working** — it caught five
real spec defects, one of which invalidates an entire workstream's interface contract, plus a live
production data-loss bug the drill had missed. Nothing was built, nothing was half-written, and the
whole thing cost one subagent.

The problem is what happened **after** the rejection:

1. **wf-swa's findings existed only as prose.** Default mode has no artifact channel, so the
   orchestrator — which is explicitly forbidden from routing on prose — was handed nothing it
   could route on. A human had to hand-transcribe five blockers into a markdown file or they'd
   have died with the session.
2. **There is no route from "slice rejected" to "wf-sa re-designs."** The pipeline dead-ends at
   `HALT and report`. Even if F1 were fixed, the design-issue plumbing **cannot** carry it:
   `record-design-issue` needs a task to park, and `wf-sa` fix mode needs a sprint to read. In
   `preparing`, by definition, neither exists.
3. **wf-sa fix mode is the wrong target anyway**, and the right one (default mode) is interactive
   and therefore undispatchable.

| # | Sev | One-line | Fix surface |
|---|-----|----------|-------------|
| F1 | **HIGH** | wf-swa default-mode escalation is prose-only — no machine-readable channel | `wf-swa/references/default-mode.md`, `paths.design_issues` schema |
| F2 | **HIGH** | No route from a `preparing` slice-rejection to wf-sa; the DI plumbing structurally can't carry it | `wf-orchestrate` §1.1, `wf pipeline record-design-issue`, `dispatch-fix` |
| F3 | MED | wf-sa fix mode can't take a slice rejection; default mode can't be dispatched (interactive) | `wf-sa` mode boundary — or accept-by-design |
| F4 | LOW | `sweep-transients` leaves a stale resolved-only `design-issues.yaml`, reporting it in neither `deleted` nor `skipped` | `wf orchestrate sweep-transients` |

---

## What actually happened

1. `/wf-orchestrate` on dems. Kickoff clean: `sweep-transients` → `{"deleted": [], "skipped": []}`;
   `reclaim-stale` → `[]`; `current-phase` → `idle`.
2. `transition --to preparing`. `$SPRINT` absent, `$DESIGN_SLICE` present (18.6 KB, authored
   earlier the same day) — the exact precondition for §1.1.
3. Dispatched `wf-swa` with the Preparing envelope (`mode: default`), per §1.1.
4. **wf-swa returned HALTED / escalated-to-SA** with five spec-layer blockers, a verified live bug,
   and a slice-size objection. It wrote no sprint — correctly, and it said so explicitly.
5. Orchestrator re-checked `$SPRINT` on disk (still absent) → §1.1's only instruction is **"HALT and
   report"** → halted; telemetry recorded `outcome: halted`.
6. Human instructed: *"dispatch wf-sa in fix mode with all the issues raised by swa as input."*
7. Orchestrator read `wf-sa/references/fix-mode.md`, determined the dispatch would **provably halt
   at Step 1** (no `di_artifact` entry, no `sprint_artifact` at all), and declined — escalating the
   routing decision back to the human rather than burning a subagent to discover it.
8. Human elected to run `/wf-sa` in default mode themselves.

**wf-swa behaved exactly per spec.** This is worth stating plainly because the failure *looks* like
"the agent forgot to write its findings down." It didn't. `references/default-mode.md` L111–121:

> ## Halt conditions
> Halt and report with outcome `escalated` if:
> - `$DESIGN_SLICE` is absent (it is wf-sa's output).
> - …
> - A requirement cannot be made testable, or its criteria cannot be turned into a task without
>   crossing a component boundary — **flag to the SA.**

"Flag to the SA" — with no channel named, no artifact, no schema. The skill asked for prose and got
prose. The gap is in the toolkit's design, not the agent's compliance.

---

## F1 — [NEW · HIGH] wf-swa default-mode escalation has no machine-readable channel

**What.** `design-issues.yaml` (`paths.design_issues`) is written **only** by build and review, per
`wf-orchestrate` § Return protocols. wf-swa's *fix* mode reads and reclassifies DI entries; its
*default* mode neither reads nor writes them. So a default-mode escalation — the SWA rejecting a
slice — has nowhere to land but the return message.

**Why it bites.** It contradicts the orchestrator's own hard constraint:

> **Route on the helper's verdict, never on a sub-agent's prose.**
> Pipe every sub-agent's output to `/tmp/wf-orch-<task-id>.log` and read only the verdict the
> helper emits — never echo a diff or test output into your own context.

There is no helper and no verdict for this path. The orchestrator is left with *only* prose, and
the skill's instruction to pipe sub-agent output to `/tmp` and not read it would, if followed
literally here, **discard the findings entirely**. The two rules are in direct tension on this
path.

**Observed cost.** Five blockers — including one (`uuid.UUID` vs `string`, with the
`OutsideRoomID = "outside"` case that ADR-002 created deliberately) that invalidates workstream 1's
whole interface contract — plus a verified live data-loss bug, existed **only** in one subagent's
return text. Recovering them required a human to ask for a hand-written transcription. wf-swa's
telemetry row records `outcome: escalated` and nothing about *what* was escalated; once that
context window is gone, so are the findings.

**Proposed fix.** Give default mode an artifact. Either:
- **(a)** extend `paths.design_issues` with a `preparing`-scoped entry (`task_id: null`, a
  `fix_kind` such as `slice_defect`), and have `default-mode.md`'s halt conditions write one entry
  per blocker before returning; or
- **(b)** a dedicated `paths.slice_rejection` artifact wf-sa reads as a first-class input.

(a) reuses the existing schema but drags in F2's plumbing problem. (b) is cleaner if the routing
stays human-gated (F3).

Minimum viable regardless of route: **default mode must persist its blockers to disk before it
halts.** Even with no automated consumer, that alone converts this run's manual transcription into
a file wf-sa can be pointed at.

---

## F2 — [NEW · HIGH] No route from a `preparing` slice-rejection to wf-sa — and the DI plumbing structurally cannot carry one

**What.** `wf-orchestrate` §1.1 is the entire route:

> …dispatch the `wf-swa` agent with the **Preparing envelope** to build the sprint from the design
> slice, then re-check `$SPRINT` on disk: still absent → **HALT and report** (wf-swa could not
> build a sprint from the slice).

**The parenthetical encodes a wrong assumption.** It reads the absent sprint as a *capability*
failure — wf-swa couldn't. The real case is wf-swa **correctly refusing**: the slice is defective
and decomposing it would launder five spec defects into task contracts. Same on-disk symptom,
opposite meaning, and they want opposite routes (one is "the SWA is stuck, help it"; the other is
"the slice is wrong, send it back to the SA"). The orchestrator cannot distinguish them because
of F1 — no verdict, only prose.

**Why fixing F1 alone is not enough.** Even with a DI written, the existing plumbing rejects it at
two independent points:

- `wf pipeline record-design-issue <di_id> --task <id> --severity <s> --fix_kind <k>` — **parks the
  task.** In `preparing` there is no task and no sprint; there is nothing to park.
- `wf-sa` fix mode halt condition: *"`di_id` is not in `$di_artifact`, or `task_id` is not in
  `$sprint_artifact`."* — **no sprint file exists**, and cannot, since its absence is the very
  thing that triggered this path.

The design-issue mechanism is built end-to-end on the assumption that a DI is raised *by a task,
against a sprint*. A slice rejection is raised **before either exists**. It is not a task-scoped
defect; it is an input-scoped one.

**Proposed fix.** Add an explicit §1.1 branch distinguishing *"wf-swa halted: slice defective"* from
*"wf-swa halted: other"*, keyed on the artifact F1 introduces. Then decide F3's question — whether
the branch routes to wf-sa automatically or exits to the human by design. If it exits, say so **in
the skill**, so the orchestrator reports "the slice needs a wf-sa re-design, run `/wf-sa`" rather
than the current bare "wf-swa could not build a sprint," which misdescribes what happened.

---

## F3 — [NEW · MED] wf-sa fix mode is structurally mismatched to a slice rejection; default mode is undispatchable

**What.** With the human asking to route these blockers to fix mode, the orchestrator checked
whether it could. It cannot, on four independent grounds:

1. **Cardinality.** *"Resolve only this issue."* One `di_id`. A slice rejection is n blockers
   (here, five plus two non-blocking findings).
2. **Preconditions.** Halt condition 1 fires immediately — no `di_artifact` entry, no
   `sprint_artifact` (F2).
3. **Permitted operations.** *"Never reshape boundaries, add requirements, or redesign beyond the
   defect."* But **three of the five blockers require minting `internal/repository` requirements
   the slice does not contain**, and a fourth is a type-ownership/boundary call. Fix mode's own
   halt condition names the situation: *"The minimum fix would reshape a component boundary or
   ripple beyond the implicated requirement(s) and ADR — that is a re-design, not a surgical
   amendment."*
4. **Authority.** Fix mode *"works autonomously (no human alignment)."* But two blockers are
   genuine human rulings: the `uuid` vs `string` contract decision (which fights standing ADR-002),
   and REQ-64's *"every active entity"* scope (doors-only vs all entity types). Fix mode would
   either halt on *"The driving capability is wrong — the human owns the why"* or silently decide
   the project's architecture.

**So the correct target is wf-sa default mode** — Phase 2 for the boundary call, Phase 3 to mint
the missing requirements and restate the contract, Phase 4 to ratify, Phase 6 to re-cut.
**But default mode cannot be dispatched as a subagent:** Phase 4 is *"the interactive core"* —
*"**WAIT for the human** to ratify or redirect"*, *"Do not leave Phase 4 on your own judgement."* A
dispatched subagent has no human. It would stall or self-ratify.

Note the asymmetry this creates: `wf-orchestrate` **can** dispatch `wf-swa` default mode (it is
autonomous) but **can never** dispatch `wf-sa` default mode. The pipeline is therefore *structurally
incapable* of self-healing a bad slice, no matter what F1/F2 do.

**Question for the maintainer — is this a gap or accepted-by-design?** A reasonable position is
that it is **correct**: a slice defective enough to fail decomposition implicates decisions the
human owns, and wf-sa's whole design says the human ratifies the *why*. This run supports that —
B1 and B5 genuinely needed a human.

But two things are **not** acceptable-by-design even if the exit is:
- the **transcription burden** (F1) — the human should be handed a file, not asked to reconstruct
  five blockers from chat scrollback;
- the **misdescription** (F2) — the halt should say "slice rejected, run `/wf-sa`", not "wf-swa
  could not build a sprint."

Fixing those two makes a human-gated exit a *clean* outcome rather than a dead-end. Suggest
CANDIDATES **C32** if the auto-route is deferred — cf. **C31** (driver: autonomously resolve a
stage-fix DI), which is the same shape of question one phase later.

---

## F4 — [NEW · LOW] `sweep-transients` leaves a stale resolved-only `design-issues.yaml`, silently

**What.** Kickoff `sweep-transients` returned `{"deleted": [], "skipped": []}` while
`.wf/transient/design-issues.yaml` sat on disk — dated 2026-07-12, three entries, **all
`status: resolved`**, all from a sprint that shipped as dems PR #207.

**Two observations:**
- The file was reported in **neither** `deleted` nor `skipped` — so it is *invisible* to the sweep,
  not consciously exempted. If skipping it is intentional, it should say so in `skipped`.
- Resolved entries from a shipped sprint survive into the next run's `preparing`. `complete-sprint`
  archives the plan + final state, but evidently does not reset this file.

**Why it's LOW, not benign.** Currently harmless — `dispatch-fix` only acts on `open` entries, so
stale resolved ones are inert. But the file accumulates without bound across sprints, and it is the
file F1's proposed fix (a) would write into. If a `preparing`-scoped DI lands in a file that already
carries a previous sprint's residue, "is this DI from *this* run?" becomes a real question. Worth
resolving **before** building F1(a) on top of it.

---

## What worked (recorded so it isn't re-litigated)

- **[OK] The `preparing` gate.** wf-swa caught five defects *before* a single task contract was
  written. The cost of the catch was one subagent and zero rework. This is the gate paying for
  itself.
- **[OK] wf-swa's grounding discipline.** It verified against real source rather than the slice's
  claims — reading `room.go`, `door_write.go`, `property_definitions.go`, and actually decoding the
  UI's payload against the real struct tags. That is how it found a **live production data-loss bug
  the drill digest had missed** (`allowedValues`/`sortOrder` silently dropped on every Settings
  enum edit). The slice's drill had spotted the sibling `entityType` mismatch and stopped there.
- **[OK] The orchestrator's prose-vs-verdict discipline held under pressure.** Instructed to
  dispatch a route that would fail, it read the target skill's contract first and declined with
  evidence rather than complying and reporting a halt. The `HALT and report` outcome, and the
  idempotent `preparing` resume, both behaved exactly as documented.
