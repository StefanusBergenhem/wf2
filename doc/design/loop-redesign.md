# wf2 loop redesign — continuous iterative delivery

**Status:** accepted 2026-07-31 (Stefanus + Claude interview, 19 decisions).
**Supersedes:** the waterfall design→drain shape (design backlog, wf-orchestrate
skill, wf-spec-fix, EARS/REQ layer, human-gated per-design SA sessions).
**Governing ruling:** this is a considered end-state redesign, not incremental
scope growth — build the complete new concept; the failure mode is wholesale
revert with learnings carried back. No half-way points.

---

## 1. Why

Evidence from the dems pilot (13 sprints, telemetry + archives):

- **Context cost tracks unit-of-work, not role prose.** wf-tl: a 296-word skill
  averaging 219–232k context because it decomposes a whole sprint at once. Every
  role scoped to one question/one diff (drill, review, discover) sits under 100k.
- **Completeness is discoverable, not designable.** One capability was closed and
  re-opened three times despite design-time adequacy gating; the information
  needed to judge a capability complete only exists after implementation.
- **Upfront fan-out is the dominant defect source.** The 55-task spec-first
  sprint: 13 contracts instantiated from one template before any ran; the same
  spec gap rediscovered 4× (L-087), the same name collision 4× (L-088),
  class-wide fixes blocked by task-scoped repair rules 5× (L-078). 18 design
  issues, 11 repair tasks appended mid-sprint — while 54/55 tasks built green on
  attempt 1 and 0 issues escalated. **The execution machinery works; the
  waterfall planning layer is what fails.**
- **The same fact stored in multiple places drifts.** Contract contradictions
  (L-069, L-101, L-044, L-102) all trace to redundant representations, not to
  missing precision.

The redesign inverts the planning layer: design just-in-time, one deliberately
partial iteration at a time, converging on capabilities by adversarial proof
instead of executing a stored plan — and moves orchestration from an LLM session
to a script, per the governor (anything a script can do, a script does).

## 2. The loop

```
HUMAN (durable inputs)              CONTINUOUS DRIVER LOOP (one iteration = one sprint)
──────────────────────              ────────────────────────────────────────────
PO session   → CAPABILITIES        ┌─► sprint start: refresh discover brief
SA session   → CHARTER + ADRs      │   wf-designer (originate): revise PLAN,
PR review    → merge / comments /  │     cut slice = ordered increments
               request-changes     │     [soundness walk + design-time adequacy]
Escalation   → decision_prep       │   per increment:
  ruling       ruling → resume     │     wf-tl (JIT): task contracts → sub-layers
                                   │     per sub-layer: build → review → merge
                                   │     boundary: stage_check + checkpoint +
                                   │       wf-designer (repair) on design issues
                                   │   closeout: retro → adequacy (served caps)
                                   │     → drain → ship stacked PR
                                   └── next sprint, until a stop rule fires
```

**One driver invocation loops sprints continuously.** Each sprint branch stacks
on the previous unmerged one; each ships its own PR. Stop rules (§8.4):
escalation, work exhaustion, manual stop, stack-depth cap.

The human's steady-state role: set direction (PO/SA sessions), review PRs, rule
on escalations. PR review is a **brake and feedback channel, not a gate** — the
loop does not wait for approval. Comments flow back through existing channels
(LEARNINGS entries, charter/capability edits); a request-changes review is a
stop signal; fix-forward is the default resolution, backward amendment of the
stack is manual and exceptional. The driver never rewrites the stack.

## 3. Durable artifacts

| Artifact | Owner | New? | Content |
|---|---|---|---|
| `paths.capabilities` | PO session | no | user-voice needs; open work-set; drains on adequacy proof |
| `paths.charter` | SA session | **new** | direction: target shape (fat-marker), ranked forces, domain language, sequencing rationale, no-go zones |
| `paths.adrs` | SA session (designer drafts) | no | irreversible decisions; constraint lines |
| `AGENTS.md` tree | retro proposals, human applies | no | local commands/gotchas/conventions |
| `paths.plan` | **wf-designer** | **new** | rolling plan: next few milestones and why |

**The charter** is the written-down direction the autonomous designer converges
toward — the replacement for the human being present in every design session.
Governor-clean: it describes the *future*, the one thing code cannot report.
Guardrails: single file; SA-session-owned (the designer reads, never writes);
hard size cap enforced by a hygiene rule (`hygiene.charter_max`, default ~120
lines); elements are deleted in SA sessions once the repo reaches them. No
mechanical staleness check.

**The plan** is the one durable file the autonomous role writes: milestone
altitude only (no requirements, no component detail), hard cap
(`hygiene.plan_max`, default ~60 lines), **mandatorily revised at every sprint
start** (the prior plan is a hypothesis re-validated against fresh repo state,
never a commitment executed blindly), trimmed as milestones ship. It rides in
every sprint PR — the reviewer's direction-drift audit surface for the stacked
loop.

**No design backlog.** Designed work is built in the same sprint that designs
it. The slice's `serves:` header plus the merge record are the drain anchor;
the plan is the only cross-sprint working state. The backlog file, its
template, and its trim machinery are deleted.

**No priority encoding on capabilities.** Work selection is designer judgment
from charter + ADRs + capabilities + learnings. Human boundary-setting *is* the
prioritization; the loop implements everything in scope and stops on
exhaustion; a new PO/SA session refills the well. A sprint is "what makes sense
next" — an enabler, hardening, a bugfix batch, or a capability increment.

## 4. Roles

**`wf-designer` (new; dispatched headless session) — the autonomous design
role.** Three entry modes:

- **originate** — the per-sprint default. Refreshed grounding (discover brief,
  system-test register, drills — dispatched directly; a headless top-level
  session has full dispatch power), revise `paths.plan`, cut the slice as
  ordered increments, mint SYS-TCs, write cross-increment interface contracts.
  **Pre-release review loops (kept from old wf-sa Phase 5):** the
  design-heuristics soundness walk (auditable one-line verdicts in the slice)
  and a **design-time wf-adequacy dispatch** — verdict question: *does the
  scenario set prove what this iteration claims* (the slice states its claimed
  scope; slices are deliberately partial). Fold residuals back until adequate
  before releasing the slice to the increment loop.
- **repair** — resolve one design issue at whatever layer it lives (absorbs
  wf-spec-fix): contract defect → amend and return; merged-code defect →
  follow-up task; slice defect → re-cut the *remainder* (merged increments are
  facts); class-wide defect → amend every affected undispatched contract and
  record the sweep.
- **resume** — consume a human ruling from `paths.decision_prep` and continue
  where the halt left off.

**The escalation gate (all modes).** Four criteria halt to the human — the
designer writes `paths.decision_prep` (draft decision briefs, options,
recommendation) and the driver pauses:

1. a decision that meets the ADR threshold (adr-rules);
2. a capability recast — resolving something would change *what the user
   needs*, not how it is met;
3. supersession of a shipped SYS-TC scenario;
4. charter contradiction — the right design would violate the charter.

Everything below the gate is decided autonomously and logged in the sprint's
decision report (shipped in the PR body): component-level supersessions,
NFR/authz deferrals, interpretive assumptions that do not change a capability's
meaning, plan reshuffles.

**Retired designer rule:** "cover each driver in full / never design part of
one" is deleted. The designer designs explicit *increments toward* a
capability, states what the iteration claims and knowingly leaves, and is
forbidden from claiming completion — the close-time adequacy gate detects done
(§7).

**`wf-sa` (interactive, main context) — shrinks to the human trio:** charter
authorship, ADR-threshold decisions, escalation rulings / capability
ratification (consumes `decision_prep`, records rulings for resume mode). No
backlog authoring, no per-design alignment sessions.

**`wf-tl` (dispatched per increment, JIT).** Reads the repo as it exists after
increment N−1 merged. Widened: authors the full requirement-grade content —
acceptance criteria (the single spec layer, §5.2) — plus task decomposition
with `depends_on`. Bound by the increment's allocation: work it cannot trace to
the increment is a slice defect raised to the designer, never invented scope.
Raises a slice defect when an increment decomposes past the task cap.

**Unchanged in shape (mechanical rewrites for new artifact fields only):**
wf-build, wf-review (chain still `review.passes`), wf-stage-repair (increment
boundaries), wf-drill, wf-adequacy, wf-po, wf-retrospective (runs at every
sprint close — it pumps the learnings the next slice consumes), wf-discover
(driver-invoked refresh at every sprint start), wf-init (new templates).

**Deleted roles:** wf-spec-fix (absorbed into designer repair mode),
wf-orchestrate the skill (replaced by the driver; frozen bugfix-only during
bring-up, deleted after the first green dems sprint).

## 5. Sprint anatomy

### 5.1 Structure

```
sprint (one loop iteration, one branch, one PR)
└── increments (designer-declared, ordered, ~2–5; slice-check cap)
    └── sub-layers (pipeline topo-layers of TL's depends_on)
        └── tasks (parallel worktrees; build → review chain)
```

An **increment** is a design milestone: goal, component allocation, end-to-end
flow (wiring included), and an **observable checkpoint** ("after this, X
demonstrably works"). The TL is dispatched once per increment when its turn
comes. **The sprint's contract file is cumulative:** each increment's TL
*appends* its tasks (every task carries `increment:`) and never rewrites or
removes earlier increments' entries — merged increments are facts; a slice
re-cut prunes only the invalidated unmerged increments (`wf sprint prune`).
The close-time merge record and adequacy candidates therefore see the whole
sprint, not the last increment. Merges to the sprint branch happen per
sub-layer (today's batch-merge machinery). At the **increment boundary**: heavy checks (`commands.stage_check`),
checkpoint verification, and design-issue repair (designer repair mode) — so
the next increment's TL always plans against a checked, merged tree.

**Sizing discipline.** Config caps as trust knobs: `limits.increments_per_sprint`
(default 4, slice-check error) and `limits.tasks_per_increment` (default 10 —
the TL raises a slice defect when an increment exceeds it, forcing a designer
split or re-cut). **Pilot-then-fleet** is a designer skill rule: when an
increment's allocation contains N structurally identical items, cut a pilot
increment (one item end-to-end, template proven) before the fleet increment;
the task cap backstops it mechanically.

### 5.2 The slice

Transient, per sprint. Sections: `serves:` header (every CAP/L id, written out
in full), the design narrative, the **claimed scope** (what this iteration
delivers of each served capability's promise — the design-time adequacy input),
the ordered increments, the SYS-TC cases (Gherkin-light, `Covers: CAP-n`,
minted from `id_counters.sys_tc`), interface contracts for new seams that span
increments (a seam both sides of which are built in different increments must
be shaped here — source cannot answer it at TL time), the supersessions list,
NFR & authz outcomes, binding ADRs, soundness verdicts, and the decision log
(assumptions, below-gate supersessions/deferrals) for the PR report.

### 5.3 The task contract (reworked — narrative-first, no EARS layer)

The V-model's separate requirement statement exists for independent consumers
across time; here the TL authors statement, criterion, and contract in one
sitting and the build agent reads them at once. dems showed the layer's real
cost: the same behaviour represented in several fields that drift apart. So the
acceptance criterion *is* the requirement, and the contract has exactly four
sections — each fact lives in exactly one place:

```yaml
id: T3
increment: 2
covers: [CAP-024]          # and/or L-ids; trace = task → increment → CAP/L
story: |                   # 2–4 paragraphs, FIRST field: what this task builds,
                           # why, how the change flows through the code,
                           # what is new against what exists.
acceptance:                # the single requirement-grade layer
  - id: AC-1               # atomic, EARS-disciplined phrasing (trigger →
    criterion: ...         # observable response), one behaviour per criterion
    tests:
      - {level: integration, seam: "HTTP + postgres", target: TestZonesPatch}
  - id: AC-2
    criterion: ...
    verified_by: inspection   # gate/config facts: name the source fact
boundaries: |              # ONE merged section: out-of-scope, read-only files,
                           # fixed interfaces. Nothing here may be contradicted
                           # by any other field — one home, no drift.
grounding:                 # pointers only, no prose restating code
  - "backend/internal/handlers/zones.go:88 — current mount point"
  - dependency_commits: {T1: <sha>}
system_tests: [SYS-TC-44]  # e2e tasks only; text inlined by materialize
```

`implementation_notes` is dead. REQ ids and `id_counters.req` are dead. The
increment's slice section (the stage narrative) rides in every task envelope,
so a build agent reads: increment narrative → covers → story → acceptance →
boundaries → grounding (plus the task's title), and nothing else — `covers`
rides along because build and review judge scope against it.

**Contract checks (sprint-check successor, re-anchored):** story present and
non-trivial; every AC carries tests[] or verified_by; boundaries is a single
section; grounding pointers resolve (C11 successor); `tests[].target` unique
per package across the sprint's contracts (L-088); depends_on acyclic within
the increment; task count within cap; every task traces to the increment's
allocation; cumulative-file integrity — task ids unique across the whole
sprint, increment ordering monotone, no task block silently absorbed into a
neighbour's YAML list (C17).

### 5.4 Defect classification (the repair ladder)

Every rung is a cold-read boundary; classification routes the fix:

1. build cannot satisfy a coherent contract → build feedback loop (unchanged);
2. contract self-contradicts or diverges from its increment → contract defect,
   designer repair amends (class-wide sweep allowed and recorded);
3. increment cannot be specified within the slice's allocation → slice defect,
   designer re-cuts the remainder;
4. resolving anything would trip the four-criterion gate → decision_prep, halt.

## 6. The driver

A Python program (`tools/driver/`, TDD like all wf2 scripts) that executes the
loop deterministically. It replaces the wf-orchestrate skill entirely; the
never-run POC branch is mined for parts (worktree git ops, dispatch
bookkeeping) and then retired.

### 6.1 Agent invocation — harness portability by config

The driver launches every role via a config-keyed command template:

```yaml
driver:
  agent_cmd: 'claude -p --dangerously-skip-permissions "{prompt}"'   # per-install
```

(OpenCode target renders `opencode run ...`, etc. — rendered at init like every
other harness difference.) **The agent process contract is: exit code +
artifacts on disk.** The driver never parses agent output — every verdict comes
from disk via the CLI helpers (`inspect-build-return`, slice/sprint checks, DI
files), exactly the routing-on-prose ban the skill already enforced. Any
harness that can run a prompt headlessly and leave files behind is supported by
definition.

### 6.2 Phase machine (per sprint)

`sprint_start` (branch off previous sprint tip; discover refresh) →
`designing` (wf-designer originate; on halt → `awaiting_ruling`) →
`increment_loop` (per increment: TL → sub-layer dispatch/build/review/merge →
boundary checks/repair) → `closeout` (retro → close-time adequacy → drain →
ship PR) → next `sprint_start`, or stop. All state on disk
(`paths.pipeline_state` successor); every phase is resumable — a restarted
driver reconstructs position from disk, exactly as the skill's kickoff §0 did.

Parallelism: tasks within a sub-layer run concurrently (worktrees), bounded by
`driver.max_parallel`. Preflight/long gates run with explicit timeouts derived
from measured runtimes — never trusting a harness default (wf-learning L-090
is a requirement here, not a skill instruction). All file rewrites through the
CLI preserve comments/unicode and skip no-op writes (L-106 likewise).

### 6.3 Stacked PRs

Sprint N+1 branches from sprint N's tip. Its PR targets sprint N's branch while
that is unmerged, else `project.base_branch` (standard stacking; merging
bottom-up retargets automatically on GitHub). Stack depth is **derived, not
stored**: the count of shipped sprint branches not yet merged into the base.

### 6.4 Stop rules

1. **Escalation** — designer wrote `decision_prep`: finish safely in-flight
   work, pause to `awaiting_ruling`.
2. **Work exhaustion** — no open, unparked capability or learning remains in
   scope: clean exit; a new PO/SA session refills.
3. **Manual stop** — a stop file (config-keyed path): finish the current
   sprint cleanly, ship, exit.
4. **Stack depth cap** — `driver.max_unmerged_sprints` (default 3): pause until
   PRs merge. Merging feeds the loop; ignoring it bounds regret.

A request-changes review acts as (3) — surfaced to the driver at the next
boundary via a `gh`-derived check, config-keyed command.

### 6.5 Telemetry

Usage hooks unchanged (per-role `context_max` remains the sizing metric). The
driver additionally appends dispatch/routing/stop events to the telemetry log —
the retrospective and `wf telemetry roles` gain an exact, non-fuzzy join.

## 7. Convergence — how a capability actually finishes

Nobody declares done; the gate detects done.

- At each **sprint close**, dispatch `wf-adequacy` for every capability in the
  slice's `serves:` header — full-promise question: does the *shipped* scenario
  register now cover the capability's whole promise? (Adversarial, grounded in
  source, never in the design's own decomposition — unchanged doctrine.)
- **adequate** → the capability drains (snapshot to archive, remove from
  `paths.capabilities`).
- **inadequate** → residuals append to the capability's notes; they are input
  to the next plan revision.
- **3 consecutive inadequate** verdicts → the capability is **parked** for a PO
  session — the promise is mis-scoped or unfalsifiable; that is a wording
  problem, not a design problem. Parked capabilities are skipped by the
  designer; all-parked triggers work exhaustion.
- **Re-opening a drained capability is a first-class normal event** (a later
  learning or PO decision), not a failure. The iterative frame expects it.

Learnings drain at sprint close from the `serves:` header + merge record, as
capabilities' requirement-lane machinery did before — minus the backlog
middleman.

## 8. Config (new keys, each with a named consumer)

```yaml
paths:
  charter: ".wf/charter.md"          # designer reads; SA session writes
  plan:    ".wf/plan.md"             # designer writes; PR reviewers read
driver:
  agent_cmd: "..."                   # driver: launch template
  max_parallel: 4                    # driver: sub-layer concurrency
  max_unmerged_sprints: 3            # driver: stack depth cap
  stop_file: ".wf/transient/STOP"    # driver: manual stop
limits:
  increments_per_sprint: 4           # slice check
  tasks_per_increment: 10            # TL over-cap slice defect
hygiene:
  charter_max: 120                   # hygiene rule
  plan_max: 60                       # hygiene rule
```

Removed: `id_counters.req`, every backlog path/knob, spec-fix routing entries.

## 9. Deleted inventory

- `skills/wf-orchestrate/` (after first green driver sprint), `skills/wf-spec-fix/`,
  `agents/wf-spec-fix.md`; wf-sa's Phase 4 per-design alignment machinery and
  fix/backlog phases; wf-tl's whole-sprint default mode.
- `design-backlog` template, `_trim_backlog`/lane-label parsing (L-105's
  habitat), backlog grep conventions, `wf pipeline complete-sprint`'s backlog
  branch (replaced by slice-header drain).
- The EARS/REQ layer end-to-end: requirement-syntax (reworked into
  criterion-syntax for ACs), REQ id minting, REQ-keyed checks (B5, C9's
  REQ-facing halves), `testing_mandate` remnants.
- The old task-contract template and `implementation_notes`.
- The POC driver branch after mining.

## 10. Migration

1. **wf2 implementation** (order): CLI/pipeline re-plumb + new checks (TDD) →
   driver (TDD) → role authoring (via /skill-builder; new wf-designer, shrunk
   wf-sa, widened wf-tl, mechanically-updated survivors) → templates
   (charter/plan/slice/contract; wf-init) → render/install updates →
   validation-repo smoke.
2. **dems conversion (one SA session, human + assistant):** convert the
   undrained design backlog — direction and unbuilt intent → charter;
   load-bearing decisions → ADRs (most already exist); capabilities stay open
   untouched (nothing is proven by conversion); learnings carry over unchanged;
   seed `paths.plan` from what the backlog said was next. Archive the backlog
   file (write-only maintainer archive), then delete it. Config sync; install.
3. **Bring-up:** first runs with `driver.max_unmerged_sprints: 1` — effectively
   one sprint per invocation until the loop earns trust; raise the knob as
   PR-review reversals stay low. This is a knob setting, not a design variant.
4. **Revert plan:** wf2 is git-history; dems re-installs the pre-redesign
   commit; the archived backlog snapshot restores the old working state.
   Learnings from the failed attempt carry back like any dogfood cycle.

Adjacent (not solved here): dems' preflight duration multiplies every loop
iteration's wall time — a dems-side workstream.

## 11. What we measure (tuning signals)

- **Escalation-gate precision:** halts per sprint (too many → gate too tight)
  vs PR-review reversals of autonomous decisions (too many → too loose).
- **Plan-repair tax:** design issues per sprint and mid-sprint task growth —
  the 55-task sprint's 18/25% is the baseline to beat.
- **Role context:** per-role `context_max` — target all roles ≈100k; wf-tl and
  wf-build are the watch items.
- **Convergence:** adequacy verdicts per capability over time; parks; re-opens.
- **Throughput:** wall-clock per sprint with zero human attention (driver runs
  unattended; the human cost is PR review only).
