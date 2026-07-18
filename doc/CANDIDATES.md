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

**Date:** 2026-06-20
**Context:** `wf-retrospective` ships as the **dogfoodable slice**: read the telemetry
session log, distil `repo_observation` → `paths.learnings` (project learnings wf-sa
reads as drivers) and `wf_friction` → `paths.wf_learnings` (toolkit friction), dedup by
the `sources` provenance set, create-only `open` entries. It runs against the telemetry
PO/SA/SWA/drill already write — no orchestration needed.

**Built since (2026-06-22):** the sprint-execution analysis now lands as **cross-task pattern
detection** — `wf-retrospective` reads `pipeline_state` (recurring rejections, design-issue
clusters by `fix_kind`, escalation/block causes) and distils the patterns no single session
can see into the same learnings streams, sourced by `sprint_id`. Per-task velocity/counts are
reported transiently, not stored. **2026-07-10:** feedback gained a `friction_kind` enum, so
the friction clustering step is now a mechanical groupby before judgement. **Still
deferred:** the maintained `MEMORY.yaml` lessons store (dedup, capacity-cap, confidence,
reinforcement) — wf1's governor-ish overreach, and nothing in wf2 consumes a
distilled-lessons store.

**Also deferred — `handled` is an optimistic close.** wf-sa flips a learning to
`handled` when it *designs* the fix, not when build *lands* it; nothing downstream
confirms the code shipped. Recoverable — an abandoned fix's smell re-surfaces in a later
observation and re-distils. When the `[REQ]`-style coverage harvester exists, `handled`
can become *derived from commit citations* instead of a stored flag — the same move
made for superseded requirements (retired ids verified gone via reconcile, 2026-07-10).

**Trigger to act:** the orchestration half is done. Build the `MEMORY.yaml` store only if a
real consumer for a maintained lessons store appears (none today — the open learnings streams
suffice). When the coverage harvest is wired into build/review, switch `handled` to derived
(above).

---

## C10 — Compliance / audit trace (capability → requirement → test walk)

**Date:** 2026-06-21
**Context:** the **capabilities-as-open-work-set** reframe (2026-06-21 — completed
capabilities graduate OUT rather than accumulate as a durable catalog) was adopted
after establishing that nothing reads a *completed* capability. The one honest
exception identified: a **walked compliance/audit trace** (test → requirement →
capability → user-need), which regulated industries genuinely require.

**Analysis:** the *requirement-level* trace already survives — every requirement's
EARS text lives in its `[REQ]` test tag, harvestable on demand, so test → requirement
is intact without retaining capabilities. What a compliance trace adds on top is the
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

**Update 2026-07-09:** the requirement-level readable view now exists —
`tools/reconcile/register.py` derives a markdown register (REQ/SYS-TC id, statement,
proving tests) from the tags on demand (and since 2026-07-10 wf-sa consumes it as its
what's-already-promised input). Still missing for a full compliance trace: the
capability → user-need apex, which graduation drops.

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

## C13 — `parent_interface`: verbatim interface contract in the task contract

**Date:** 2026-06-21
**Context:** Cluster 4's A1 allocates per-component requirements across a delivery path
(core logic → orchestration → composition-root wiring), coordinated by task `depends_on`.
A wiring/orchestration task needs the **exact interface** of the component it wires in. wf1
solved this by quoting the dependency component's exposed interface DbC **verbatim** into
the dependent task's contract (`parent_interface`), so the developer never guessed the
integration shape — the contract pinned it.

**Analysis:** relevant precision for A1's wiring tasks, but it depends on an `exposes:`
interface-with-DbC declaration per component (wf1 carried this in the durable DESIGN —
which wf2 killed). In wf2 the interface is re-derived (discover) or scouted (wf-drill), so
the verbatim-quote would be sourced differently. Premature until the task-contract authoring
(wf-swa → build) is the actual bottleneck and the interface source is settled.

**Update 2026-07-09:** the **new/widened-seam half shipped** — the design-slice carries
an `## Interface contracts` section (SA-authored, for components/seams with no source to
read yet) and the task contract an optional `interface_contract` field copied verbatim from
it. **Update 2026-07-10:** the *detection* side also tightened — dems T16 wired the wrong
persistence seam past review, and wf-review now verifies a contract-mandated seam against
the implementation's wiring, not just a passing assertion. What this entry still covers is
the **existing-interface** case: quoting a dependency component's *current* interface
(sourced from discover/drill) into a wiring task, so the builder never guesses it.

**Trigger to act:** when wiring/integration tasks start failing review for guessed
interface shapes — add a `parent_interface`-style verbatim quote to the task contract,
sourced from discover/drill rather than a durable DESIGN.

---

## C14 — In-flight tracking on the design backlog (concurrency)

**Date:** 2026-06-21
**Context:** the drain-pipeline model has wf-sa cut a design-slice from the committed design
backlog, and remove built requirements from the backlog when reconcile confirms them. The
current model assumes the flow is **human-controlled and strictly sequential across roles**
(SA → SWA → build, one increment at a time), so nothing marks which backlog entries are
already cut into a live slice/sprint and *building*. wf-sa's "don't re-cut an in-flight
entry" is enforced only by the operator running one increment at a time.

**Analysis:** under concurrency (parallel increments, or an orchestrator dispatching
several), wf-sa could re-cut a design that is already mid-build → double-build. The backlog
would then need explicit per-design **in-flight** state (e.g. `cut` / `building` markers, or
a derived check against live sprints), and wf-sa would skip in-flight entries when cutting.
Building it now is machinery for an absent concurrency model (dogfood law) — the sequential
human-driven flow has no race.

**Trigger to act:** when the flow stops being strictly sequential — a real run with parallel
increments, or the orchestrator dispatching more than one slice's work at once. Then add
in-flight tracking to the backlog and an "skip in-flight" rule to wf-sa's cut step.

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

## C20 — SA decomposition heuristic: factor out shared composition roots (dogfood-1 F-3)

**Date:** 2026-07-09
**Context:** dogfood run 1 (dems): the SA folded `cmd/server` route+adapter wiring into
**each** endpoint task's acceptance criteria. Every handler task edited the same ~600-line
composition root, so the SWA serialized four otherwise-parallel tasks (T13→T14→T15→T16) to
avoid worktree-merge conflicts — defeating the worktree parallelism the build stage exists
for. Derived, not directly reported: the surface observation was a `repo_observation`, the
lever is the SA's decomposition pattern.

**Likely shape when built:** an SA design-heuristic (and/or a wf-swa decomposition note):
when many parallel tasks route through one shared file, factor the shared composition root
into its own task (or stub the registration seam) so leaf tasks stay independent — the
`files_to_touch` sets partition.

**Trigger to act:** a second run reproduces the serialization (the dogfood-1 report's own
threshold — one observation is an inference, not a pattern).

---

## C21 — Mechanical check: every dispatched role left a telemetry row

**Date:** 2026-07-09
**Context:** dogfood run 1 lost telemetry two ways — build/review appends resolved the
relative sink against the worktree cwd and died with the worktree (fixed 2026-07-09:
`record_session.py` now anchors a relative sink to the main checkout root), and nothing
noticed the loss until a manual audit two days later. The fix removes the known loss
vector; what remains unguarded is the *detection* gap — a silently skipped or misrouted
telemetry write is invisible until someone reads the sink. (Since 2026-07-10 the Claude
usage hook appends an independent `kind: "usage"` row per session, so a session that ran
but wrote no skill-row is now visible offline by comparing the two row kinds — a partial,
maintainer-side mitigation, not an in-run check.)

**Likely shape when built:** the orchestrator records the sink's line count at
`wf pipeline dispatch` and the return inspectors warn when it did not grow — a cheap
baseline-compare in pipeline state, not a new subsystem.

**Trigger to act:** a role's telemetry goes missing again *after* the root-anchor fix.
Building the checker before a second loss mode shows up is machinery without evidence.

---

## C22 — `wf-qa`: exploratory web-app QA role (system-altitude, on the running app)

**Date:** 2026-07-10
**Context:** dogfood run 1 — hand-browsing the dems web app found bugs the e2e lane
missed (scripted SYS-TC paths verify the promised flows; nobody *explores*). The gap is a
verification peer of wf-review (judgement on the diff) and the SYS-TC lane (scripted
end-to-end): judgement on the **running app**. Exploratory noticing is genuinely
LLM-shaped work, so mechanical-over-LLM does not bar it — the discipline is that findings
exit structured, not as prose reports. The immediate need is covered without wf machinery:
`anthropics/skills@webapp-testing` (official, Playwright-driven browse/screenshot/inspect
loop) is installed in dems (`.agents/skills/webapp-testing`) for ad-hoc sprint-close QA.

**Shape when built:** a `wf-qa` role dispatched at `end_of_sprint` after the stage checks
run green — loads the webapp-testing skill, exercises the capabilities' user-visible flows
plus free exploration, and routes every finding as a design issue (`component_defect` for
defects in merged code — the fix loop now handles that kind end-to-end) or hands the PO a
capability-voice need. Read-only outside the browser; its report is transient.

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

**Date:** 2026-07-10
**Context:** dogfood run 1 — task worktrees lacked gitignored dependency dirs
(`node_modules`); preflight never installs them, so the human copied them by hand. The
stale-base half of the recovery shipped 2026-07-10 in both tracks (`GitOps.add_worktree`
+ GIT_OPERATIONS.md § Worktree); the LLM track also carries a judgement-level provision
instruction ("run the project's install command, or copy the dir from the main
checkout"). The **mechanical** twin was deliberately not built: a blanket
copy-any-top-level-gitignored-dir rule is dangerous (`cp -al` hardlinks let a worktree
build corrupt the host copy; a copied Python `.venv` resolves against the host checkout
via embedded absolute paths; multi-GB cache copies recreate the very stall), and doing
it honestly needs a per-project declaration of what to provision and how.

**Likely shape when built:** a config-declared provision command (e.g. a
`commands.provision` the driver runs in each fresh/recreated worktree), captured by
wf-init from repo evidence like the other commands — not a dir-copy heuristic.

**Trigger to act:** a second run loses time to missing worktree deps, showing which shape
(install command vs copy) real runs need. Until then the LLM-track instruction covers it.

---

## C26 — wf-swa: SYS-TC `depends_on` resolution for a learnings-driven slice

**Date:** 2026-07-12
**Context:** dems `sprint-20260711-typed-edge-hardening` (wf-learning L-013). `default-mode.md`
Phase 3 / `task-contract.md` resolve a SYS-TC's `depends_on` as "the tasks building the
requirements **driven by that capability**" — a CAP → REQ link read off the slice. A slice
with **no new capabilities** (its requirements serve `L-NNN`, not `CAP-NNN`) has no such link,
so a SYS-TC that `Covers CAP-NNN` cannot be mechanically resolved to its building tasks. The
SwA fell back to mapping by "the tasks that assemble the path the case exercises."

**Observation:** the CAP-driver resolution rule assumes every slice introduces capabilities.
A learnings-driven slice (bug-hardening, refactors) breaks that assumption. Either the SA must
attach a driver the SYS-TC can resolve against, or `default-mode.md` needs an explicit
"no-capability-driver" resolution rule (map the case to the tasks that build the path it
exercises).

**Trigger to act:** a second learnings-driven slice reaches the SwA and the SYS-TC
`depends_on` is again resolved by hand. Then add the fallback rule to `default-mode.md`
(and/or have wf-sa carry a resolvable driver on a learnings-only SYS-TC).

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

**Date:** 2026-07-12
**Context:** dogfood — the human wanted authority over the subsystem partition wf-scout
reconciles from the three candidate clusterings (folder · depgraph · git-cochange). Today the
partition is fully machine-chosen; there is no way to say "these components are one subsystem"
or "split that one" and have discover honor it on the next run.

**Observation:** an optional human-supplied grouping input discover reads and treats as
authoritative (an override the scout reconciles *around*, not against) — e.g. a committed
`paths.discover_groupings` the scout merges before writing `subsystems.json`, or seeds the
scout prompt with. Must stay derive-friendly: the override is intent (small, committed,
hand-authored), the partition it shapes stays transient and regenerated each run.

**Trigger to act:** a second run where the machine partition actively obstructs planning and a
manual regroup would have paid for itself. Until then the three-clustering scout output is
enough.

---

## C31 — driver: autonomously resolve a stage-fix design issue (not just record + escalate)

**Date:** 2026-07-13
**Context:** the wf-orchestrate SKILL now resolves a heavy-check (`stage_check`) design issue
in-line via §2b (dispatch-fix → wf-sa/wf-swa → re-cut the stage-repair). The live driver
(`_stage_fix_cycle`) only records the DI canonically (host file + state) and then escalates
the boundary — it does not run dispatch-fix + re-loop. The manual (skill) path is what
dogfood runs exercise, so the driver's escalate-after-record is currently sufficient.

**Observation:** bring `_stage_fix_cycle` to full parity — on a stage-fix `design_issue`,
call the existing `_resolve_design_issues` machinery and re-run the heavy check. The snag is
the synthetic `STAGE-FIX-<sprint>` task is not a DAG node, so the `component_defect` branch
(which does `compute-stages --force` and expects the parked task to re-enter a later stage)
has no clean re-entry. Needs a design for how a stage-fix follow-up task re-triggers the
heavy check after it merges.

**Trigger to act:** the first autonomous (SDK-driver) run that hits a stage-fix design issue
— until the driver is actually used for a sprint with heavy checks, the skill path covers it.

---

## C32 — wf-sa Phase 4: dry-run the decomposition before the human ratifies

**Date:** 2026-07-14
**Context:** two dems `/wf-orchestrate` runs on 2026-07-14 (111 minutes apart) both halted in
`preparing` with wf-swa rejecting the slice. The second rejection was on `REQ-141` — a
requirement wf-sa *minted* during the 92-minute interactive re-design that round 1 triggered.
Both rounds failed on the same class of defect: requirement ownership vs. the real atomic edit
set. wf-swa flagged the repeat itself ("same class as the previous cut's B3/B4").

The order of operations is the cause: wf-sa Phase 4 is the interactive core where the human
ratifies, but decomposition — the only step that empirically tests whether the design is
buildable — runs in wf-swa on the *next* orchestrate run, after ratification. So the human
ratifies buildability they cannot assess, and the SA's only real feedback arrives one lap later.

**Observation:** dispatch wf-swa read-only against the draft slice inside Phase 4, before the
human ratifies — "can every requirement be owned by one component, and is each atomic edit set
inside it?" A ~6–17 minute autonomous check gating a 92-minute interactive session.

**Why it is deferred rather than done:** the slice-rejection route (`slice_defect` → wf-sa fix
mode → re-dispatch wf-swa, bounded at `_MAX_REDESIGN_ROUNDS`) now makes each lap autonomous and
cheap, which was the same note's cheaper mitigation. The human-session cost the dry-run exists to
protect only materializes on an ADR / unratified-assumption / capability escalation. Part of the
observed pain also came from the boundaries-are-law vs. atomic-edit-set collision, which is now
resolved — round 2's `SortOrder` blocker would not be a blocker today, so the residual defect rate
is unmeasured.

**Trigger to act:** `dispatch-fix`'s round bound actually fires on a real run, or two consecutive
interactive wf-sa runs produce slices that need a re-cut. Either says the loop is not converging
and the feedback belongs before the ratification gate, not after.

---

## C33 — driver: route a slice rejection, don't just report it

**Date:** 2026-07-14
**Context:** `wf-orchestrate` §1a now resolves a rejected design slice autonomously —
`dispatch-fix` routes the `slice_defect` DI to wf-sa fix mode, which re-cuts the slice, and
the orchestrator re-dispatches wf-swa against it, bounded by `_MAX_REDESIGN_ROUNDS`. The live
driver (`_run`, preparing) only *reports* the rejection: `_no_sprint_reason` reads the open
`slice_defect` and escalates naming the DI, the blocker count, and `/wf-sa`. It does not run
dispatch-fix, does not dispatch wf-sa, and does not re-loop.

**Observation:** bring the driver to parity — the same dispatch-fix → wf-sa → re-dispatch
wf-swa loop, reusing the existing `_resolve_design_issues` machinery for the route and
`dispatch-fix`'s exit 1 for the round bound. Unlike C31's stage-fix case there is no
awkward re-entry: preparing is a straight loop back to the wf-swa dispatch, with no DAG node
to place.

**Trigger to act:** the first autonomous (SDK-driver) run that hits a slice rejection. Both
dems runs that motivated this work used the skill path; until the driver actually drives a
sprint, its honest escalation costs one human `/wf-sa` invocation — the same thing the skill
path did before §1a existed. Cf. **C31** (same driver/skill parity class, one phase later).

---

## C34 — `wf slice check`: gate unratified supersessions, not just assumptions

**Date:** 2026-07-14
**Context:** wf-sa Phase 3 requires every **supersession** (a shipped `REQ-<n>` / `SYS-TC-<n>`
this design invalidates or retires) to be ratified by the human at Phase 4, and
`assets/design-slice.md.tmpl`'s Supersedes section says as much — "alignment before it may
appear here". Nothing enforces it. `wf slice check` (`tools/cli/slice.py`) greps for
`UNCONFIRMED` assumption lines and nothing else, so an unratified supersession reaches wf-swa
silently. Assumptions have a marker and a gate; supersessions have neither.

**Observation:** give the Supersedes section a ratification marker of its own and fail
`slice check` on an unratified entry, exactly as it fails an `UNCONFIRMED` assumption. That
makes the Phase 4 supersession rule mechanical instead of a prose promise, in both modes.

**Why now-ish:** wf-sa fix mode (`slice_defect` path) runs autonomously with no Phase 4, so it
carries a fourth escalation trigger — "resolving a blocker would supersede a shipped
requirement → halt" — written *only because* this gate does not exist. **Delete that trigger
when this candidate ships**; it is a hand-held substitute for a mechanical check. In default
mode the hole is older and softer: a human is present at Phase 4 and sees the supersession
presented, so it takes an SA omission to slip through.

**Trigger to act:** a supersession reaching a sprint unratified (from either mode), or the
next time `slice.py` is opened for other work — the marker + check is small, and it retires
a prose rule in fix-mode.md.

---

## C35 — wf-po: guard against revising a capability whose work is in flight

**Date:** 2026-07-14
**Context:** capabilities now drain at **build**, not at design (wf-sa Phase 1). Under the old
model anything sitting in `$CAPABILITIES` was by definition un-designed, so `wf-po`'s rule —
"you may revise an un-built capability with the user's assent" — was safe by construction.
It no longer is: an un-built capability may be designed, in the backlog, cut into a live
slice, and building right now. wf-po has no `paths.design_backlog` binding, never reads it,
and is explicitly forbidden from surfacing wf-voice to the user, so neither the agent nor the
user can tell. wf-sa got exactly this guard in the same change ("grep `$DESIGN_BACKLOG` for
each id you consider: one a surviving design already serves is in flight"); wf-po did not —
and wf-po is the role that actually rewrites the text.

**Observation:** give wf-po a read-only `DESIGN_BACKLOG` binding and gate the revise rule —
grep the backlog for the id; a hit means the work is underway, so name that to the user and
get explicit assent to changing work in flight, or add a new capability instead.

**Failure it prevents:** wf-po rewrites CAP-9 while a sprint builds requirements traced to
CAP-9's old wording. Every `serves: CAP-9` trace then points at intent nobody agreed to, and
nothing detects it — the requirement still names a live id.

**Trigger to act:** the first wf-po run that revises (not adds) a capability while a sprint is
in flight. Deliberately parked rather than fixed with the drain change: the hazard needs that
exact sequence to bite, and no run has done it. Note this was **created** by the drain change,
not inherited — if it fires, it fires on us.

---

## C36 — `wf sprint check` passes a sprint it never checked against a slice

**Date:** 2026-07-14
**Context:** `wf-orchestrate` §1 step 1 now gates on `wf sprint check` reporting `verdict: pass`
rather than on `$SPRINT`'s presence — presence cannot distinguish a good sprint from one that
failed its own gate. But `sprint.py`'s verdict is `"fail" if errors or (args.strict and warns)`,
and a **missing design-slice is `warn("A0")`** ("slice not found; ran intra-sprint checks only"),
not an error. So `sprint check` against no slice returns `verdict: pass`, and §1 step 1 accepts a
sprint whose entire slice-conformance family (A0/A1) never ran.

**Observation:** A0 is the one warning that means "I could not perform the check you asked for",
which is categorically different from C5's "this is allowed but consider splitting". Either make
A0 an error, or give the verdict a third state the orchestrator can route on (`pass` /
`unverified` / `fail`). `--strict` is NOT the fix — it would also promote C5 (>5 files) to an
error, and an atomic edit set legitimately exceeds 5 files, which is why C5 is a warning.

**Why deferred:** unreachable in normal operation — `complete-sprint` drains the slice and the
sprint together, and a crash between the two self-heals (the phase is still `end_of_sprint`, so
the next run re-runs closeout). No run has produced a stale-sprint-without-slice state.

**Trigger to act:** any run where `sprint check` reports A0, or a second consumer starts routing
on its verdict. Noted because it is the same shape as the bug §1 step 1 was just fixed for — a
check that cannot tell "verified good" from "not verified" — and that shape has now bitten this
pipeline three times in one change.

---

## C37 — skill prose is the one wf2 surface with no mechanical check, and it is where the bugs are

**Date:** 2026-07-14
**Context:** the slice-rejection change (this same day) took **five adversarial review passes, and
every pass found a critical defect**. All of them lived in **skill prose**, none was caught by a
test, and 284 tests were green throughout — including while the feature was dead code on its main
path. The Python was clean from pass 3 onward; its tests have teeth (mutation-verified). The prose
had eleven files of load-bearing instructions verified only by careful reading, and the defect rate
never dropped. Representative kills: §1a unreachable because wf-swa leaves a failed `$SPRINT` on
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
