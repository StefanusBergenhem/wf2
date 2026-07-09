# wf2 — Candidates

Deferred observations about the wf2 toolkit itself: improvements, risks, and
thresholds worth acting on later. Not scheduled work — a parking lot so a
forward-looking note isn't lost. Promote an entry to real work when its trigger
fires; delete it when resolved.

---

## C3 — Telemetry: capture tokens + tool counts via per-harness hooks

**Date:** 2026-06-14
**Context:** Telemetry records time + outcome + structured feedback (skill-written,
harness-agnostic). It does **not** capture token cost or tool-call counts, which
was the originally-intended "main purpose." Three harnesses were researched
(Claude Code, pi, opencode) to find how.

**Finding — capture is irreducibly harness-specific.** Every harness keeps
token/tool data in a per-session transcript/store, **none exposes that store to an
in-session bash command** (so the skill-invoked recorder structurally cannot read
it), and there is no cumulative total anywhere (always aggregate). The native
capture point differs per harness:

- **Claude Code** — `Stop` / `SubagentStop` hook receives `transcript_path` on
  stdin; parse the JSONL, sum `usage.{input,output,cache_read}` tokens, count
  `tool_use` blocks. Subagents get their own `agent_transcript_path`.
- **pi** (earendil-works) — `session_shutdown` extension aggregates in-process
  (`ctx.sessionManager.getEntries()`), **or** post-run parse of the session JSONL
  under `~/.pi/agent/sessions/` (`usage.totalTokens`, `toolCall` blocks) with the
  session pinned via `--session` / `PI_CODING_AGENT_SESSION_DIR`. No in-session env
  var. (Confirm pi identity: earendil Pi vs oh-my-pi — they differ.)
- **opencode** — read the on-disk store (`~/.local/share/opencode`; JSON tree
  pre-1.2, SQLite `opencode.db` 1.2+) keyed by a session id pinned via
  `opencode run --session <id>`; sum `tokens.{input,output,reasoning,cache.*}`,
  count `type:"tool"` parts. `OPENCODE_SESSION_ID` in tool env is unmerged — do not
  rely on it.

Even wf1 never solved this from the skill: its telemetry left token columns
`(hook)` / null, "fill via an optional host Stop hook."

**Recommended shape when built — two layers:** (1) the skill writes the
agnostic record it already does (agent/time/outcome/feedback); (2) a small
**per-harness adapter** (Claude Stop hook · pi `session_shutdown` · opencode
store-reader), installed per target, enriches with tokens+tools. This is the one
genuinely harness-coupled piece of wf2 — keep it isolated in the adapters.

**Trigger to act:** when token cost actually needs measuring (e.g. a dogfood run
where context budget or per-agent cost is the question being asked). Until then its
absence does not hurt — defer. Build the Claude adapter first (the dogfood harness).

---

## C4 — Fix-mode subagents (wf-swa contract_amendment BUILT; wf-sa spec_amendment deferred)

**Date:** 2026-06-14 (updated 2026-06-22)
**Context:** the orchestration layer now ships the **routing half** — `wf orchestrate
dispatch-fix` reads a design issue's `fix_kind` and routes it: `contract_amendment →
wf-swa`, `spec_amendment → wf-sa`, `unknown → human`. What is NOT built is the **fix
MODE** in each subagent: today `wf-swa` / `wf-sa` ship default-mode only. wf1's SWA/SA
had a second mode the orchestrator dispatched to surgically resolve a DI mid-execution
(amend in isolation, commit, flip the DI to resolved; the orchestrator re-dispatches the
original task at the same attempt count). The lifecycle reason SA|SWA stay split (SWA is
the re-dispatched, surgical contract-fixer) still holds; only the mechanism is unbuilt.

**Reconciliation (absorbs the dropped C12 — taxonomy + routing now built):** the
taxonomy is two autonomous kinds plus unknown. wf1's `recut → wf-po` is **dropped** —
wf2's PO owns capabilities, not sprint cutting; SA owns the slice cut, so a needed
re-cut folds into a `spec_amendment` (SA decides the fix scope). The planning-time
"infeasible cut" case is the `preparing` swa→sa escalation, not a stage-execution DI a
task-scoped build/review agent could classify.

**Status (2026-06-22):** the **`wf-swa` contract_amendment fix-mode is BUILT** — agent
wrapper + a mode-split skill (classify → minimum-amend one task contract, no commit since the
sprint is transient → flip the DI to `resolved`; a genuine spec defect halts to the human). It
is on a **live path**: build/review raise `contract_amendment`, the orchestrator routes it to
`wf-swa`. The **`wf-sa` spec_amendment fix-mode — and its agent wrapper — are NOT built and are
deferred.**

**Trigger to act (wf-sa half):** there is **no automated producer of a `spec_amendment` DI
today** — build/review are code-layer and raise `contract_amendment` only, and `wf-swa`
fix-mode escalates a genuine spec defect **to the human** rather than auto-reclassifying (that
auto-route is **C19**). So a `wf-sa` fix-mode would be a consumer with no producer, and `wf-sa`
is never autonomously dispatched anyway (the `preparing` step dispatches only `wf-swa`; on its
escalation the human re-runs `wf-sa`'s default mode). Build the `wf-sa` spec_amendment fix-mode
+ wrapper **together with C19** — the loop change that first produces a `spec_amendment` DI.
Until then a spec defect surfaced at build time is a human escalation: re-run `wf-sa` default
mode to reshape the spec and re-cut. Keep the **mechanical classification check** for when it
lands (spec correct + contract diverges → contract_amendment; contract matches spec + spec
wrong → spec_amendment; else unknown).

---

## C6 — How SA knows which capabilities are in scope this round

**Date:** 2026-06-14
**Context:** `wf-sa` Phase 1 step 1 says *"Read `$CAPABILITIES` and identify the
capabilities this change serves."* That is vague — it does not say how SA learns
which capabilities are new / in-scope for this round vs already handled. Two storage
options were floated: (1) a per-capability status (`new | designed | implemented`),
(2) an `ongoing` + `completed` capabilities file pair.

**Analysis (governor lens):** both stored options are the wrong shape.
- **`implemented`/`done` is derivable** — coverage = `[REQ]` test tags ⟷ capabilities
  set-diff. Storing it stores what code reports (governor violation).
- **`designed` is rot-prone** — the design-slice is ephemeral and *free to
  regenerate*, so a durable "designed" flag has no backing artifact. Not tracking it
  costs nothing: if a capability was designed but not built, SA just re-designs it.
- **`ongoing`/`completed` file pair** is the wf1 sync-tax — a maintained second copy
  of lifecycle with entries shuttled between files by hand. Hard no.
- The only **durable, non-derivable** status is intent: `planned` vs `deferred`
  (already in PO's scaffold).

**Likely resolution when picked up:**
1. "Which capabilities this round" is a **session input** — SA is invoked with a
   change-to-design (a capability id / feature / free-text ask) and resolves it to
   the capability set it serves. SA does not autonomously scan for "what's new."
2. **The backlog is the derived gap** — `planned` capabilities with no proving test
   yet. Computed on demand from the `[REQ]` coverage harvest; no backlog file, no
   per-capability build status. This is consistent with "no backlog tier; the slice
   is the unit of work."
3. **Prune PO's status values to `planned | deferred`** (drop `in_progress` =
   transient, `done` = derivable) — `capabilities.yaml.tmpl` + the two PO status
   references. **DONE 2026-06-21** as part of the capabilities-as-open-work-set reframe
   (completed capabilities graduate OUT rather than carry a `done` status). Points 1–2
   (SA scope = session input; backlog = derived gap) remain open pending the coverage
   harvester.

**Trigger to act:** when build/review land the `[REQ]` coverage harvester (so the
derived gap is actually computable), or when a multi-driver / orchestrated model
needs to avoid re-picking an in-flight capability. Until then the interactive
human-names-the-scope flow is sufficient and the current vague wording is harmless.

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
telemetry adapters (C3) — the one genuinely harness-coupled part of an agent is its
frontmatter. The body is harness-agnostic prose and renders fine everywhere.

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
reported transiently, not stored. **Still deferred:** the maintained `MEMORY.yaml` lessons
store (dedup, capacity-cap, confidence, reinforcement) — wf1's governor-ish overreach, and
nothing in wf2 consumes a distilled-lessons store.

**Also deferred — `handled` is an optimistic close.** wf-sa flips a learning to
`handled` when it *designs* the fix, not when build *lands* it; nothing downstream
confirms the code shipped. Recoverable — an abandoned fix's smell re-surfaces in a later
observation and re-distils. When the `[REQ]`-style coverage harvester exists, `handled`
can become *derived from commit citations* instead of a stored flag — the same move
deferred for capability "done" in C6.

**Trigger to act:** the orchestration half is done. Build the `MEMORY.yaml` store only if a
real consumer for a maintained lessons store appears (none today — the open learnings streams
suffice). When the `[REQ]` coverage harvester lands, switch `handled` to derived (above).

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
proving tests) from the tags on demand. Still missing for a full compliance trace: the
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

**Update 2026-07-09:** the **new/widened-seam half shipped** — the design-slice now carries
an `## Interface contracts` section (SA-authored, for components/seams with no source to
read yet) and the task contract an optional `interface_contract` field copied verbatim from
it. What this entry still covers is the **existing-interface** case: quoting a dependency
component's *current* interface (sourced from discover/drill) into a wiring task.

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

## C15 — The "test tree" is a path with ≥2 callers but no config key

**Date:** 2026-06-21
**Context:** wf-sa now invokes two tools that need the project's test tree as a `--scan` /
`--tests` root — `reconcile.py` (drain the built, SKILL.md Phase 1) and `next_id.py`
(allocate ids, Phase 3). Neither resolves the root from config; both say "the test tree" and
rely on SA judgement to fill the path.

**Observation:** this is the C1 threshold (a thing read by ≥2 callers belongs in one source of
truth) applied to a **path** rather than a config *reader*. The "one source of truth for paths"
ground rule says skills resolve paths from `.wf/config.yaml`; "the test tree" is the one path in
wf-sa resolved by judgement instead. A `paths.tests` key would fix it — **but a single root does
not fit polyglot / co-located layouts** (Go's `_test.go` lives beside source, so the "test tree"
is effectively the source root; a TS repo may scatter `__tests__`). So the key may need to be a
*list* of roots, or the convention may be "the source root" for grep-only consumers. Needs a
decision, not a reflex `paths.tests: ".wf/..."`.

**Trigger to act:** a third consumer needs the test root, or a dogfood run shows the SA
resolving it inconsistently between the reconcile call and the next_id call. Then add a config
key (likely a list) and point both invocations at it. Until then the judgement-filled path is
harmless and consistent across the two callers.

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

**Trigger to act:** a scope violation slips past review judgement, or a dogfood wants the
files_to_touch boundary enforced deterministically (e.g. a mechanical pre-review gate, or
folding it into the build-return inspection). Then add the check as a CLI verb and have review
cite it instead of eyeballing `git diff --name-only`. Until then the judgement read is cheap and
sufficient.

---

## C19 — Auto-route a wf-swa-reclassified design issue to wf-sa (spec escalation)

**Date:** 2026-06-22 (updated 2026-07-04)
**Context:** the code-layer/spec-layer principle has `wf-swa`, while fixing a
`contract_amendment` design issue, escalate to `wf-sa` when the root cause is actually a wrong
requirement or ADR. `wf-swa` fix-mode does the honest thing today: on a spec defect it **halts
and escalates to the human**, who re-runs `wf-sa`. The orchestrator (driver
`_resolve_design_issues` + LLM-track §2b) now **re-reads the design issue after the fixer
returns**: `resolved` → mark resolved in state and re-run the task; still `open` → block the
task and escalate to the human. What it does NOT yet do is inspect a changed `fix_kind` on a
still-open issue — a fix-mode re-classification (flip `fix_kind` → `spec_amendment`, leave
`status: open`) lands in the block-and-escalate branch instead of being re-routed to `wf-sa`.

**Likely shape when built:** in the still-open branch, if `fix_kind` changed, re-run
`dispatch-fix` (routing to `wf-sa`) instead of blocking the task. Touches the driver's
still-open branch in `_resolve_design_issues`, the LLM-track §2b, and the driver tests. Then
`wf-swa` fix-mode can re-classify autonomously instead of halting to the human. This is the
first producer of a `spec_amendment` DI, so it lands **with the `wf-sa` spec_amendment fix-mode
+ wrapper deferred in C4** — one is the route, the other its consumer.

**Trigger to act:** a real run hits a spec defect surfaced at the contract layer and the
human round-trip is friction. Until then the halt-to-human path is correct and cheap.

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
telemetry write is invisible until someone reads the sink.

**Likely shape when built:** the orchestrator records the sink's line count at
`wf pipeline dispatch` and the return inspectors warn when it did not grow — a cheap
baseline-compare in pipeline state, not a new subsystem.

**Trigger to act:** a role's telemetry goes missing again *after* the root-anchor fix.
Building the checker before a second loss mode shows up is machinery without evidence.
