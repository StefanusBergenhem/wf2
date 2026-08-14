# wf2 stage-horizon revision — design cut per stage, proof owned by the capability

**Status:** settled 2026-08-08 (Stefanus + Claude, grounded in the s1 run; 33 decisions
closed across five interview rounds). Residual risk in §15. The
per-stage role is designed in §11, the driver in §12.
**Amends:** `loop-redesign.md` §5.1 (the four-level unit hierarchy), §5.2 (the
slice as a 2–5-increment forecast), §5.3's home for SYS-TC, §6.3 (PR unit).
**Does not touch:** the build→review chain, the worktree/merge machinery, the escalation
gate's five criteria, the governor. The phase machine keeps `sprint_start`, `designing`,
`awaiting_ruling` and `closeout`; only `increment_loop` is replaced (§12).

---

## 1. What s1 proved

`loop-redesign.md` set out to kill one failure: *the whole graph authored up front,
no feedback per stage*. It JIT'd the **contracts** (a TL per increment) and left the
**design** monolithic. The failure moved up one level instead of dying.

Measured on dems `sprint/s1` (34 tasks, 4 increments, 43.1 h):

| | |
|---|---|
| slice authored before task 1 built | 692 lines · 4 increments · every SYS-TC · every interface contract |
| `sprint.yaml` at close | 4,553 lines / 191 KB (2,977 lines of task body) |
| its growth, append-only | 680 → 1,403 → 2,114 → 2,977 |
| sub-layers | 17, mean width 2.0 (concurrency 1.57× against `max_parallel: 4`) |
| design-feedback events in 43 h | 4 |
| wf-tl peak context | 267 k median / 281 k max — the highest of any role |

The four-level tree was **specified**, not drifted: `loop-redesign.md` §5.1 names it,
`wf-basics` §3 teaches it, and `increments_per_sprint: 4` × `tasks_per_increment: 10`
ratifies it at exactly the 34 tasks s1 produced. The unit names rotated (old sprint →
"increment", old stage → "sub-layer") and a new layer was added above them.

## 2. Two design levels, not four

```
capability   durable   the promise + the SYS-TC set that would prove it + both adequacy gates
plan         durable   milestone altitude, rolling, re-validated at every cut
stage        transient design + tasks, cut from MERGED reality, exactly one ahead
task         transient one contract, one build agent, one review chain
```

`increment` and `sub-layer` are **deleted as design units**. A stage is what a
sub-layer was — the tasks with no dependency between them — except that it is now the
unit the design itself is cut at, so there is nothing above it to forecast and nothing
below it to serialize into.

The PR is **packaging** (§7, §9), carrying no design meaning. That is the invariant that
keeps this collapse from recurring: any unit that both *forecasts work* and *names a
level* will regrow into an increment.

## 3. The design horizon is exactly one stage

Each cut reads the durable inputs (capabilities, charter, architecture, plan) plus
drills plus **the tree as merged**, and produces exactly one stage. Then it is re-cut.

Not two. A stage is *the tasks with no dependency between them*, so a second stage
depends on the first by construction — cutting it in advance forecasts against unmerged
code, which is the bet this revision exists to delete. And two stages that are genuinely
independent of each other are not two stages: they are one wider stage, and cutting them
together is strictly better because they then run in parallel. The horizon buys back one
dispatch round-trip (~10 min, $2–6; ~2.8 h across s1's 17 stages) at the price of the
invariant — and a soft "1–2" bound is what grew into four increments last time.

**Consequences, all deliberate:**

- There is no artifact to amend in flight. `wf sprint append-task` is **not built** —
  the need for it was a symptom of the horizon, and the next cut *is* the fix. The
  0-byte truncation risk (dems L-120) dissolves with it.
- The repair ladder's `contract_amendment` and `slice_recut` rungs lose their subject.
  What survives of repair is re-cutting the *next* stage with the defect as an input.
- Interface contracts spanning increments lose their reason to exist: a seam's two
  sides are either in one stage or in successive cuts against merged source.
- `limits.increments_per_sprint` retires. `limits.tasks_per_increment` becomes a
  per-stage width bound.

## 4. SYS-TC belongs to the capability

The scenario set is the capability's proof obligation, so it lives beside the promise
in `paths.capabilities` — not minted into a transient slice and reconstructed at drain
time from the test register.

- **Authored** when the capability is first taken up for work, from the durable
  documents. Not at PO time.
- **Amendable** while it builds. Adding, re-wording, and removing scenarios against
  what the tree turns out to be is expected, not exceptional — an up-front set derived
  from documents systematically under-covers a legacy repo, which is wf2's premise
  case. (CLAUDE.md's own record: four times a design's proofs were all present while
  the capability was false, because the decomposition had missed sibling code paths.)
- **Landed per stage**, never batched: each scenario ships in the stage that closes
  its path. `loop-redesign.md`'s "assign each case to the increment that completes its
  path" carries over verbatim. Deferring the set to the end of a capability
  reintroduces the V-model integration tail.
- **Bounded by the drain.** Only capabilities in flight carry a set — in dems, 1–2 of
  16 open entries — so the file does not accumulate.

**Two adequacy gates, both source-grounded:**

1. **At authoring** — does this scenario set cover the capability's promise? Must be
   drilled and adversarial against source. Judging a doc-derived set against the docs
   it came from is the decomposition validating itself, which is the exact failure the
   adversarial rule exists to prevent.
2. **At completion** — do the *shipped* scenarios prove the capability? Unchanged from
   today: adversarial, source-grounded, never grounded in the design's own
   decomposition. This is the drain gate.

Gate 1 asks `wf-adequacy` the question token **`proposed-set`** (replacing
`iteration-claim`, whose input — the claimed scope — is deleted). A distinct token is
required, not cosmetic: `drain-capability` globs the newest `full-promise` digest as its
verdict, so a design-time digest written months before anything ships must not be
globbable as one. Gate 1 reuses `adequacy.py`'s `PARK_THRESHOLD` wholesale — **three
consecutive inadequates parks the capability** for a PO session and the role reports
escalated. The failure mode is identical to gate 2's: a promise nobody can decompose into
provable scenarios is a wording problem, not a design problem.

### Schema and the cross-cutting scenario

Scenarios are **nested under their capability with no `covers:` field** — the nesting is
the whole answer. A scenario proving two capabilities is **duplicated under both, keeping
the same SYS-TC id**: one test, one `[SYS-TC:id]` tag, satisfying both sets at drain time.
The duplication's own failure mode — two copies of the text drifting — is closed
mechanically: **`wf workset check` errors when one id carries different text under two
entries.**

**Shipped-ness is never stored.** It derives from `[SYS-TC:id]` tags in the test tree via
the register. Storing it would be the exact governor violation this design is built to
avoid.

**Learnings** need no scenario set — a learning is one actionable observation whose proof
is its task's own acceptance criteria and the merge record, and requiring an e2e per
learning inflates the test tree. A learning *may* carry one when its observation is
genuinely end-to-end, nested in `paths.learnings` in the same shape; it does not gate the
learning's drain. Because the gate spans both files it is **`wf workset check`**, not `wf
capability check` — it homes the slice's A10 (every case names what it covers) and A11
(every served CAP has a case), and it gives Phase 3 a mechanical release gate where today
it ends on nothing but an LLM verdict. A capability carrying **no** set is legal — it has
not been taken up yet — so `wf-po` is never required to author scenarios.

**Amendment authority** follows the shipped line: unshipped scenarios the role amends
freely; a shipped one is a promise the system already keeps, so retiring it stays
escalation criterion 3.

**Phase 3 seeds from the register.** A capability whose scenarios already shipped starts
its set with those, not from a blank page — otherwise gate 2's set-difference never
empties and the capability can never drain. This is also why dems needs no data migration.

## 5. Where the slice's contents go

The slice is deleted. Its eleven sections are accounted for one by one — nothing
load-bearing is dropped silently.

| slice section | disposition |
|---|---|
| `Serves:` header | **stage artifact** — still the PR body's input; the *drain* anchor moves to the capability's SYS-TC set + the merge record |
| System view (brief / drill pointers) | **stage artifact** — a pointer, re-derived each cut |
| Design narrative | **dissolved** — see below |
| Claimed scope | **deleted** — it fed gate-1 adequacy against a per-iteration claim; gate 1 now judges the scenario set against the *whole* promise, once. The plan's `Delivers:` line carries the human-facing half |
| Increment goal | **stage goal** |
| Increment allocation | **stage allocation — the architecture bind is non-negotiable.** `slice check`'s rejection of a component in neither the repo nor `paths.architecture` carries over to the stage gate unchanged; without it the role invents structure |
| Increment flow | **stage artifact** (see below) |
| Increment checkpoint | **stage artifact**, and the PR boundary rule (§9) |
| System test cases | **the capability** (§4) |
| Interface contracts | **deleted** — they existed only for a seam spanning increments. At one stage both sides are in the stage, or the next cut shapes the second side against merged source |
| Supersessions | **stage artifact**, unchanged in substance; escalation criterion 3 unchanged |
| NFR & authz | **stage artifact**, plus gap 1 below |
| Binding ADRs | **re-derived per cut** — already derived from `constraint:` lines, never stored |
| Soundness (7 heuristics) | **stage artifact** — a process gate, not an artifact; smaller surface per stage |
| Decision log | **stage artifact → PR body**; a PR batching several stages concatenates them |

### The design narrative dissolves — it does not relocate

It does **not** go in the plan. `hygiene.plan_max: 60` holds the plan at milestone
altitude with *no component allocation*, and the plan is the PR's direction-drift
surface. Decisively: the plan is **durable and committed**, and a narrative describing
how components wire together is derivable from source the moment it is built — storing
it is a straight governor violation, the same class of mistake as the REQ prose already
deleted from the test tree.

Decomposed, every element is already housed:

| narrative element | home |
|---|---|
| the shape chosen and the force that drove it | **charter** (target shape, ranked forces), or an **ADR** when it clears the threshold |
| each touched component's role | **architecture map** (1–2 sentence intent + depends-on) |
| how behaviour flows through the components, wiring included | **merged source** for what is built; the **stage artifact** for what is being built now |

Cross-stage coherence therefore comes from reading the tree — which is the point of the
horizon change, and strictly better than a narrative authored before stage 1 ran.

### Two gaps this exposes (both present today)

**Gap 1 — NFR/authz deferrals evaporate.** A deferral carries "what · why · revisit
when" into a transient decision log and dies at close; per-stage it dies sooner, and
nothing carries the revisit trigger. **An NFR or authz deferral appends an entry to
`paths.learnings`** — a deferral *is* an open observation, which is what the durable
open work-set is for.

**Gap 2 — a below-gate decision taken at one stage that shapes a later one.** **A
below-gate decision that must outlive its stage becomes an ADR or a learning; if it is
neither, it was not load-bearing.** A rule, not a new document — and the same test the
ADR threshold already applies.

## 6. The stage artifact

**One file, YAML.** `design-slice.md` and `sprint.yaml` both retire into `paths.stage`
(`stage.yaml`). The two-file split existed because two roles wrote at different times;
one role writing in one sitting needs one file, one write, one gate. YAML because the
contracts must be machine-read for the build envelope and the checks, and the prose
already lives in block scalars (`story: |`) today. Markdown would mean regex-parsing
contracts — which is what `slice_checks.increment_section` does now, and is the fragile
part.

### `depends_on` is deleted

A stage *is* the tasks with no dependency between them, so an edge inside one is a
contradiction and the field has no meaning. What goes with it:

- the DAG machinery — cycle detection, `_is_later`, topological layering in
  `pipeline compute-stages`;
- `dependency_commits` grounding — upstream is now one thing, the merged tip the stage
  was cut from;
- the e2e edge. A system test moves to the **next** stage, running against a genuinely
  merged tree instead of a worktree with dependencies grafted in — and landing the
  SYS-TC exactly on the PR boundary (§9).

wf-tl's soft rule (*two tasks on the same file need an edge*) hardens into a partition:
**two tasks in a stage may not centre on the same file** — factor it out, or push one to
the next stage.

### Shape

```yaml
# STAGE — one cut. TRANSIENT: written fresh, archived and deleted at stage merge.
stage: 7
serves: [CAP-004, L-118]
goal: "<one line>"
grounded_in: ["<discover_brief>", "<drill digest>"]

allocation:
  - component: <id from the brief or the architecture map>   # gate: the A12 bind
    does: "<what it must do in this stage>"
flow: |
  <how the behaviour moves through those components, in order, wiring included>
checkpoint: "<after this stage, X demonstrably works — observed by how>"

supersessions: [...]        # omit when nothing shipped is invalidated
nfr:   [{envelope: "subject · metric · threshold · condition · source", task: S7-T2}]
authz: [{guards: "<entry point>", task: S7-T3}]
                            # a deferral names what · why · revisit · learning: L-NNN
soundness: {boundary_srp: "...", ownership: "...", ...}   # one line per heuristic
decisions: ["Assumption — ...", "Deferral — ... (→ L-142)"]   # → PR body

tasks:
  - id: S7-T1               # stage-prefixed: unique across the PR, no counter to maintain
    title: "..."
    covers: [CAP-004]
    story: |
    acceptance: [{id: AC-1, criterion: "...", tests: [{level: unit, target: "..."}]}]
    boundaries: |
    grounding: ["<path>:<line> — <what is there>"]
```

No `increment:`, no `depends_on:`. At s1's ~88 lines per task body, a 2–4-task stage
runs **~220–400 lines** against the accumulator's 4,553.

**Stage numbering is repo-lifetime monotonic**, via an `id_counters.stage` high-water
mark like `id_counters.sys_tc`. Never per sprint: a restarting number that keys state is
precisely the bug that produced s1's four phantom 28-hour sub-layers — stage numbers
restarting each increment while summaries were keyed by number alone, and the
retrospective then reasoned on top of them.

**`limits.tasks_per_stage` stays at 10.** The cap is a *mis-cut detector*, not a
concurrency knob — `driver.max_parallel` already queues the excess. A ten-task stage is
not wrong if the work genuinely partitions; it is an excellent stage. Lowering it to
`max_parallel` would push wide work into artificial serialization, which is the failure
this revision exists to remove.

The build envelope replaces today's `increment_narrative` with `goal` + `allocation` +
`flow` + `checkpoint` (~15 lines). Everything else in the header is gate or PR material
and never travels — a field the build agent cannot act on is pure context cost.

### Lifecycle

Written fresh at each cut. At stage merge the driver appends `serves`, `checkpoint` and
`decisions` to the PR-body accumulator, archives the file to `paths.archive`, and
deletes it. Nothing accumulates, so nothing needs append integrity.

**One consequence the deletion forces.** `complete-sprint` runs at ship, several stages
later, and it drains a learning when every task covering it merged — but by then the
stage files that named those `covers:` are gone. So `load-stage` records each task's
`covers` into its run-state entry and unions the stage's `serves:` into a run-state
accumulator, and the drain reads that. This is **not** the accumulator §1 convicts: it is
machine-owned run state, keyed by the merge record, never read by a role, and reset at
sprint close. Without it a learning served by the first stage of a four-stage PR could
never drain.

### One gate: `wf stage check`

| survives | dies | new |
|---|---|---|
| B3, B6, B7, B8, B9, B10, B11 — the contract schema | C1 — deps / cycles / ordering | **C19** — error on any `depends_on` or `increment` field |
| C4 (e2e shape), C6 + C16 (covers names open ids) | C12 — `dependency_commits` | **C20** — warn on overlapping `grounding` paths between two tasks: the partition rule, mechanized as far as it goes |
| C11 (grounding resolves), C13 (duplicate test targets) | C15 — increment declared in the slice | **C18** — error when a `resolved` design issue names no successor task, or one this stage does not carry (`fix_kind: no_change` exempt) |
| C14 → `limits.tasks_per_stage` width cap | C17 — cumulative append integrity | |
| **A12 — component in the repo or the architecture map**; A4/A5 — ADR citations resolve | A0 — slice absent; A8 — claimed scope; A9 — increment numbering; A2 — a scenario with no e2e task | |
| **A6** redefined — `goal`/`flow`/`checkpoint` present and not placeholders (where the deleted `## Design narrative` check's job went); **A7** redefined — `serves:` as a YAML key | A10/A11 — **moved** to `wf workset check` | |

C17 exists solely to police appends to the accumulator. It dies with the accumulator,
and so does the failure class that truncated `sprint.yaml` to 0 bytes.

The work-set gate is its own verb because it spans two files and runs at a different
moment. `wf workset check`: **A10** scenario well-formed, **A11** one id's copies carry
identical text across entries, **A13** no id above `id_counters.sys_tc`, **A14** no
duplicate id within one entry, **A15** *warn* when a set's text has drifted from its
shipped tag, **A16** a present-but-unreadable work-set file. An entry with no set passes
silently — it has not been taken up.

### The cost, stated

**Design dispatches now equal the dependency depth of the work.** s1's 17 layers had
widths `2,1,1,5 / 2,1,1,4 / 2,2,4 / 1,2,1,1,1,3` — eight of them width 1 or 2. That is
17 cuts instead of 4: roughly **$40–100 and ~2.8 h of serial time** on a 43 h run that
was 61% idle. Affordable, but the shape matters: a deeply chained refactor pays a design
dispatch per link. Width therefore becomes a **scope-selection** problem — the role cuts
wider by choosing less-coupled work — which is where that decision belongs and where it
was not before.

## 7. Naming: sprint is packaging, stage is the cut

`sprint` is **kept**, for the branch / PR / closeout unit — nearly what it already means
in the driver. It is **not** reused for the stage. Reusing a familiar word for a changed
unit is the exact mechanism of the last drift (old sprint → "increment", old stage →
"sub-layer", and the semantic shift went unnoticed for a whole redesign), and "sprint"
carries the agile reading of a time-boxed batch of *committed, forecast* work — the model
this revision deletes. These are LLM-facing files; the word is the prompt.

Kept unchanged: branch naming `sprint/s1`, the driver phase machine (`sprint_start` → …
→ `closeout` → ship), `driver.max_unmerged_sprints`, the PR-body accumulator.

| now | becomes |
|---|---|
| `paths.sprint` → `sprint.yaml` | `paths.stage` → `stage.yaml` |
| `paths.design_slice` | folded into `paths.stage` |
| `wf sprint check` / `task` / `materialize` / `prune` | `wf stage check` / `task` / `materialize`; `prune` dies |
| `limits.tasks_per_increment` | `limits.tasks_per_stage` |
| `limits.increments_per_sprint` | retires |
| `wf slice check` | folds into `wf stage check`; `tools/cli/slice.py` → **`mdread.py`** |
| — | new: `id_counters.stage`, `driver.max_stages_per_sprint`, `wf workset check` |
| `pr-body-{sprint}.md`, hard-coded at `phases.py:425` | **`paths.pr_body`** → `.wf/transient/pr-body.md`, one file reset at `sprint_start` |
| `driver.agent_cmd_overrides.wf-tl` | retires with the role |
| `skills/wf-orchestrate/` | **deleted** — `loop-redesign.md` §9 deferred it until the first green driver sprint; s1 was it |

`tools/cli/slice.py` cannot be deleted wholesale: it is the shared markdown reader.
`section()` is imported by `pipeline.py:1397` (adequacy `## Residuals`), `phases.py:184`
(`## Ruling`), `:393` and `:463` — and the ruling brief and adequacy digests stay
markdown. As `mdread.py` it keeps `section`, `_prose`, `adr_index`, `adr_citations`,
`limit`, `architecture_components`, `_component_id` and the A12 finding; everything
increment- or slice-shaped goes.

### Closeout splits in two

Today `closeout: [wf-retrospective, adequacy, ship]` runs all three at the sprint
boundary. The two triggers no longer coincide — a capability can complete mid-sprint,
and a sprint can ship without completing any:

- **per sprint** (the PR boundary) — retrospective, ship;
- **per capability** — adequacy gate 2 → drain, fired whenever a capability's SYS-TC set
  is fully shipped, checked at every **stage** close.

## 8. Test levels below SYS-TC are unchanged

SYS-TC is the top layer only, never the whole test strategy. The levels beneath it are
already mandated through the acceptance criteria and stay exactly as they are:

- every criterion carries `tests` (each with an explicit `level:`, `seam:`, `target:`)
  or `verified_by: inspection` — never neither, never both;
- `wf-build` runs red→green→refactor per entry against real dependencies, not mocks;
- `system-testcase-syntax.md` keeps integration-shaped scenarios out of the SYS-TC
  layer from above, and `task-contract.md` keeps e2e irreplaceable from below.

Because these derive from acceptance criteria, and criteria are cut per stage from
merged reality, **the lower levels are already just-in-time and this revision does not
touch them.** Only SYS-TC migrates.

Known gap, not closed here: nothing gates the *mix* of levels — presence is checked,
proportion is not.

## 9. PR cadence follows an end-to-end fact

The PR is not the correctness gate (wf-review per task and the SYS-TC set are). Its
jobs are direction feedback and bounding regret, so it is sized by the second: how much
wrong work can be paid for before it can be stopped.

**Ship a PR whenever the merged tree crosses an end-to-end checkpoint — a stage that
lands at least one SYS-TC — batching the preceding non-checkpoint stages into it, or at
`driver.max_stages_per_sprint: 4`, whichever comes first.**

The fallback is not optional trimming. An enabler or hardening stretch can run many
stages without landing a scenario — s1's increment 1 built four tasks before its first
e2e — and without a bound that produces exactly the unbounded PR this rule exists to
prevent. Four stages is, at s1's mean stage width of ~2, about eight tasks and ~40 files:
roughly one s1 increment, the largest genuinely reviewable unit in the run's data. It also
keeps the SYS-TC landing the *usual* trigger rather than the exception.

**This rule is load-bearing code, not a preference.** `terminal.sprint_done`
(`pipeline.py:382`) is already dead — nothing in `tools/driver/` reads it. The driver's
actual sprint-completion signal is `loop.py:103-106`, `_increment_numbers(rt)` exhausting
the slice's increment list. That list is deleted, so this rule is its only replacement.

At s1's $13.50/task a PR carries ~$50–90 of exposure (against ~$460 for a per-capability
PR over 165 files). Its body writes itself: the scenarios now provably passing, plus the
plan delta. The boundary is a *fact about the system*, which is what keeps it from
drifting into a new hierarchy level.

`driver.max_unmerged_sprints` becomes a PR stack-depth bound at **3**. A PR is now ~8
tasks, so three unmerged is ~24 tasks of exposure — less than the 34 already accepted in a
single s1 sprint, at a third of the stalls. The review brake engages independently: a
`CHANGES_REQUESTED` verdict still stops the loop. Setting it to 1 for a first bring-up run
is a knob, not a design variant.

## 10. What this is NOT justified by

**Throughput.** s1's 1.57× concurrency is a decomposition problem, not a horizon one.
Increment 4's chain — `[T34][T26,T27][T28][T29][T30][T31,T32,T33]` — is a strangler
refactor whose every deletion is blocked until its callers move. A per-stage cut
produces the same chain, better informed. Worth ~9 machine-hours at most.

**Dead clock.** 26.6 of 43.1 hours were dead, and **zero** were a human deciding
anything (s1 escalated 0). The breakdown is faults: 12.8 h `tasks_blocked`, 7.4 h
rate-limit, 3.1 h `tl_no_contracts`, 2.1 h `launch_failed`, 1.2 h other. Dead clock is
a fault-recovery workstream, orthogonal to this revision — it neither improves nor
worsens it.

**The case that holds** is correctness of feedback: designing against merged reality
instead of a four-increment forecast, and deleting the amend/append/prune surface
outright. More halts are acceptable — and welcome — when they are true halts that
catch a wrong direction before it is built.

## 11. The role

`wf-designer` keeps its name — contracts are design output, and a rename is churn.
**`wf-tl` is deleted**; its references move over.

| moves in | from |
|---|---|
| `design-heuristics.md`, `system-testcase-syntax.md`, `plan.md.tmpl` | wf-designer |
| `task-contract.md`, `criterion-syntax.md`, `design_issues.yaml.tmpl` | wf-tl |
| `stage.yaml.tmpl` | replaces `slice.md.tmpl` + `sprint.yaml.tmpl` |

One linear procedure, one conditional step, one surviving mode (`resume`).
`originate` and `repair` existed because the design outlived the evidence.

| phase | what |
|---|---|
| **1 Ground** | wf-basics + telemetry stamp; read charter, architecture, plan, capabilities, learnings, discover brief, **and open design issues**; skip parked capabilities; derive the system-test register; read the `constraint:` line of every ADR that may bind |
| **2 Choose** | what is next given the plan, the open work-set, the open design issues, and the tree as merged. **Cut for width** — the widest set of work with no dependency between its parts, which may span more than one capability or learning. Work exhaustion → clean exit. Gate: drill anything touching existing code |
| **3 Scenarios** *(only when taking up a capability/learning with no set)* | load `system-testcase-syntax.md`; mint from `id_counters.sys_tc`; write into `paths.capabilities`; dispatch `wf-adequacy` source-grounded until adequate — **then stop and report** |
| **4 Plan** | read it every cut; **rewrite only when a milestone shipped or the evidence contradicts one** |
| **5 Cut** | the stage header (goal · allocation · flow · checkpoint) and its tasks — no `depends_on`, partitioned by file, e2e tasks for the SYS-TCs whose path merged last stage; supersessions; NFR & authz (a deferral appends a learning); decisions |
| **6 Soundness** | the seven heuristics, one auditable line each |
| **7 Release** | `wf stage check` to pass; bump counters; commit plan + capabilities + config; report |

**Phase 3 ends the dispatch.** Authoring a scenario set and driving adversarial adequacy
to convergence is the fattest work in the loop; letting it also cut a stage makes the
worst-case dispatch the one already carrying the most context. Ending there costs one
extra dispatch per capability (~$3, ~10 min, a handful per project) and keeps every
dispatch one-jobbed. This is the primary defence against the §12 context risk.

### Design issues drain at authoring

Today `issues.promote()` copies a build/review-raised issue into `paths.design_issues` as
`status: open`, `issues.mirror()` parks the task, the design role's repair mode sets
`status: resolved` + `fix_kind`, and `sweep_design_issues` (`orchestrate.py:511`) prunes
the closed entries. That drain point is **kept**: once the design has answered the issue
its content lives in a task contract, and the contract's own build→review→merge lifecycle
takes over. Holding the issue open until merge duplicates state the contract already
carries; and if the fix turns out wrong, that is a *new* issue from the new build or
review, not a resurrection of the old one.

The one real failure mode — a role marking `resolved` without addressing anything — is
closed mechanically rather than by wording: **the resolution names the successor task
(`fix_kind` plus `task: S8-T2`), and `wf stage check` errors when that id is not in
`stage.yaml`.** `fix_kind: no_change` with a reason covers the legitimate nothing-to-do.
That same field is what lets the driver map a blocked task's branch onto its successor
(§13).

### Two things that sit outside the role

- **Adequacy gate 2 is the driver's.** At every stage close, set-difference the
  capability's SYS-TC set against the register; empty → dispatch `wf-adequacy`
  full-promise → drain. Pure mechanism, no judgment, so it is not in a skill.
- **Width cannot be gated.** `limits.tasks_per_stage` is an upper bound; a lower bound
  would be wrong, since some work genuinely is one task wide. Track mean stage width as
  a telemetry signal — a trend toward 1 means the role is cutting too conservatively and
  design cost is compounding.

## 12. The driver

**`designing` stays a first-class phase.** `sprint_start → designing → stage_run →` back
to `designing`, or forward to `closeout`. It is not folded into a compound `stage_loop`:
`designing` is where the escalation gate and the work-exhaustion pause live, and
`resume_ruling` routes into a *named* phase. Resumability is the property the whole driver
is built on, and a compound state cannot say which sub-step a run was suspended at.

**Heavy checks run at every stage close.** `commands.stage_check` fires ~17× per
s1-equivalent instead of 4×, making stage close the dominant serial term and firing
`wf-stage-repair` about four times more often. Accepted deliberately: catching an
integration break at the stage that caused it is worth more than the wall clock, and the
per-task `commands.preflight` gate is not a substitute for cross-task integration.

**A red stage check halts.** Repair up to `STAGE_REPAIR_ATTEMPTS`, then
`Halt("stage_check_red")` **carrying the stage-check log path**. The stage has already
merged when the check runs, so red means the sprint branch is broken; and under §9 the
branch may already be pushed, so reverting means a force-push or a revert commit — worse
machinery than a halt. A broken integration state is a *true* halt, and s1's evidence says
it is rare (2 repair dispatches over 4 boundaries, zero halts).

**Escalation pauses the whole loop**, unchanged. Letting the loop pick unrelated work
while a ruling is outstanding drifts it away from the thing it just said needed a decision
and queues `decision_prep` files.

**Work exhaustion ships first.** The role reports exhaustion at a cut; the driver ships
whatever stages have merged as a PR, then exits. Otherwise merged, reviewed, green work
strands on a branch nobody is looking at.

**An empty disk has two meanings, and the driver must tell them apart.** §11's Phase 3
ends its dispatch deliberately after authoring a capability's scenario set — leaving
exactly the empty `paths.stage` that work exhaustion leaves. Routing both the same way
halts a loop that has work left to do, and makes the behaviour look non-deterministic to
whoever restarts it: whether the first dispatch of a run stops depends on whether the role
happened to pick a capability or a learning.

The signal that separates them is the work-set's **scenario count**: `wf workset check`
before and after the dispatch. Grown → the role did Phase 3's job, so dispatch it again to
cut. Unchanged → genuine exhaustion. Bounded at `SCENARIO_ROUNDS` so a role that only ever
authors cannot spin. Derived from an artifact, never from the role's prose.

**Cadences:**

| what | when | why |
|---|---|---|
| discover brief refresh | per sprint | a repo-wide map one stage's merge rarely moves; drills carry the freshness |
| `wf-retrospective` | per sprint | it is what drains `paths.telemetry`, so running it less often inflates its own read |
| adequacy gate 2 | every stage close | set-difference the capability's set against the register; empty → dispatch → drain |
| `closeout:` config | `[wf-retrospective, ship]` | gate 2 is pure mechanism with no ordering to configure, and CLAUDE.md forbids a knob nothing honors |

**Drill-cache staleness is derived, not judged.** Each digest header carries `taken_at:
<sha>` and `targets: [paths]`; a digest is stale when any target path changed since that
sha, checked at stage close. The cache was designed for one cut per sprint and its only
current rule is an LLM judgment (*"if a digest looks stale against the current tree,
re-drill"*). With the tree moving under it every stage, that judgment has to become a
check — mechanical-over-LLM applied to freshness.

**The prune must skip `adequacy-*`.** Adequacy digests share `paths.drill_cache`, and
`adequacy.consecutive_inadequate` derives a capability's park count by *counting* them. A
literal prune-every-stale-digest would silently reset every capability's road to being
parked — the three-strikes rule in §4 would never fire, and a promise nobody can decompose
would be re-designed forever.

## 13. Blocked tasks

### `tasks_blocked` stops existing

`_blocked_gate` (`tools/driver/increments.py:573`) halts because *"a blocked task cannot
be reopened inside the sprint, and `propagate-blocks` dooms everything depending on
it."* With no `depends_on`, a blocked task dooms nothing: the stage closes with the N−1
that merged, and the blocked work re-enters at the next cut — one dispatch away, not a
sprint away. `propagate-blocks`, `blocked-tasks`, `unblock-task` and `_blocked_gate` all
lose their subject.

That was **12.8 of s1's 26.6 dead hours**, the largest single line item — deleted as a
mechanism rather than recovered from. §10's claim that this revision does not touch dead
clock holds for the *remaining* 13.8 h, not for this.

### The re-entry channel

Two block causes survive, and **neither writes a design issue today**: the build wrote no
artifact after `REDISPATCH_ATTEMPTS` (`increments.py:323`) and review rejected at every
allowed attempt (`increments.py:335`). So §11's "the blocked work re-enters at the next
cut" had no channel at all.

**The driver raises a design issue at both `_block` sites**, via `issues.record()` — which
already exists as its channel for issues it detects itself ("a red gate, a conflicted
merge") — with the block reason as the summary. The role then reads it at Phase 1 like any
other issue: no new mechanism, no new read, and it survives a restart because it is on
disk.

`complete-sprint` therefore **copies** `paths.design_issues` to the archive rather than
moving it. A blocked task's issue is the channel into the *next* cut, and that cut may be
in the next sprint; moving the file at ship would drop every open issue on the floor.
Resolved entries still drain through `orchestrate sweep-design-issues`.

### Salvaging the work

The blocked branch holds up to three attempts' commits, and §11's rule gives the driver
its mapping for free: the role's issue resolution names the successor task.

1. **Cut the new task's worktree from the old branch**, then rebase onto the current
   sprint tip: `git worktree add -b S8-T2 <path> <old-branch>` followed by a rebase.
2. **On conflict, abort and fall back to a fresh worktree** from the tip. Carrying the old
   work is opportunistic, not required — spending a `wf-stage-repair` dispatch to salvage
   a build review already rejected three times is bad economics. `_repair_merge` exists
   for merges to the sprint branch, where the work is approved and *must* land; this is
   not that.
3. **The envelope carries `prior_attempt`** — the branch and the block reason. Without it
   `wf-build`'s Red phase breaks silently: its rule is *confirm the tests FAIL*, and
   prior-attempt code may already make a new test pass, which the agent would then
   misdiagnose as a vacuous test and restructure a perfectly good one.

## 14. Build order and migration

`loop-redesign.md`'s governing ruling carries: **end-state only, no half-way points**, the
failure mode being wholesale revert with learnings carried back. A half-migrated hierarchy
with stages and increments coexisting is worse than either end, and dems has just closed
s1 cleanly with nothing in flight — the cleanest cut point available.

Order: **config template first**, then `loop-redesign.md` §10.1 — CLI/checks (TDD) →
driver (TDD) → roles → templates → render/install → validation-repo smoke. The template
moves to the front because every path and limit rename ripples into both the CLI and the
driver, and per CLAUDE.md it is the single source of defaults: settling it first means one
rename pass, not three.

**dems migration is a config edit and a re-install.** No data migration: scenario sets are
authored lazily when a capability is taken up, and nothing is in flight. The one behaviour
that makes this safe is §4's seeding rule — Phase 3 starts a capability's set from the
register, not from a blank page.

The whole edit, diffed against the pilot's live config:

| | |
|---|---|
| add | `paths.stage`, `paths.pr_body`, `driver.max_stages_per_sprint: 4`, `limits.tasks_per_stage: 10`, `id_counters.stage: 0` |
| remove | `paths.design_slice`, `paths.sprint`, `limits.increments_per_sprint`, `limits.tasks_per_increment`, `driver.agent_cmd_overrides.wf-tl` |
| change | `closeout: [wf-retrospective, ship]` (drop `adequacy` — it is driver logic now); `driver.max_unmerged_sprints: 1 → 3` |

`CAPABILITIES.yaml` and `LEARNINGS.yaml` are untouched: an entry with no `system_tests`
set is legal and simply means the entry has not been taken up. **Check for a live
`wf-driver` before installing** — an install under a running loop corrupts it.

Scale, measured: **~59 of 199 driver test functions** assert on the deleted surface, plus
both fixture modules (`support.py`, `fakes.py`) that gate them; `slice_test.sh` (561
lines) is deleted or reborn as `stage_test.sh`; `sprint_test.sh` (879 lines, 68 affected
assertions) is rewritten; `pipeline_test.sh` loses ~55 of 178 assertions.

## 15. Residual risk

- **Context budget is the merge's live risk.** wf-tl already peaks at 267 k / 281 k, the
  highest of any role, *with the slice handed to it*; the merged role also grounds on the
  durable set and reads source. Against `loop-redesign.md` §11's ≈100 k target: measure
  `context_max` on the first dems stage and treat >150 k as a design failure, not a
  tuning knob. The counter-case is strong — the 692-line slice, the 3 k-line accumulator,
  and 8–9-task decomposition all disappear — but it is a prediction, not a result.
- **Three deletions are replaced by one behaviour.** Claimed scope, interface contracts
  and the design narrative all resolve to "read merged source". That works only if the
  role genuinely reads the tree rather than the plan. If it does not, this trades a stale
  forecast for no forecast, which is worse. It is the first thing dogfooding should check.
- **Stage close is now the dominant serial term** (§12), and `wf-stage-repair` fires ~4×
  more often. If stage-check wall time turns out to gate the loop, the choice to run it
  every stage is the first knob to revisit — not the horizon.
- **Width cannot be gated** (§11). Watch mean stage width; a trend toward 1 means design
  dispatches are compounding against a chain the role is not cutting around.
