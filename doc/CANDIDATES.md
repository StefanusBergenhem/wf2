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

## C15 — The "test tree" is a path with ≥3 callers but no config key

**Date:** 2026-06-21
**Context:** wf-sa invokes tools that need the project's test tree as a `--scan` /
`--tests` root — `reconcile.py` (drain the built), `next_id.py` (allocate ids), and since
2026-07-10 `register.py` (the what's-already-promised read). None resolves the root from
config; all say "the test tree" and rely on SA judgement to fill the path.

**Observation:** this is the C1 threshold (a thing read by ≥2 callers belongs in one source of
truth) applied to a **path** rather than a config *reader*. The "one source of truth for paths"
ground rule says skills resolve paths from `.wf/config.yaml`; "the test tree" is the one path in
wf-sa resolved by judgement instead. A `paths.tests` key would fix it — **but a single root does
not fit polyglot / co-located layouts** (Go's `_test.go` lives beside source, so the "test tree"
is effectively the source root; a TS repo may scatter `__tests__`). So the key may need to be a
*list* of roots, or the convention may be "the source root" for grep-only consumers. Needs a
decision, not a reflex `paths.tests: ".wf/..."`.

**Update 2026-07-10 — trigger fired:** `register.py` in wf-sa Phase 1 is the third consumer.
Open decision: single root vs list vs "source root" convention (all three consumers are
grep-only scanners, so a repo-root default may be sufficient — at the cost of scanning
vendored trees). Promote at the next config touch.

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

## C18 — A mechanical scope-compliance check (`git diff --name-only` ⊆ `files_to_touch`)

**Date:** 2026-06-22
**Context:** `wf-review` was reduced to a pure **judgement** gate — it re-runs no mechanical
command (the build already ran `commands.preflight` green to hand off; the stage boundary
re-runs the heavy checks). The one check that straddles the line is **scope**: "did the diff
stay inside `files_to_touch`?" Review does it now by reading `git diff --name-only` against
the contract's list — cheap, but still a human-in-the-loop judgement that a mechanical check
could make deterministic. This also stands in for wf1's dropped architecture-compliance gate
(no durable DESIGN to check component ownership / dependency_rules against), so a changed file
outside `files_to_touch` is the only structural boundary review still enforces.

**Observation:** set-membership of changed files in a declared list is purely mechanical —
exactly the kind of check the toolkit prefers to do in a script, not an LLM. A
`wf check scope <worktree> <task-id>` verb (diff names vs the contract's `files_to_touch`,
exit non-zero + the offending paths) would turn the one remaining structural review check into
a deterministic verdict the build/review boundary could route on, freeing the reviewer to spend
its judgement only on what genuinely needs reading.

**Update 2026-07-10:** the *author-time* half shipped — `wf sprint check` now errors when a
testing mandate has no test-file home in `files_to_touch` (dems' #1 build-halt cause) and
warns when `implementation_notes` name an out-of-scope file, so the contracts that forced
mid-build scope amendments are caught at cut time. The *runtime* diff⊆contract check this
entry proposes remains open.

**Trigger to act:** a scope violation slips past review judgement, or a dogfood wants the
files_to_touch boundary enforced deterministically (e.g. a mechanical pre-review gate, or
folding it into the build-return inspection). Then add the check as a CLI verb and have review
cite it instead of eyeballing `git diff --name-only`. Until then the judgement read is cheap and
sufficient.

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
