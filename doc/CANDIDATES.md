# wf2 — Candidates

Deferred observations about the wf2 toolkit itself: improvements, risks, and
thresholds worth acting on later. Not scheduled work — a parking lot so a
forward-looking note isn't lost. Promote an entry to real work when its trigger
fires; delete it when resolved.

---

## C8 — Agent frontmatter is Claude-format; pi/opencode untested

**Date:** 2026-06-15
**Context:** `install.sh` now renders the `agents/` category per harness (the first
agent is `wf-drill`). The render path (copy + token-subst) is harness-agnostic, but
the agent **frontmatter** (`name`/`description`/`tools: Read, Grep, Glob, Bash`) is
written in **Claude's** schema. Rendering to `.pi/agents/` or `.opencode/agents/`
copies that Claude frontmatter verbatim — pi and opencode may expect a different
agent-definition shape (tool-grant syntax especially).

**Observation:** only the Claude target is dogfooded, so this is deferred like the
telemetry adapters (the Claude usage-hook adapter shipped 2026-07-10; pi/opencode
adapters remain deferred on the same grounds) — the genuinely harness-coupled part
of an agent is its frontmatter. The body is harness-agnostic prose and renders fine
everywhere.

**Trigger to act:** when pi or opencode becomes a real target. Then verify each
harness's agent-definition schema and, where it differs, guard the frontmatter with
`wf:if <target>` blocks in `agents/wf-drill.md` (the renderer already supports them).

---

## C9 — Retrospective: telemetry-distil + cross-task run analysis BUILT; MEMORY.yaml store deferred

**Date:** 2026-06-20 (re-anchored 2026-07-31 for the continuous-loop redesign)
**Context:** `wf-retrospective` ships as the **dogfoodable slice**: read the telemetry
session log, distil `repo_observation` → `paths.learnings` (project learnings the design
role reads as drivers) and `wf_friction` → `paths.wf_learnings` (toolkit friction), dedup by
the `sources` provenance set, create-only `open` entries. It runs against the telemetry
PO/SA/TL/drill already write — no orchestration needed.

**Built since (2026-06-22):** the sprint-execution analysis now lands as **cross-task pattern
detection** — `wf-retrospective` reads `pipeline_state` (recurring rejections, design-issue
clusters by `fix_kind`, escalation/block causes) and distils the patterns no single session
can see into the same learnings streams, sourced by `sprint_id`. Per-task velocity/counts are
reported transiently, not stored. **2026-07-10:** feedback gained a `friction_kind` enum, so
the friction clustering step is now a mechanical groupby before judgement. **Still
deferred:** the maintained `MEMORY.yaml` lessons store (dedup, capacity-cap, confidence,
reinforcement) — wf1's governor-ish overreach, and nothing in wf2 consumes a
distilled-lessons store.

**The optimistic-close half resolved 2026-07-25:** learnings drain mechanically at sprint
close — `wf pipeline complete-sprint` removes a learning only when the slice's `serves:`
header named it *and* every task covering it merged (the merge record), so "designed but
never landed" can no longer close one.

**Trigger to act:** the orchestration half is done. Build the `MEMORY.yaml` store only if a
real consumer for a maintained lessons store appears (none today — the open learnings streams
suffice).

---

## C10 — Compliance / audit trace (capability → scenario → test walk)

**Date:** 2026-06-21 (re-anchored 2026-07-31 for the continuous-loop redesign)
**Context:** the **capabilities-as-open-work-set** reframe (2026-06-21 — completed
capabilities graduate OUT rather than accumulate as a durable catalog) was adopted
after establishing that nothing reads a *completed* capability. The one honest
exception identified: a **walked compliance/audit trace** (test → requirement →
capability → user-need), which regulated industries genuinely require.

**Analysis:** what a compliance trace needs on top of the shipped test tree is the
**capability → user-need** apex, which the graduation model drops. Retaining it is a
**project-specific** need (the adopter's regulatory regime), not something the general
toolkit should hoard by default — baking "keep every capability forever for audit"
into wf2 over-fits to one class of repo.

**Likely shape when built:** don't graduate completed capabilities into oblivion but
into a **trace store** (an archived capability ledger keyed to the requirement tags
that realize it), or reconstruct the chain on demand from preserved `[REQ]` tags + an
append-only graduation log. Pairs with **C11 (product description)** as its likely
host layer — both are the durable *external/record* tier sitting above the open
work-set.

**State 2026-07-31 (post loop-redesign):** nothing requirement-shaped persists in the
tree — the EARS/REQ layer is gone entirely, and the acceptance criterion in a transient
task contract is the only requirement-grade statement there ever is. What a trace can be
built from: the **SYS-TC lane** (each shipped scenario's user-voice description in the
test itself, readable via `tools/reconcile/register.py`), and `paths.archive`, whose
snapshots of the slice, sprint contracts, and drained capabilities hold the full
statements and the design narrative at drain time. If a compliance mandate ever fires
this trigger, the trace is built from those two — nothing else exists.

**Trigger to act:** when a project with a real audit/traceability mandate adopts wf2
(the user works in such industries and expects to need it — but no current run does).

---

## C11 — Product description (external, customer-voice durable artifact)

**Date:** 2026-06-21
**Context:** a sellable / proprietary product (SaaS or otherwise) needs an external
description — you cannot tell customers "read the code." None of wf2's existing
durable artifacts fills this: a **capability** is open *future* intent in internal
voice that graduates out; the **discover brief** is a code-derived *technical*
component map; an **ADR** is an internal decision. A product description is what the
product *does today*, in *customer* voice, kept durably.

**Analysis (governor lens):** legitimate to store. Positioning, narrative, audience
framing, naming, and what-to-emphasize are **pure human intent — non-derivable from
code**; the derivable half (the raw feature inventory) is the cheap part. It is a
*marketing* artifact, so its feature-drift maintenance is inherent and accepted — NOT
the technical-structure rot wf2 exists to kill (you *want* editorial control over how
the product is described).

It also **completes the graduation model.** A graduating capability deposits residue
into test-tags (component why) + ADRs (decisions) — but neither carries the
*customer-voice "what a user can now do"* essence, which otherwise evaporates on
graduation. The product description is exactly that essence's durable home:
**capability graduates → its customer-facing essence updates the product description →
the capability drops.** Not a bolt-on; it closes a gap the open-work-set model leaves.

**Shape when built — keep minimal (resist the wf1 trap):** a single human-owned
`PRODUCT.md`-style doc with a tight schema, updated at capability-graduation, NOT
auto-generated and NOT a marketing CMS (no pricing / persona / competitive-positioning
sprawl). The moment it grows sections nobody downstream consumes, it has become wf1.

**Trigger to act:** the first time a real product built on wf2 needs an external /
sellable description (a dogfood where someone reaches for "what do we tell
customers"). **C10 (compliance trace)** is its natural second-step extension.

---

## C13 — pin a dependency's *existing* interface in the task contract

**Date:** 2026-06-21 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** the new-seam half shipped and survives the redesign — the slice's
`## Interface contracts` section shapes a seam whose two sides are built in different
increments, because source cannot answer it at TL time. The residual is the
**existing-interface** case: a wiring task consuming a component an earlier increment
already merged, or one already in the tree. The reworked contract has no
`interface_contract` field (`sprint check` B9 rejects it); the only vehicles are
`grounding` pointers — "pointers only, no prose restating code" — and
`dependency_commits`, which hand the build agent a sha to read rather than the shape
itself. The mitigation that did land: wf-tl is dispatched JIT per increment, so the
dependency's real signature exists in the merged tree when the contract is cut.

**Likely shape when acted on:** a cut-time rule that every wiring/integration task's
`grounding` names the dependency's interface symbol as a resolvable pointer (C11 then
verifies it) — *not* a verbatim quote: restating code in the contract is exactly the
multi-representation drift the four-section contract exists to kill.

**Trigger to act:** a driver sprint where a wiring task is rejected in review, or raises
a contract defect, for a guessed integration shape.

---

## C17 — A `wf-research` agent for external-standard grounding (symmetric to wf-drill)

**Date:** 2026-06-22
**Context:** Cluster 8 gave wf-po an external-research affordance as a *nudge* — when a
capability must conform to an external standard the brief and user cannot settle (IFC in the
dogfood; equally a regulation, an API spec, an accessibility standard), the PO dispatches its
harness's research/web capability and treats the result as input. This leans on a host
capability rather than a wf-defined agent.

**Analysis:** the principled answer is a `wf-research` agent symmetric to `wf-drill` —
`wf-drill` is the *internal* scout (repo), `wf-research` would be the *external* scout
(domain/standard); both read-only, both depositing a transient digest the caller treats as
input. It would make external grounding **harness-portable** (wf-defined, rendered per
harness) instead of depending on whatever research tool the host happens to expose, and it
would give other upstream roles (a future SA grounding a constraint against a standard) the
same affordance. Building it now is machinery on a single data point (one IFC case) — the
dogfood law says wait. The nudge is sufficient while the host's general research capability
covers it.

**Trigger to act:** external-standard grounding recurs across dogfoods (a second or third run
reaches for it), or a harness without a usable research capability makes the nudge
unreliable. Then define `wf-research` (read-only, transient digest, per-harness render) and
point wf-po — and any other upstream role that grounds against external standards — at it
instead of the host capability.

---

## C20 — decomposition heuristic: factor out shared composition roots (dogfood-1 F-3)

**Date:** 2026-07-09 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** dogfood run 1 (dems): every endpoint task carried the wiring of the same
~600-line composition root, so four otherwise-parallel tasks were serialized (T13→T14→T15→T16)
to avoid worktree-merge conflicts — defeating the parallelism the build stage exists for.
The collision survives one layer down in the new loop: the designer allocates components
per increment, wf-tl's `depends_on` decides the sub-layers, and a sub-layer's tasks run in
parallel worktrees and batch-merge — so N tasks all editing one registration file either
collapse into a serial chain of sub-layers or conflict at the merge.

**Likely shape when built:** a designer/TL decomposition heuristic — when an increment's
allocation routes many tasks through one shared file, give the shared root its own task (or
stub the registration seam) so the leaf tasks' write sets are disjoint. Pilot-then-fleet
covers the structurally-identical case; this is the shared-sink case.

**Trigger to act:** a driver sprint where an increment's sub-layers collapse to a serial
chain, or a batch merge conflicts, on one shared composition root.

---

## C21 — Mechanical check: every dispatched role left a telemetry row

**Date:** 2026-07-09 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** dogfood run 1 lost telemetry two ways — build/review appends resolved the
relative sink against the worktree cwd and died with the worktree (fixed 2026-07-09:
`record_session.py` now anchors a relative sink to the main checkout root), and nothing
noticed the loss until a manual audit two days later. The fix removes the known loss
vector; the *detection* gap is what remains. The redesign supplies most of what a detector
needs: the driver appends a `kind: driver_event` row per dispatch/routing/stop, and the
Claude usage hook appends a `kind: usage` row per session — so "this role was dispatched
and left no session row" is now an exact join rather than a fuzzy one. Nobody performs
that join during a run.

**Likely shape when built:** after a dispatch returns, the driver compares the sink against
the dispatch event it just wrote and warns when the role's own row never appeared — a cheap
read in the driver, not a new subsystem.

**Trigger to act:** a role's telemetry goes missing in a driver run (surfaced offline when
the retrospective's dispatch↔session join comes up short).

---

## C22 — `wf-qa`: exploratory web-app QA role (system-altitude, on the running app)

**Date:** 2026-07-10 (re-anchored 2026-07-31 for the continuous-loop redesign)
**Context:** dogfood run 1 — hand-browsing the dems web app found bugs the e2e lane
missed (scripted SYS-TC paths verify the promised flows; nobody *explores*). The gap is a
verification peer of wf-review (judgement on the diff) and the SYS-TC lane (scripted
end-to-end): judgement on the **running app**. Exploratory noticing is genuinely
LLM-shaped work, so mechanical-over-LLM does not bar it — the discipline is that findings
exit structured, not as prose reports. The immediate need is covered without wf machinery:
`anthropics/skills@webapp-testing` (official, Playwright-driven browse/screenshot/inspect
loop) is installed in dems (`.agents/skills/webapp-testing`) for ad-hoc sprint-close QA.

**Shape when built:** a `wf-qa` entry in the `closeout` list, dispatched after the last
increment boundary's heavy checks are green — loads the webapp-testing skill, exercises the
served capabilities' user-visible flows plus free exploration, and routes every finding as
a design issue (`component_defect` for defects in merged code — the repair ladder handles
that kind end-to-end) or as a capability-voice need for the next PO session. Read-only
outside the browser; its report is transient.

**Trigger to act:** the first ad-hoc dems QA run with the installed skill proves the shape
(app start/auth needs, what "browse the flows" resolves to, where findings route) — promote
once it demonstrably catches what the e2e lane missed, and fold what the run taught into the
role definition.

---

## C23 — The build refactor phase never fires (cost without observed benefit)

**Date:** 2026-07-10
**Context:** dogfood run 1 — 28 build sessions each ran the refactor checklist pass; zero
produced a refactor. The user kept it as-is for now (one run is one data point), but the
pass is a per-task tax whose yield is so far unobserved.

**Likely shape when acted on:** gate the refactor pass on a green-phase signal (duplication
introduced, contract-forced awkwardness flagged during implementation) instead of running
the checklist unconditionally — or drop it and leave TDD's refactor step to builder
judgement.

**Trigger to act:** dogfood-2 evidence — the token adapter now measures per-session cost, so
the pass's price can be weighed against refactors actually produced; a second zero-yield run
promotes this.

---

## C24 — `human_intervention` telemetry field (autonomy measurement)

**Date:** 2026-07-10
**Context:** declined when `friction_kind` shipped. dems required repeated mid-session human
interventions (manual worktree recreation, hand-edited config, re-running roles) that are
invisible in telemetry — the sessions still ended `completed`. Autonomy is the metric wf2
actually optimizes, and today it is anecdotal.

**Likely shape when built:** a count + reason field in the session feedback (enum'd reason
if patterns emerge), written by the role when the human had to step in mid-session.

**Trigger to act:** autonomy measurement becomes a question actually being asked of the
telemetry (e.g. comparing dogfood runs, or claiming an orchestration change reduced
babysitting).

---

## C25 — Worktree dependency provisioning (the second half of L-004)

**Date:** 2026-07-10 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** dogfood run 1 — task worktrees lacked gitignored dependency dirs
(`node_modules`); preflight never installs them, so the human copied them by hand. The
stale-base half of the recovery shipped 2026-07-10 (`GitOps.add_worktree`). The
judgement-level provision instruction ("run the project's install command, or copy the dir
from the main checkout") lives only in wf-orchestrate's `GIT_OPERATIONS.md` asset, which
retires with that skill — the driver creates worktrees with **no** provisioning step, so
once the loop runs unattended nothing covers this. The **mechanical** twin was deliberately
not built: a blanket copy-any-top-level-gitignored-dir rule is dangerous (`cp -al`
hardlinks let a worktree build corrupt the host copy; a copied Python `.venv` resolves
against the host checkout via embedded absolute paths; multi-GB cache copies recreate the
very stall), and doing it honestly needs a per-project declaration of what to provision.

**Likely shape when built:** a config-declared provision command (e.g. a
`commands.provision` the driver runs in each fresh/recreated worktree), captured by
wf-init from repo evidence like the other commands — not a dir-copy heuristic.

**Trigger to act:** the first driver sprint whose task builds fail or stall on missing
gitignored deps in a fresh worktree.

---

## C27 — build Step-4 gate can exclude the very tests a task's ACs are proven by

**Date:** 2026-07-12
**Context:** dems `sprint-20260711-typed-edge-hardening` (wf-learning L-014). `wf-build`'s
mandated Step-4 gate runs `commands.preflight` (the default preflight). A task whose ACs are
proven by a **higher tier** — dems' migration-tagged replay tests run only under
`commands.stage_check` (preflight all) or `preflight.sh migration` — never executes its own
AC-proving tests at the build gate; the tier only runs later, at the stage boundary. The build
returns green having not run the tests that prove its contract.

**Observation:** the build gate assumes the default preflight tier is a superset of every
task's AC tests. When a task's mandated tests live in a tier the default preflight skips, the
build's own gate is blind to them. The fix is to run the AC-proving tier for the task, not just
the default preflight — e.g. the contract names the test command/tier its mandate needs, and
the build runs that.

**Trigger to act:** a task again passes its build gate while its AC tests sit in an unrun tier
(the stage boundary catches it, at the cost of a late round-trip). Then have the build gate run
the task's mandated tier, sourced from the contract or `commands`.

---

## C29 — wf-discover: let the human pin subsystem groupings

**Date:** 2026-07-12 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** dogfood — the human wanted authority over the subsystem partition the
`wf-discover` agent's scout step reconciles from the three candidate clusterings (folder ·
depgraph · git-cochange). The partition is fully machine-chosen; there is no way to say
"these components are one subsystem" or "split that one" and have discover honor it on the
next run — and the driver now refreshes the brief at **every** sprint start, so a partition
the human dislikes is re-derived every iteration and feeds every design.

**Observation:** an optional human-supplied grouping input discover reads and treats as
authoritative (an override the scout reconciles *around*, not against) — e.g. a committed
`paths.discover_groupings` merged before `paths.discover_subsystems` is written, or seeded
into the scout prompt. Must stay derive-friendly: the override is intent (small, committed,
hand-authored), the partition it shapes stays transient and regenerated each run.

**Trigger to act:** a driver sprint where the machine partition demonstrably misleads the
design role (a slice mis-allocates components along the wrong subsystem seam), or a second
run where a manual regroup would have paid for itself.

---

## C34 — mechanically gate shipped-SYS-TC supersessions in the slice

**Date:** 2026-07-14 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** in the redesigned loop, superseding a *shipped* SYS-TC scenario is escalation-gate
criterion 3: wf-designer halts, writes `paths.decision_prep`, the human rules, resume applies it.
That path is role prose — no mechanism verifies a ruling actually happened. The only mechanical
piece is `complete-sprint`'s survivor sweep (a superseded id still tagged in the test tree), which
runs *after* the sprint built against the supersession. A designer that skips the halt and lists a
shipped id under `## Supersessions` ships unratified, surfacing only in PR review.
**Observation:** a slice-check rule could cross the Supersessions section against the shipped
`[SYS-TC:]` tags in the test tree and fail on any shipped id present without a ruling record —
but `decision_prep` is transient (deleted on resume), so a durable ratification record would be
new machinery. Dogfood law: wait for a run to prove the escalation gate alone is insufficient.
**Trigger to act:** a driver sprint ships a shipped-SYS-TC supersession without a halt (visible
in PR review or the retro's decision log), or PR-review reversals cite an unratified supersession.

---

## C35 — wf-po: guard against revising a capability whose work is in flight

**Date:** 2026-07-14 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** wf-po may "revise an un-built capability with the user's assent". A capability
drains only on an adequate close-time verdict, so an open capability may be mid-build right
now — and the continuous loop makes that the *normal* case: the driver runs unattended while
the human opens a PO session, and several sprints may be stacked unmerged. wf-po reads only
`paths.capabilities`; there is no backlog left to grep, so neither the agent nor the user can
tell that a live sprint serves the id being rewritten.

**Observation:** give wf-po a read-only binding to the in-flight signal that does exist — the
open slice's `serves:` header (`paths.design_slice`), plus the driver state's sprint id — and
gate the revise rule on it: a hit means the work is underway, so name that to the user and get
explicit assent to changing work in flight, or add a new capability instead.

**Failure it prevents:** wf-po rewrites CAP-9 while an increment builds tasks carrying
`covers: [CAP-9]`. The close-time adequacy question is then asked against intent nobody agreed
to, and nothing detects it — the id is still live.

**Trigger to act:** the first PO session that revises (not adds) a capability while a driver
sprint is in flight. Note this hazard was **created** by our own drain model, not inherited —
if it fires, it fires on us.

---

## C36 — `wf sprint check` passes a sprint it never checked against a slice

**Date:** 2026-07-14 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** the driver gates every increment's contracts on `wf sprint check`
(`_contracts_green` in `tools/driver/increments.py`) and re-runs the TL/repair loop while it
is red. But `sprint.py`'s verdict is `"fail" if errors or (args.strict and warns)`, and a
**missing design-slice is `warn("A0")`** ("slice not found; ran contract checks only"), not
an error. So a check that could not run the slice→sprint family — C15 (every task traces to
a declared increment) and A2 (every slice scenario has an e2e task carrying it) — still
returns `verdict: pass`, and the driver proceeds into the sub-layer loop.

**Observation:** A0 is the one warning that means "I could not perform the check you asked
for". Either make it an error, or give the verdict a third state the driver routes on
(`pass` / `unverified` / `fail`). `--strict` is NOT the fix — it would also promote A2, which
is deliberately a warning while the cumulative sprint file is still filling increment by
increment.

**Why deferred:** hard to reach — the design role writes the slice before any increment is
decomposed, and `complete-sprint` drains slice and sprint together. No run has produced a
sprint-without-slice state.

**Trigger to act:** any run where `sprint check` reports A0, or a second consumer starts
routing on its verdict.

---

## C37 — skill prose is the one wf2 surface with no mechanical check, and it is where the bugs are

**Date:** 2026-07-14
**Context:** the slice-rejection change (this same day) took **five adversarial review passes, and
every pass found a critical defect**. All of them lived in **skill prose**, none was caught by a
test, and 284 tests were green throughout — including while the feature was dead code on its main
path. The Python was clean from pass 3 onward; its tests have teeth (mutation-verified). The prose
had eleven files of load-bearing instructions verified only by careful reading, and the defect rate
never dropped. Representative kills: §1a unreachable because wf-tl leaves a failed `$SPRINT` on
disk; the drain ordered so it never drains; fix mode's "commit nothing" colliding with the sprint
branch's clean-tree gate (the loop's *success* path); a skill claiming `wf slice check` closes a
design issue, which it has never done.

This contradicts wf2's own governor — *anything a script can verify, a script verifies* — on the
surface where wf2 spends most of its correctness budget.

**Observation, in two halves. Be honest about which is which:**

*The cheap half — a `wf skills check` linter.* Mechanically decidable today: every `$TOKEN` a
SKILL/reference uses is declared in that skill's path list; every `paths.X` named exists in the
config template; every `assets/X` and `references/X.md` referenced exists; `description` ≤ 280
chars; every cross-file phase/step reference ("Phase 6 steps 5–6") resolves to a step that exists.
**Of this change's ~8 serious defects it would have caught exactly one** (wf-sa was told to read
`$DESIGN_ISSUES` with no binding for it). Worth building — it is small, TDD-able, and forever — but
it does not touch the class that actually hurt.

*The expensive half — the semantic-contradiction class.* Prose contradicting prose across files,
or prose asserting behaviour a tool does not have. Seven of eight defects. No linter decides these.
What demonstrably DID find them: an adversarial agent told to **trace flows end-to-end naming
file:line at each hop**, and to **verify every tool claim against the tool's source**. That is a
role, not a script — a wf-review for the spec layer, or a standing "trace the state machine" step
before any multi-skill change lands.

**Trigger to act:** the next change touching 3+ skills, or the next dogfood run that halts on a
skill instruction that was wrong rather than a defect it correctly caught. The linter half can be
built any time `tools/cli/` is open. The role half needs a decision about whether wf2 gets a spec-layer
reviewer — cf. the standing rule that if SA output needs review, you introduce a reviewer role rather
than bolting checks onto the next consumer. That rule was written about the slice; it applies to the
skills themselves.

**2026-07-24 — the PILOT-SIDE deployment of the role half SHIPPED as `wf-adequacy`**
(spec-layer-redesign; see `doc/notes/spec-layer-redesign-20260724.md`). Trigger evidence: dems
drained CAP-023 four times with every covering SYS-TC present and passing — each scenario proved
the design's decomposition, not the capability's promise; only adversarial source-grounded drills
found the residuals. `wf-adequacy` is that drill as a standing role, dispatched by wf-sa at the
capability-drain gate and at design validation. **Still open here:** (a) the `wf skills check`
linter half, unchanged; (b) the **wf2-side deployment** — an adversarial reviewer for wf2's own
skill prose (this file's original evidence) is still manual practice, not a role. Do not mark C37
resolved on the pilot-side ship alone.

---

## C38 — Cut-time completeness: two heuristic members with no home

**Date:** 2026-07-20 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** the contract-authoring layer is the most recurrent wf-toolkit friction family — a
contract is cut that is incomplete, internally contradictory, or unfaithful to source, and the
build agent recovers it a review-reject or halt cycle late. The source-claim half shipped
2026-07-22 and survives: a claim about a write/read surface, a mechanism, a fixture's reach, or
a command's environment must be resolved to source at cut time and cited as `<path>:<symbol>`,
and `sprint check` C11 re-resolves every `grounding` pointer and errors when the symbol is not
there. The redesign also removed two of this family's historical habitats — the four-section
contract deletes `implementation_notes` and the per-task `interface_contract` (B9 rejects both),
leaving one home per fact.

**The residual — two members C11 still cannot reach:**
- a **bare qualified type claim** carries no path to resolve against, so C11 is blind to it. Its
  surviving surfaces are the slice's `## Interface contracts` section and a contract's
  `boundaries` (fixed interfaces). Recurred four times as `interface_contract` type names that
  exist nowhere (`domain.RuleConflict` 2026-07-20, `domain.AttachmentTarget` 2026-07-27).
- a **compound acceptance criterion** whose `tests[]` prove only one of its branches (L-050) —
  now sharper, because the AC *is* the requirement: nothing else states the behaviour, so an
  unproven branch has no second representation to catch it.

**Analysis:** both need a heuristic over prose (which qualified symbols are type claims; which
`and` joins two branches rather than one condition), and a cut gate that cries wolf gets ignored.
A task legitimately *creates* the type or branch it names, so neither check can fire on absence
alone; each would need an "is this the task's own to write?" test the way C11 got one from the
grounding pointer's path.

**Trigger fired 2026-07-27, and the recommendation stands:** four point-fixes in one dems pass,
none of which would have caught the one member that had no stateable rule. **Act on C37's
wf2-side half rather than a fifth point-fix** — an adversarial spec-layer reviewer subsumes both
residual members without needing a heuristic that can tell "the task creates this type" from
"this type does not exist." Cf. **C37**, **C13** (pinning a dependency's existing interface).

---

## C39 — Tree-derivation scans must prune the transient tree

**Date:** 2026-08-06
**Context:** dogfood run 2 (first parallel driver run). `wf slice check`'s `adr_index`
did an `rglob("ADR-*.md")` over the whole repo with a `_SKIP_DIRS` set that deliberately
does **not** skip `.wf` (the canonical ADR home is `.wf/adrs`). It therefore walked into
`.wf/transient/worktrees/`, where each per-task worktree is a **whole checkout carrying
its own copy of every ADR** — so every cited ADR came back defined in three "ADR sets"
and A5-collided with itself. On a resume with two live worktrees the driver halted with
`slice_check_red: the slice on disk no longer passes its gate`, on a slice that had been
green at sprint start. Only reachable once worktrees can be alive at slice-check time —
i.e. the continuous loop's resume path, which is why it never surfaced before.
Fixed for `adr_index` (prunes `paths.transient`).

**The general shape:** any derivation that walks the repo tree is now walking N+1 copies
of it whenever tasks are in flight. Audited at fix time: `impact.py`'s two scans skip
`.wf` wholesale (safe); `pipeline.py`/`adequacy.py` glob a named cache dir, not the tree.
The one remaining exposure is `impact.py:89`, which globs **config-authored** `add`
patterns against the repo root — a rule written as `**/*.yaml` would match inside every
live worktree.

**Trigger to act:** an `impact` rule is authored with a `**` pattern, or any new
tree-walking derivation is added. The fix is the same three lines each time; the real
candidate is whether tree-walking derivations should share one pruned walker instead of
each re-deriving the skip set.

## C41 — nothing stops two drivers from running on one repo

**Date:** 2026-08-07
**Context:** a second `wf-driver` was started against dems while one was already ten
minutes into a sub-layer. Neither noticed the other. The damage was immediate and
entirely mechanical:

- the second driver's `resume_hygiene` → `pipeline reclaim-stale` reset the first
  driver's two **live** dispatches to `pending` — the verb cannot tell a slot held by a
  running agent from one orphaned by a dead run, because "was the launcher still alive?"
  is not a question the run state can answer;
- it then re-dispatched both tasks, and `worktree_add` — correctly, by its own rule —
  found the base branch was no longer an ancestor of the worktree HEADs and **deleted and
  recreated both worktrees underneath the first driver's running agents**;
- the first driver's build then returned `escalate_no_artifacts` (its worktree had
  vanished mid-run) and spent one of its two redispatches recovering from a cause that
  had nothing to do with the build.

Every step behaved exactly as designed. The composition is what fails, and it fails
silently: no error, no warning, and a run state that stays internally consistent
throughout — so nothing downstream can detect that it happened.

**Why it is not merely operator error:** the driver is explicitly resumable by re-running
the same command, and the position lives on disk precisely so a human can restart it
after a halt. "Re-run to resume" and "never run twice" are contradictory instructions to
hold in a human's head, and the second one is enforced nowhere. The recovery cost is real
but bounded (a redispatch and some wasted agent time); the *diagnostic* cost is not — the
symptoms (a build that wrote nothing, a task retried for no visible reason) are
indistinguishable from the failures the loop is built to absorb.

**Shape of the fix:** a pidfile under `paths.transient` written at startup and removed on
exit, with a liveness check on a stale one (the pid is gone, or is not a driver) so a
crashed run does not lock the repo forever. Refuse to start with a message naming the
running pid. Roughly the same size as the other startup gates in `loop.py`, and it
belongs beside `verify_position` — both answer "is the world what this run assumes?".

**Trigger to act:** it happens once more, or any run is unattended long enough that a
human could reasonably forget one is going. Recurrence so far: **1** (2026-08-07).

**Detected independently, from inside.** dems `wf-learnings.yaml` L-119 is a build session
reporting the same incident with no knowledge of its cause: *"Mid-build, HEAD advanced to
an unrelated commit and the session's own uncommitted changes vanished from the worktree —
`ps` showed a live wf-driver plus a claude -p dispatch."* It cost that session a `ps`
investigation to reach a conclusion the driver could have stated at startup, which is the
diagnostic cost above, priced. A second, milder shape of the same thing lands whenever a
human commits to the sprint branch mid-run: `worktree_add` then destroys the live
worktrees for the same reason. The pidfile does not cover that one.
