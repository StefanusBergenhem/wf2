# wf2 — Candidates

Deferred observations about the wf2 toolkit itself: improvements, risks, and
thresholds worth acting on later. Not scheduled work — a parking lot so a
forward-looking note isn't lost. Promote an entry to real work when its trigger
fires; delete it when resolved.

---

## C8 — Agent frontmatter is Claude-format; pi/opencode untested

**Date:** 2026-06-15
**Context:** `install.sh` renders the `agents/` category per harness (seven agents as of
2026-08-10). The render path (copy + token-subst) is harness-agnostic, but
the agent **frontmatter** (`name`/`description`/`tools: Read, Grep, Glob, Bash`) is
written in **Claude's** schema. Rendering to `.pi/agents/` or `.opencode/agents/`
copies that Claude frontmatter verbatim — pi and opencode may expect a different
agent-definition shape (tool-grant syntax especially).

**Observation:** only the Claude target is dogfooded, so this is deferred like the
telemetry adapters (the Claude usage-hook adapter shipped 2026-07-10; the **pi** adapter
shipped 2026-08-11 — `pi_usage_hook.py`, folded by the driver after a `pi` launch since pi
has no end-of-session hook; opencode remains deferred) — the genuinely harness-coupled part
of an agent is its frontmatter. The body is harness-agnostic prose and renders fine
everywhere. The pi work touched the launch template and telemetry only; **frontmatter is
still untested on pi**, so this entry stands.

**Trigger to act:** when pi or opencode becomes a real target. Then verify each
harness's agent-definition schema and, where it differs, guard the frontmatter with
`wf:if <target>` blocks in `agents/wf-drill.md` (the renderer already supports them).

---

## C9 — a maintained `MEMORY.yaml` lessons store

**Date:** 2026-06-20 (compressed 2026-08-10 — every other half of this entry has shipped)
**Context:** `wf-retrospective` ships whole. It distils the telemetry session log into the two
learnings streams (`repo_observation` → `paths.learnings`, `wf_friction` →
`paths.wf_learnings`), clusters friction by `friction_kind` and design issues by `fix_kind`
before judging, and both streams drain mechanically: `wf pipeline complete-sprint` removes a
learning only when the stage's `serves:` header named it *and* every task covering it merged.

**Still deferred:** the maintained `MEMORY.yaml` lessons store (dedup, capacity-cap,
confidence, reinforcement). It was wf1's governor-ish overreach, and nothing in wf2 consumes
a distilled-lessons store — the two open streams suffice.

**Trigger to act:** a real consumer for a maintained lessons store appears. None today.

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

**Likely shape when built:** don't graduate completed capabilities into oblivion but into
a **trace store** — an archived capability ledger keyed to whatever realizes it — paired
with **C11 (product description)** as its likely host layer. Both are the durable
*external/record* tier sitting above the open work-set.

**What the trace can actually be built from (2026-07-31, post loop-redesign):** nothing
requirement-shaped persists in the tree. The EARS/REQ tag layer is gone entirely — so any
earlier "reconstruct from `[REQ]` tags" sketch is dead — and the acceptance criterion in a
transient task contract is the only requirement-grade statement there ever is. Two sources
survive: the **SYS-TC lane** (each shipped scenario's user-voice description sits in the
test itself, readable via `tools/reconcile/register.py`), and **`paths.archive`**, whose
snapshots of each merged stage — task contracts inlined — and of every drained capability
hold the full statements and the design narrative as of drain time. If a compliance mandate
ever fires this trigger, the trace is built from those two; nothing else exists.

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

**Date:** 2026-06-21 (rewritten 2026-08-08 for the stage-horizon revision)
**Context:** the new-seam half is gone — with a one-stage horizon there is no seam whose
two sides are built in different design units, so the slice's `## Interface contracts`
section was deleted. What remains is the **existing-interface** case: a wiring task
consuming a component an earlier stage already merged, or one already in the tree. The
contract has no `interface_contract` field (`stage check` B9 rejects it) and no
`dependency_commits`; the only vehicle is a `grounding` pointer — "pointers only, no prose
restating code". The mitigation is now structural rather than incidental: every stage is
cut against the merged tree, so the dependency's real signature exists by construction when
the contract is written.

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
stage close's heavy checks are green — loads the webapp-testing skill, exercises the
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

**Still live 2026-08-10:** `skills/wf-build/SKILL.md` still carries the unconditional
`### Refactor` pass, and s1 + s2 have run since without anyone counting its yield. The
measurement is now cheap and unrun: per-dispatch cost lands in `driver-logs` via
`dispatch.py`'s `_RESULT_FIELDS`, and a refactor-only commit is greppable in the merged
sprint branches.

**Trigger to act:** take the measurement — s1's 34 tasks and s2's 7 are sitting there. A
second observed zero-yield run promotes this; a non-zero yield closes it.

---

## C24 — `human_intervention` telemetry field (autonomy measurement)

**Date:** 2026-07-10 (narrowed 2026-08-10 — the driver now measures most of what this wanted)
**Context:** declined when `friction_kind` shipped. dems required repeated mid-session human
interventions (manual worktree recreation, hand-edited config, re-running roles) that are
invisible in telemetry — the sessions still ended `completed`. Autonomy is the metric wf2
actually optimizes, and it was anecdotal.

**What shipped instead, and it covers the bigger half.** The driver appends a `kind:
driver_event` row per dispatch/routing/stop (`tools/driver/events.py`), so *blocked* time is
now an exact derivation with no new field: s2's post-mortem read **19h04m of a 27h21m run
halted waiting for a human** straight off those rows (C43). Halt-wait is the dominant autonomy
cost, and it is already measured.

**The residual is narrower than the original entry:** intervention *inside* a running session
— the human fixing a worktree or re-running a role while the dispatch is live. The driver
cannot see that; only the role can report it. dems L-119 is the shape (a build session that
spent its own turns on a `ps` investigation and reported the incident in prose).

**Trigger to act:** a run whose autonomy story is not explained by halt-wait — i.e. the
driver-event derivation says the loop was busy while the human remembers babysitting it.

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
design role (a stage mis-allocates components along the wrong subsystem seam), or a second
run where a manual regroup would have paid for itself.

---

## C34 — mechanically gate shipped-SYS-TC supersessions in the stage

**Date:** 2026-07-14 (rewritten 2026-08-08 for the stage-horizon revision)
**Context:** superseding a *shipped* SYS-TC scenario is escalation-gate criterion 3:
wf-designer halts, writes `paths.decision_prep`, the human rules, resume applies it. That path
is role prose — no mechanism verifies a ruling actually happened. The mechanical pieces near it
are `complete-sprint`'s survivor sweep (a superseded id still tagged in the test tree), which
runs *after* the work was built against the supersession, and `workset check` A15, which only
warns when a work-set scenario's text has drifted from its shipped tag. A designer that skips
the halt and lists a shipped id under the stage's `supersessions:` ships unratified, surfacing
only in PR review.
**Observation:** a stage-check rule could cross `supersessions:` against the shipped
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
open stage's `serves:` list (`paths.stage`), plus the driver state's sprint id — and gate the
revise rule on it: a hit means the work is underway, so name that to the user and get explicit
assent to changing work in flight, or add a new capability instead. A capability that has been
taken up now also carries its `system_tests` set, which is a second, more durable in-flight
signal than the transient stage file.

**Failure it prevents:** wf-po rewrites CAP-9 while a stage builds tasks carrying
`covers: [CAP-9]`. The close-time adequacy question is then asked against intent nobody agreed
to, and nothing detects it — the id is still live.

**Trigger to act:** the first PO session that revises (not adds) a capability while a driver
sprint is in flight. Note this hazard was **created** by our own drain model, not inherited —
if it fires, it fires on us.

---

## C37 — skill prose is the one wf2 surface with no mechanical check, and it is where the bugs are

**Date:** 2026-07-14
**Context:** the slice-rejection change (this same day) took **five adversarial review passes, and
every pass found a critical defect**. All of them lived in **skill prose**, none was caught by a
test, and 284 tests were green throughout — including while the feature was dead code on its main
path. The Python was clean from pass 3 onward; its tests have teeth (mutation-verified). The prose
had eleven files of load-bearing instructions verified only by careful reading, and the defect rate
never dropped. Representative kills, all in roles that have since been rewritten or deleted: a
phase unreachable because the contract role left a failed sprint file on disk; the drain ordered
so it never drains; a fix mode's "commit nothing" colliding with the sprint branch's clean-tree
gate (the loop's *success* path); a skill claiming a gate closes a design issue, which no gate
has ever done.

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
(spec-layer-redesign; see `doc/archive/spec-layer-redesign-20260724.md`). Trigger evidence: dems
drained CAP-023 four times with every covering SYS-TC present and passing — each scenario proved
the design's decomposition, not the capability's promise; only adversarial source-grounded drills
found the residuals. `wf-adequacy` is that drill as a standing role, dispatched by wf-sa at the
capability-drain gate and at design validation. **Still open here:** (a) the `wf skills check`
linter half, unchanged; (b) the **wf2-side deployment** — an adversarial reviewer for wf2's own
skill prose (this file's original evidence) is still manual practice, not a role. Do not mark C37
resolved on the pilot-side ship alone.

**2026-08-10 — the evidence base widened, and the cost was feature-shaped.** The adequacy agent
stated its output line form exactly and ignored it in **10 of 10** dems digests, which blocked the
only mechanical convergence signal available to L-125's stop rule. **C38** already concluded its
two residual members are subsumed by this entry's role half rather than by a fifth point-fix. The
cheapest first move is no longer the linter: it is a **format gate on the one output another tool
parses**, because that class is mechanically decidable and it is where a prose miss costs a
feature rather than a re-read.

**That first move shipped 2026-08-10** as `wf adequacy check` — the adequacy digest now carries a
`**Residuals:** <n>` header, the agent runs the gate on its own file before it finishes, and the
gate rejects a verdict its enumeration contradicts. It is the first mechanical check on agent
prose anywhere in wf2, and it is worth copying at every other seam where a role's output is
parsed rather than read. **Both original halves remain open:** the `wf skills check` linter, and
the wf2-side adversarial reviewer for wf2's own skill prose.

**2026-08-14 — the first slice of the linter half shipped**, as
`tools/cli/tests/envelope_parity_test.sh` rather than a `wf` verb (a verb would put the check in
every consumer's context for a rule only the maintainer can violate; wf2 fixes before shipping and
assumes no consumer edits skills). It decides two of the linter's listed items — every
`paths./commands./limits./hygiene.` key a role's text names exists in the config template, and the
role's `envelope:` frontmatter matches that set in **both** directions — and it carries a `--fix`
that writes the declaration from the text, so the two cannot drift. It is wired into the new
repo-wide `run_all.sh`. Writing it immediately paid: two shared skills used real key names as
*format illustrations*, which forced those keys into every role that loads them, and wf-adequacy
was "reading" `paths.current_task` on the strength of an example. **Still open:** `$TOKEN`
declarations, `assets/`+`references/` existence, `description` length, cross-file step references,
and both original halves below.

**The premise "the Python is the trustworthy half" is now falsified, though.** A 2026-08-10 review
of `tools/` found the same semantic-contradiction class *inside the TDD'd code*, three times, each
a docstring asserting a consumer that does not exist: `drain.py:520` says the ship step folds
`paths.drain_report` into the PR body (nothing reads that file), `State.resume_phase` documents
itself as routing resumes (written and cleared, read only by a test), and `stages.py:50-57`
explains the resume gap it leaves while missing four steps that gap also skips. Tests do not check
that a comment is true, so the surface with no mechanical check is wider than skill prose — it is
**every load-bearing claim written in prose**, wherever it lives.

---

## C38 — Cut-time completeness: two heuristic members with no home

**Date:** 2026-07-20 (rewritten 2026-07-31 for the continuous-loop redesign)
**Context:** the contract-authoring layer is the most recurrent wf-toolkit friction family — a
contract is cut that is incomplete, internally contradictory, or unfaithful to source, and the
build agent recovers it a review-reject or halt cycle late. The source-claim half shipped
2026-07-22 and survives: a claim about a write/read surface, a mechanism, a fixture's reach, or
a command's environment must be resolved to source at cut time and cited as `<path>:<symbol>`,
and `stage check` C11 re-resolves every `grounding` pointer and errors when the symbol is not
there. The redesign also removed two of this family's historical habitats — the four-section
contract deletes `implementation_notes` and the per-task `interface_contract` (B9 rejects both),
leaving one home per fact.

**The residual — two members C11 still cannot reach:**
- a **bare qualified type claim** carries no path to resolve against, so C11 is blind to it.
  Its habitat narrowed with the stage-horizon cut: the slice's `## Interface contracts` section
  is deleted along with the slice, and `stage check` B9 rejects a per-task `interface_contract`
  outright (`tools/cli/stage.py:349`), so the **one surviving surface is a contract's
  `boundaries`** prose. Recurred four times as `interface_contract` type names that existed
  nowhere (`domain.RuleConflict` 2026-07-20, `domain.AttachmentTarget` 2026-07-27) — that
  vehicle is gone, but nothing stops the same claim being written into `boundaries` instead,
  and no rule reads it.
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
**Context:** dogfood run 2 (first parallel driver run). The check verb's `adr_index`
did an `rglob("ADR-*.md")` over the whole repo with a `_SKIP_DIRS` set that deliberately
does **not** skip `.wf` (the canonical ADR home is `.wf/adrs`). It therefore walked into
`.wf/transient/worktrees/`, where each per-task worktree is a **whole checkout carrying
its own copy of every ADR** — so every cited ADR came back defined in three "ADR sets"
and A5-collided with itself. On a resume with two live worktrees the driver halted with
`stage_check_red` on a design unit that had been green at sprint start. Only reachable
once worktrees can be alive at check time — i.e. the continuous loop's resume path,
which is why it never surfaced before. Fixed: `adr_index` now takes `paths.transient`
and prunes it (`tools/cli/stage.py:608`).

**The general shape:** any derivation that walks the repo tree is now walking N+1 copies
of it whenever tasks are in flight. Audited at fix time and re-audited 2026-08-10:
`impact.py`'s two scans skip `.wf` wholesale (safe); `pipeline.py`/`adequacy.py` glob a
named cache dir, not the tree. The one remaining exposure is **`tools/cli/impact.py:88`**,
which globs **config-authored** `add` patterns against the repo root with no prune — a
rule written as `**/*.yaml` matches inside every live worktree.

**Trigger to act:** an `impact` rule is authored with a `**` pattern, or any new
tree-walking derivation is added. The fix is the same three lines each time; the real
candidate is whether tree-walking derivations should share one pruned walker instead of
each re-deriving the skip set.

---

## C41 — nothing stops two drivers from running on one repo

**Date:** 2026-08-07
**Context:** a second `wf-driver` was started against dems while one was already ten
minutes into a stage. Neither noticed the other. The damage was immediate and
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
**Ruled 2026-08-08:** stays parked — build nothing until it recurs. The sprint-branch
shape in the last paragraph is ruled out entirely (only a maintainer debugging wf2 commits
to a live sprint branch); the pidfile remains the fix for the two-drivers shape alone.

**Detected independently, from inside.** dems `wf-learnings.yaml` L-119 is a build session
reporting the same incident with no knowledge of its cause: *"Mid-build, HEAD advanced to
an unrelated commit and the session's own uncommitted changes vanished from the worktree —
`ps` showed a live wf-driver plus a claude -p dispatch."* It cost that session a `ps`
investigation to reach a conclusion the driver could have stated at startup, which is the
diagnostic cost above, priced. A second, milder shape of the same thing lands whenever a
human commits to the sprint branch mid-run: `worktree_add` then destroys the live
worktrees for the same reason. The pidfile does not cover that one.

---

## C43 — a design phase can spend a whole run on one capability's scenario set

**Date:** 2026-08-09
**Context:** dems sprint s2 spent **4 wf-designer dispatches over 21 hours** (~3.1h of
agent wall time, $9.66 on the last one alone) and cut **zero stages**. Every round went to
CAP-015's SYS-TC scenario set: three consecutive escalations, each ruled by a human, then a
fourth round that re-cut the set 41 → 44 scenarios and stopped. Eight commits, all of them
scenario prose. No code built.

The driver defect that ended the run there is fixed (the resume path now runs the same
rounds a first cut gets, and a no-stage ending with work still open reports `no_stage_cut`
rather than claiming a drained backlog). But the fix makes the loop **continue into this
churn instead of stopping in it**: each cut now gets up to `SCENARIO_ROUNDS` dispatches,
and nothing across cuts notices that round N+1 is re-litigating what round N did.

**What is missing:** any count of, or signal about, design rounds spent on a capability
without a stage. The driver tracks stages shipped and the work-set's scenario total; it
does not track "cuts this sprint that produced no stage" or "consecutive escalations on
the same capability". Either is one integer in `driver.state_file`.

**Shape of the fix:** deliberately open — the honest options differ in kind.
*Cheapest:* report it (a line at each cut naming how many cuts this sprint produced no
stage) and let a human read it. *Stronger:* a stop rule at N consecutive no-stage cuts,
the same shape as the three-inadequate-verdicts park. *Strongest, and probably the real
one:* the capability was too wide, and the answer is a PO-session split rather than
anything in the driver — CAP-015 took 44 scenarios and 7 adequacy rounds to hold.

**Trigger to act:** a second sprint spends more than two cuts without a stage, or an
escalation chain on one capability reaches three again. Recurrence so far: **1**
(2026-08-09).

**How it ended (2026-08-10):** s2 did finish — the design phase cut stage 1 after the
churn above, and all 7 tasks built, reviewed and merged in **4h14m**. The full run was
27h21m, of which **19h04m was the driver halted waiting for a human ruling**, across the
three escalations and one false `work_exhaustion` stop. So the cost of this candidate is
now measured, and it is not the design *rounds* — those were ~3h of agent time. It is that
every escalation converts the loop from autonomous to human-blocking, and three of the four
halts came from one defect (the adequacy stop rule; dems L-125, still open). That argues
hard for the *strongest* option above being wrong as a first move: fix the stop rule before
adding a no-stage-cut counter, because a counter that parks a converging capability trades
a halt for a different halt.

---

## C44 — the retrospective mints learnings it cannot check are already fixed

**Date:** 2026-08-10
**Context:** the s2 retrospective wrote **7 new wf-toolkit learnings**; verifying each
against the wf2 tree found **2 already fixed** — L-128 (the per-role context report blind
to driver-dispatched roles) had shipped in `8d83612` two hours after the single telemetry
row it was written from, and L-131's staleness check already returns stale for a digest
with no `taken_at`. Both were minted as open findings and would have been read as open
work by the next session to consult the file.

**Why it matters:** the same failure the fix-it-drain-it rule exists to prevent, arriving
from the other direction. A learning stays open not because a fix went undrained, but
because the fix landed between the observation and the retrospective that harvested it.
`wf-retrospective` reads `sessions.jsonl` and pipeline state — it has no view of the
toolkit's source, so it cannot tell a live defect from a fixed one, and it re-processes
its own predecessor's session rows (the s2 window opened on the s1 retrospective's Stop
row). The rate scales with how fast wf2 is being fixed, which is exactly when it hurts.

**Shape of the fix:** two honest options. *Cheapest and mechanical:* stamp each session
feedback row with the toolkit's commit sha at dispatch (the driver knows it), and have the
retrospective drop — or flag — a wf-friction row whose sha is behind `HEAD` for a file the
fix touched. *Weaker but free:* the retrospective already writes learnings in two lanes;
have the wf-toolkit lane state, per entry, the source file it names, so a maintainer's
drain pass is a grep rather than a re-derivation. Neither should try to *judge* whether a
fix landed — that is the maintainer's read.

**Trigger to act — FIRED.** The second half of the trigger (the wf-toolkit lane crossing
~15 open entries) is met and then some: dems' `.wf/wf-learnings.yaml` stood at **52 open
entries** on 2026-08-10, so no reader can tell a live defect from a fixed one without
re-deriving all 52 against this tree. A drain audit now costs more than the dispatch-sha
stamp would have. Recurrence: **1** (2026-08-10, 2 of 7 entries) plus the standing
52-entry backlog.

---

## C46 — task size is ungated: increments wearing a task costume

**Date:** 2026-08-10 (harvested from the s1 handover as it was archived)
**Context:** s1's median task was 1–3 files and ~330 insertions. Three were not:
T31 (37 files, +1,113/−738, 3 builds, $25.59), T29 (34 files), T19 (18 files, 4 builds,
$26.16). **All three reworked** — the correlation between size and rework is the whole
finding, and rework was $96 of $417 task spend across the run.

**Why it survived the stage-horizon cut:** that revision fixed the *design* horizon, not
task width. `limits.tasks_per_stage: 10` bounds how *many* tasks a stage carries and is
documented as a mis-cut detector; nothing bounds how *big* one is. `stage check` validates
a contract's shape (four sections present, grounding pointers resolvable) and never its
scope.

**Shape when acted on — the open question is where the judgement lives.** A mechanical cap
at cut time has no honest unit (files touched is unknown at cut time; acceptance-criterion
count is gameable), so the likely answer is a designer-side heuristic plus a *post-hoc*
detector: flag at stage close any merged task whose diff or rebuild count sits far off the
stage's median, and route it to the retrospective rather than gating the cut.

**Corroborated 2026-08-10, from a direction s1 could not see.** The C48 context measurement
makes wf-build the context outlier of the whole system: `context_max` peaked at **601 k in
s2** and 316 k in s3, against a designer that peaks at 271 k / 278 k (figures corrected
after the C48 join bug) and a reviewer at 165 k. Nothing in the redesign predicted that,
and no threshold anywhere watches it. A build session at 600 k is a task whose contract,
tree reading and rework do not fit one context — which is this entry's "increment wearing a
task costume", measured at the other end. The build is also where the money goes (s1: $417
of task spend, 23% of it rework; s3: $69 of $118, and two of ten tasks took 45% of task
spend), so this is the expensive outlier, not a curiosity.

**Trigger to act:** a third oversized-and-reworked task lands in a driver sprint, or the
rework share of task spend exceeds s1's 23% in a later run. The cheap detector is now
obvious and shares C48's machinery: flag a merged task whose build `context_max` sits far
off the stage's median.

---

## C47 — every dispatch pays ~33 k tokens of harness before it does anything

**Date:** 2026-08-10 (harvested from the s1 handover as it was archived)
**Context:** measured on s1 — each headless dispatch started at **~33 k tokens** of fixed
context: 147 tools, 44 skills, 12 agents, a plugin and an MCP server, none of which any wf
role uses. That is the maintainer's personal Claude Code environment leaking into every
launch `driver.agent_cmd` makes. For a review peaking at 92 k it is a third of the context
window, and it is paid once per dispatch across every task, every attempt, every sprint.

**Why it is a wf2 candidate and not just an environment gripe:** `driver.agent_cmd` is
wf2's own config field, wf2 already reaches into it to deny the wait-for-a-later-turn tools,
and the same field is what an adopter inherits. It also **contaminates the measurement** —
every `context_max` figure in every run analysis carries this constant, so the stage-horizon
context-budget target below cannot be read honestly until it is separated out.

**Shape when acted on:** render `agent_cmd` with the harness's own scoping flags so a
dispatch loads only the wf role it was launched for, and re-measure. Cheap to try, and the
before/after is a single number per dispatch.

**ACTED ON 2026-08-11 (partially) — and the premise above is WRONG.** `--strict-mcp-config`
now ships in the claude `agent_cmd` and both overrides. Measured on dems' real launch
template: **147 tools → 27, six MCP servers → none, 33,291 → 31,152 tokens.** The scoping
flag reclaims **2.1 k, not the ~20 k the entry assumed** — the 120 MCP tools were
deferred-schema (listed by name, fetched on demand), so they were never costing what
"none of which any wf role uses" implied. Kept for blast radius over tokens: a
`--dangerously-skip-permissions` build agent had github's `create_pull_request` /
`merge_pull_request` / `push_files` in reach.

**What remains, and why it is probably not wf2's to fix:** the residual **~31 k** is Claude
Code's own system prompt + its 27 built-in tools + `CLAUDE.md`/`AGENTS.md`. Measured across
all 13 s4 build dispatches the floor is flat at **32,381–33,511 regardless of role** (a
discover, a designer and a review all start within 1 k of a build), so it is not
role-scoped context that scoping flags can reach. Treat ~31 k as a constant to subtract,
not a leak to plug.

**Trigger to act:** superseded for the token goal — reopen only if a harness gains a flag
that trims its own system prompt. The measurement half is done: **subtract ~31 k**, not
~33 k, when reading any `context_max`. Cf. **C48**.

**2026-08-14 — the constant floor is not where the context goes.** A full walk of dems'
heaviest build (S9-T1, 165 requests, 410 k peak) attributes it: ~32 k harness floor at
request 0, ~100–130 k of tool results, ~24 k of tool-call inputs, ~2 k of assistant prose —
and **125,713 tokens of the role's own reasoning**, 102 signed thinking blocks retained
across the turn, ~31% of the peak and more than every file it read. The harness sizes it
(claude: `--effort`), per role, through `driver.agent_cmd_overrides`, which the config
template now says. Two other measured leaks in the same walk, both since fixed: the dispatch
prompt carried all 48 config keys for a role that reads 9, and the contract carried the
whole stage's `flow` (158 lines) for a task whose own track is 63. What is left to attack is
the reasoning budget and the 52% of read bytes that went to rediscovering test fixtures
(the `--had-to-find` → AGENTS.md loop, shipped the same day).

---

## C48 — the stage-horizon design made four predictions and nobody has measured them

**Date:** 2026-08-10 (harvested from `doc/archive/stage-horizon.md` §15 as it was archived)
**Context:** the stage-horizon revision shipped as an **end-state cut** — no half-way
points, the stated failure mode being wholesale revert. It named four residual risks as
things dogfooding should check. s1 and s2 have both run. Two are now answered by the
evidence, two are not:

- **Answered — stage width.** §15 feared a trend toward 1 task per stage. s2's stage 1
  carried 7 tasks. No action.
- **Answered — stage close as the dominant serial term.** s2 merged all 7 tasks in 4h14m
  against a 27h21m run; close was not the bottleneck. Human halt-wait was (C43).
- **REOPENED 2026-08-10 — the context budget. The measurement was wrong; on the corrected
  number the prediction does not hold.** The first reading of this item took
  `wf telemetry roles` at its word: s3's designer showed 128 k avg / 143 k max and the item
  was closed as "the prediction held". That figure was a **wf-drill subagent's**, not the
  designer's. `telemetry.py`'s subagent lane could claim a *dispatch* candidate, so five
  drills running inside one designer dispatch evicted the designer's own transcript into
  `main_loop` — the reported "average" was arithmetically the mean of two drill rows
  (`(142741 + 112340) / 2 = 127540`). Fixed, with the lanes now disjoint. Re-derived over
  the same s3 archive:

  | wf-designer | as reported | corrected |
  |---|---|---|
  | s2 `context_max` avg / max | 164 k / 232 k | 210 k / **271 k** |
  | s3 `context_max` avg / max | 128 k / 143 k | 278 k / **278 k** (1 run) |

  Both runs were understated; s2 by less, because two of its ten designer dispatches did
  claim their own rows. Net of C47's ~31 k harness constant s3 is ~247 k — over the 150 k
  line §15 called a design failure, and level with the deleted wf-tl's 267 k peak the merge
  was meant to relieve. The clean sample is still **one** cut, so this is not yet a verdict
  on the design; it is a verdict on the closure, which was premature.

  **Second clean sample, s4 (2026-08-11): the prediction does not hold.** s4's one designer
  cut peaked at **268 k** (~237 k net of the harness constant) over 93 requests — the same
  band as s3's 278 k, on a stage that went 10/10 first-try with no repair. Two clean cuts
  now sit at ~240 k net against a 150 k line. The heaviest role is no longer the designer
  though: **wf-build averaged 217 k and peaked at 290 k** across 10 dispatches, up from
  s3's 152 k avg — and it is the role that runs ten times a stage, not once. Where it goes
  is work accumulation, not envelope: builds open at ~33 k and climb over 99 requests /
  115 tool calls, 15–38 `Read`s of 6–10 k chars plus 60–150 `Bash` calls each. No role
  compacted; the S3-T8 trajectory rises monotonically 33 k → 290 k with no reset. Cost
  tracked it: **$6.83/build in s4 vs $4.34 in s3**, while wf-review stayed flat
  ($1.60 → $1.65, 98 k → 105 k), so this is build-specific, not a stage-size effect.

  **Second finding, from the same bug:** the s3 retrospective *rationalised* the artifact
  rather than flagging it, footnoting that wf-drill "shares wf-designer's `session_id`, so
  only one row attributes to it independently". All six rows do share one `session_id`, and
  five of them are drills — the footnote explains a bug as a property of the data. A role
  reading its own tooling's output has no way to catch this class; only a re-derivation
  does. Cf. C44, which is the same shape from the other direction.
- **Open — does the role actually read the merged tree?** Three deletions (claimed scope,
  interface contracts, the design narrative) were all replaced by one behaviour: the
  designer grounds by reading merged source. If it grounds on the plan instead, the
  revision traded a stale forecast for **no** forecast, which is worse. s2 is weak evidence
  either way — its design phase spent four dispatches on scenario prose (C43), which says
  nothing about how it grounded when it finally cut.

**Composition measured 2026-08-11, and the plumbing half is FIXED.** C48 left "where it
goes is work accumulation, not envelope" as an assertion; attributing all 58 s1–s4
`wf-build` driver logs splits that accumulation in two. Per dispatch, net of C47's harness
constant (log-derived, so ~20% low on absolutes — calibrated against one real transcript
whose true peak was 222,722):

| | light half (52 req) | heavy half (120 req) |
|---|---|---|
| role prompt (5 SKILL.md reads) | 7,943 | 7,970 |
| `config.yaml` | 4,858 | 4,963 |
| task contract | 2,317 | 2,371 |
| source/test file reads | 4,816 | **33,632** |
| bash grep | 1,661 | **11,245** |
| bash cat/sed | 2,147 | **9,642** |
| **total** | **38 k** | **93 k** |

The top three rows are flat — a **fixed ~15 k plumbing tax**, of which only 2.3 k is the
contract. The build spent ~6× more context learning *how to be a wf role* than learning
*what to build*. Four items were pure protocol overhead and are now fixed: `config.yaml`
(19,972 chars, **82% comments**, ~922 tokens of data) is replaced by a resolved
paths/commands/limits/hygiene block in the dispatch prompt — **473 tokens against 4,993**,
whole-block so no per-role subset can drift; the bare `assets/*.tmpl` path that resolved
nowhere from a worktree (**48 of 58 runs hunted for it, 35 ran a filesystem-wide `find /`**)
is anchored on a new `role_dir` param; the feedback-file probe **58 of 58 runs ran** is
replaced by an explicit `mode: build|fix`; and the preamble's "locate with Grep" rule —
unfollowable, because the dispatch grants no Grep tool, which is *why* bash `grep`/`cat`
carry 11–21 k — is reworded to hold with or without one.

**What is NOT fixed, and it is the whole heavy tail.** Exploration is 54.5 k of the heavy
half's 93 k. Contracts name 8–15 files; the runs touch 15–85, **60–80% never named**
(S4-T1: 15 named, 85 touched). S4-T1's story even says "eight test files establish boundary
membership by passing a literal `Members:`" and names none of them, so the build greps for
them. This is C46's "increment wearing a task costume" measured from the contract side, and
it needs a designer-side change (grounding enumerates the touch set) or a build-side one
(build gets `paths.discover_brief`, so ten tasks a stage stop re-deriving the same
structure by grep ~40× a sprint). Neither is plumbing; both move judgement between roles.

**Trigger to act:** two items remain. The grounding question needs an adversarial read of a
cut stage against the tree it was cut from — telemetry cannot answer it (a role that reads
the plan and one that reads the tree both just show tokens); run it at the next cut. The
context budget needs the corrected number re-read after two or three more stage cuts,
which is now cheap and honest.

---

## C49 — concurrent same-agent subagents race on one start-stamp file

**Date:** 2026-08-10
**Context:** `wf-basics` §2 has each session write its start stamp to
`<root>/<paths.transient>/ts-start-<agent>` and delete it at END. The filename is keyed by
**agent name alone**, so N concurrent subagents of the same role share one file: the first
to finish deletes it, and every other one falls through the documented
`|| echo "$TS_END"` degradation and records `started_at == ended_at`. Measured on s3 — five
`wf-drill` subagents inside one designer dispatch produced **one** real duration and four
zero-width ones.

**Why it matters:** a zero-width window is unjoinable, so those four sessions carry no
context or token measurement at all. Until the C48 fix they were worse than missing — they
were the rows that displaced the designer's own. Now `wf telemetry roles` reports them as
`unjoined_subagents`, so the loss is visible and bounded, but it is still a loss, and it
scales with exactly the fan-out wf-designer is designed to do.

**Shape when acted on — the open question is what identifies a subagent instance.** The
shell cannot carry one (START and END are separate processes, so `$$` differs), and the
harness exposes no subagent id to the tool layer. Two honest options: have the skill body
mint a short token at START and reuse it in both commands (LLM-carried, but the token lives
in its own context, which is the reliable part — it was the *shell env* that was not); or
have START create `ts-start-<agent>-<nanoseconds>` and END claim the oldest unclaimed
stamp, which keeps every duration real but may permute which sibling gets which (and can
pair a start after its end — needs a guard). Neither is obviously right, which is why this
is parked rather than fixed.

**Trigger to act:** a role's fan-out grows past ~5 concurrent same-agent subagents, or
`unjoined_subagents` becomes a large enough share of a run's usage rows that a role's
measurement cannot be read. Recurrence so far: **1** (s3, 4 of 5 drills).

