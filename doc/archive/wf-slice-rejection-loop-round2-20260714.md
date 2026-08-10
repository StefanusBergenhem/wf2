# The slice-rejection dead-end, round 2 — it recurred in 111 minutes (2026-07-14)

**Source:** dems, second `/wf-orchestrate` run on 2026-07-14, against **slice A** (the wf-sa re-cut,
commit `a7eec0f`) authored specifically to resolve round 1's rejection. The run halted in
`preparing` having built nothing. Again.
**Predecessor:** [`wf-orchestrate-slice-rejection-gap-20260714.md`](wf-orchestrate-slice-rejection-gap-20260714.md)
— findings F1–F4, filed 111 minutes earlier the same day.
**Scope:** the `wf_friction` channel. The dems-side findings were hand-transcribed (again) to
`dems/doc/design/compliance-slice-a-swa-escalation-20260714-round2.md` — that transcription **is
itself F1 recurring.**

Legend: **[DUP]** second data point on a finding already filed · **[NEW]** not previously logged ·
**[OK]** working as intended.

---

## TL;DR

Round 1's note ended with a proposed minimum-viable fix: *"default mode must persist its blockers to
disk before it halts."* It was not applied — reasonably, it was filed 6 minutes before the human
started a 92-minute re-design. **The same loop then ran again, end to end, and produced the identical
three failures.** This note's value is not new analysis of F1–F4; it is the evidence that they are a
**cycle, not an incident**, plus two findings round 1 could not have seen from a single data point.

The headline is F5. Round 1 was "the SA's slice was wrong." Round 2 is **"the SA's *fix* was wrong,
in a way the SA had no way to test before the human ratified it."** That is a loop with no
convergence check, and it has now cost 2h18m of wall clock and two full SWA decomposition passes to
build zero tasks.

| # | Sev | One-line | Status |
|---|-----|----------|--------|
| **F5** | **HIGH** | **[NEW]** The re-design loop has no convergence check — the human ratifies a design whose only real test (decomposition) runs *after* ratification | New |
| **F6** | **HIGH** | **[NEW]** wf-swa's two hard rules collide on any cross-package seam with no tie-breaker — by the SWA's own account, the root of *both* round-2 blockers | New |
| F1 | **HIGH→CRIT** | **[DUP]** Default-mode escalation is prose-only. Second hand-transcription in one day; 194K tokens of analysis existed only as chat text | Recurred |
| F2 | **HIGH** | **[DUP]** No route from `preparing` rejection to wf-sa; halt still misdescribes it as "wf-swa could not build a sprint" | Recurred |
| F3 | MED→**RESOLVED-BY-WORKAROUND** | **[DUP]** The human-gated exit has calcified into project convention — the gap is now invisible | Recurred, worse |
| F4 | LOW | **[DUP]** `sweep-transients` still silently leaves the stale Jul-12 `design-issues.yaml`; a second stale DI file also surfaced | Recurred |

---

## The full timeline — one day, two rounds

| Time (UTC) | Actor | Duration | Outcome |
|---|---|---|---|
| 15:30:36 | `wf-orchestrate` | 8m | halted — no sprint |
| 15:31:18 | `wf-swa` (default) | **6m** | escalated — 5 blockers (B1–B5), prose only |
| ~15:40 | **human** | — | asked to route to wf-sa fix mode; orchestrator declined with evidence; human ran `/wf-sa` manually |
| 15:54:21 | `wf-sa` (default, interactive) | **92m** | completed — re-cut into slice A + slice B, commit `a7eec0f` |
| 15:55–15:59 | `wf-drill` ×3 | 2m | completed |
| 17:29:41 | `wf-orchestrate` (this run) | 18m | **halted — no sprint** |
| 17:30:15 | `wf-swa` (default) | **17m**, 194K tokens, 58 tool calls | **escalated — 2 new blockers, prose only** |
| 17:47:55 | — | — | escalated to human. Zero tasks built. |

**2h18m elapsed. Two SWA passes. One 92-minute SA re-design. Zero task contracts written.**

The gate is not the problem — see [OK] below; it caught real defects both times. The problem is that
every lap of this loop costs a 92-minute interactive human session, and **nothing tests the output of
that session until the next lap.**

---

## F5 — [NEW · HIGH] The re-design loop has no convergence check

**What.** Round 2's *both* blockers are on **REQ-141** — a requirement that **did not exist before
round 1**. wf-sa minted it during the 92-minute re-design to answer round 1's Finding A (the
`allowedValues`/`sortOrder` data-loss bug). The fix introduced the next rejection.

Specifically, REQ-141 ("leave unchanged every field the request does not supply") is:
1. not satisfiable by its declared owner alone (`SortOrder int` can't express absent → the atomic
   edit set crosses into `internal/handlers` → a repository-only task fails `go build ./...`); and
2. a silent regression of a **working** path (`SettingsPage.tsx` clears `unit` *by omission* today;
   under REQ-141 that becomes a no-op) that no requirement owns.

Neither is exotic. Both are the kind of thing decomposition finds in minutes — and did, twice.

**Why it bites.** Look at the order of operations in wf-sa default mode:

- **Phase 4 is "the interactive core"** — *"WAIT for the human to ratify or redirect."*
- Decomposition — the **only** step that empirically tests whether the design is buildable — happens
  in `wf-swa`, on the *next* orchestrate run, **after** the human has already ratified.

So the human ratifies buildability they cannot assess, and the SA gets its only real feedback one
lap later, from a subagent whose findings are prose (F1) on a path with no route back (F2). **The
loop's correction signal is slower than the loop.**

Round 1's B3/B4 were "REQ-122's update half / REQ-132 have no repository owner." Round 2's R2-1 is
"REQ-141 has no satisfiable owner." **The SWA itself flagged the repeat**: *"Same class as the
previous cut's B3/B4."* Two consecutive re-designs failed on the same category of defect —
requirement ownership vs. the real atomic edit set. That is a systematic blind spot in the
SA→SWA handoff, not bad luck.

**Proposed fix — options, cheapest first:**

- **(a) A decomposition dry-run inside wf-sa Phase 4.** Before the human ratifies, dispatch wf-swa
  read-only against the draft slice: "can every requirement be owned by one component, and is each
  atomic edit set inside that component?" This is a ~6–17 minute autonomous check gating a
  92-minute interactive session. The economics are not close.
- **(b) An ownership-satisfiability rule in wf-sa's requirement authoring.** Every requirement must
  name an owner **and** assert the atomic edit set fits inside it. Both rounds' blockers are exactly
  this predicate returning false. Cheap, static, no new dispatch — but relies on the SA reasoning
  about compile-time ripple, which is what it demonstrably got wrong twice.
- **(c) Accept the loop, but make each lap cheap.** If (a)/(b) are too invasive, then F1 and F2 stop
  being polish and become the whole mitigation: at minimum the human must be handed a file, not a
  scrollback.

**(a) is the recommendation.** It attacks the actual defect — that the design's only test runs after
its ratification gate.

---

## F6 — [NEW · HIGH] wf-swa's two hard rules collide with no tie-breaker — and it caused round 2

**What.** Two references, both stated as law, in direct contradiction on any cross-package seam:

> `references/default-mode.md`:
> *"Component boundaries are law. Every file a task touches belongs to that task's declared
> component. Cross-component work is separate tasks."*

> `references/task-contract.md` (Scope consistency):
> *"A signature or field change carries its consumers … the atomic edit set is the origin file plus
> its dependents."*

**In a single-module Go repo, the atomic edit set *is* cross-component.** Changing a
`internal/repository` type or signature breaks `internal/handlers` at compile time; per-task
preflight runs `go build ./...`; so splitting the change makes each half fail its own gate, and not
splitting it violates boundaries-are-law. Neither reference yields, neither names the
additive-method escape hatch, and nothing says which wins.

**Why this is not just a papercut.** From wf-swa's own telemetry, unprompted:

> *"Neither reference says which rule wins, nor names the additive-method escape hatch; **this
> consumed most of the session and is the root of both escalated blockers.**"*

That is the SWA reporting that a **toolkit defect**, not the slice, drove a 17-minute / 194K-token
session to escalation. R2-1 is *partly a wf bug*. Had the tie-breaker been documented — "the atomic
set wins; declare a primary component and list the ripple", or "prefer an additive method; here's
when a twin is acceptable" — the SWA plausibly decomposes REQ-141 without a human at all. It found
the additive-twin option itself and correctly refused to *choose* it, because choosing is an
architecture call the toolkit never told it it could make.

**Note this is the second consecutive `skill_gap` from wf-swa default mode on the same day, both
about halt conditions that don't cover the real case.** Round 1's:

> *"`default-mode.md` mandates copying the slice's Interface contracts verbatim and forbids changing
> them, but its Halt conditions list has no entry for 'the slice's interface contract fixes a shape
> that contradicts the source' — I had to route a structural id-type defect through the 'flag to the
> SA' analogy from the untestable-requirement condition."*

Same shape: the SWA hits a real, legitimate case; no halt condition covers it; it improvises a route.
**wf-swa's halt-condition list is under-specified for slice defects generally** — worth a pass in its
own right, not two point fixes.

**Proposed fix.** State the tie-breaker explicitly in `task-contract.md` *and* `default-mode.md`, and
name the additive-method escape hatch with the criteria for when a twin is acceptable vs. when the
old method must die. Then add the missing halt conditions (contract-contradicts-source;
owner-cannot-satisfy).

---

## F1 — [DUP · HIGH → **CRITICAL**] Prose-only escalation, twice in one day

Round 1 filed this as HIGH with a minimum-viable fix. **Round 2 reproduced it exactly**, and the
second data point is worse than the first:

- wf-swa round 2 burned **194,336 tokens across 58 tool calls over 17 minutes** — measuring `tsc`
  fixture breakage across 9 test files, decoding real struct tags, reading `door_write.go`,
  `import.go`, `SettingsPage.tsx`, tracing `validate_property_defs.sh` into preflight. **All of it
  existed only as return prose.**
- Its telemetry row records `outcome: escalated` and, in `wf_friction`/`repo_observation`, *two*
  findings. **Neither field holds the blockers.** There is no field that could. Once the context
  window is gone, the analysis is gone.
- A human had to ask, a second time in one day, for a hand transcription — which is why
  `compliance-slice-a-swa-escalation-20260714-round2.md` exists.

**The recurrence is the argument.** A gap that reproduces within 111 minutes, with no code change in
between, is not an incident — it's the default behaviour of the path. Every future slice rejection
will do this. **Upgrading to CRITICAL** and re-asserting round 1's minimum: *default mode must
persist its blockers to disk before it halts.* Nothing else on this list is cheaper or more certain
to pay off.

**One addition round 2 supplies:** the artifact needs to carry more than blockers. wf-swa's return
also contained the *decomposable* findings — the additive `ListDoorIDsByExternalIDs` signature that
unblocks REQ-142/132, the `entityTypes?` optionality measurement, the `validate_property_defs.sh`
gate bomb, the corrected `migration_helpers_test.go` reasoning. That is the SWA's **working notes**,
and they make the next re-cut dramatically cheaper. Prose-only throws them away too. The artifact
should have a `working_notes` section, not just `blockers`.

---

## F2 — [DUP · HIGH] Still no route; the halt still misdescribes what happened

Unchanged from round 1. §1.1's *"still absent → **HALT and report** (wf-swa could not build a sprint
from the slice)"* fired again, and the parenthetical is wrong again: wf-swa **could** decompose most
of slice A and **refused** to launder two spec defects into contracts. It even handed over the
worked-out decomposition for everything else.

Round 2 adds one data point on the *shape* of the fix: the orchestrator's report to the human had to
be assembled **entirely from the subagent's prose**, in direct tension with the skill's own hard
constraint (*"Route on the helper's verdict, never on a sub-agent's prose"* / *"pipe every
sub-agent's output to `/tmp` … never echo it into your own context"*). Followed literally, this run
would have discarded the findings and reported "wf-swa could not build a sprint" — a statement that
is both useless and false. **The orchestrator can only do the right thing here by violating its own
rule.** That tension is now confirmed on two independent runs; it should be resolved in the skill
text, not left to the agent's judgement.

---

## F3 — [DUP · MED] The workaround has calcified into project convention — which is how gaps become permanent

Round 1 asked: *"is this a gap or accepted-by-design?"* Round 2 answers it empirically, and the
answer is uncomfortable.

Between rounds, the human recorded a durable project memory:

> *"wf-sa fix mode takes one DI, not a slice re-design — a rejected slice routes to `/wf-sa` default
> mode, run interactively."*

So in round 2 the orchestrator **did not even attempt** the fix-mode route. It halted and escalated
immediately, correctly, per convention. **The dead-end is now documented as the intended path.**

That is worth flagging precisely because it looks like resolution. The toolkit gap has been absorbed
into human process: the loop still costs a 92-minute interactive session per lap, the transcription
is still manual, and now nobody will re-report it because it's "how it works." **A workaround that
stops generating friction reports stops generating fix pressure — while costing exactly as much as it
did before.**

Round 1's position — that a defective slice implicates decisions the human owns, so a human-gated
exit may be *correct* — still stands, and round 2 supports it (both blockers genuinely need a
ruling). But "the exit is human-gated" must not be conflated with "the exit is fine." The exit is
currently: findings lost unless transcribed by hand (F1), described wrongly (F2), reached only after
a full SWA pass that could have been a dry-run (F5), and partly caused by an undocumented rule
collision (F6). Make the exit clean, then it is by-design. Today it is a dead-end wearing a
convention.

---

## F4 — [DUP · LOW] `sweep-transients` — same silence, now two stale files

Reproduced verbatim: kickoff `sweep-transients` → `{"deleted": [], "skipped": []}` while
`.wf/transient/design-issues.yaml` (2026-07-12, three entries, all `status: resolved`, from the
sprint shipped as dems PR #207) sat untouched on disk. Reported in **neither** list, both runs.

**New this round:** a *second* stale artifact is also present and equally invisible —
`.wf/transient/design-issue-op-display.yaml` (2026-07-11). So the residue is accumulating across
sprints exactly as round 1 predicted, and `complete-sprint` clears neither.

Still LOW (`dispatch-fix` only acts on `open`), still worth fixing **before** F1's artifact lands in
that file.

---

## What worked — recorded so it isn't re-litigated

- **[OK] The `preparing` gate, again.** Two rejections, two sets of real defects, zero task contracts
  written, zero rework. Round 2's catch cost one subagent and 17 minutes to prevent a sprint built on
  a requirement that breaks a working user path. **The gate is the most valuable thing in this
  report** — none of F1–F6 is an argument against it. They are arguments that its *output* is
  mishandled.
- **[OK] wf-swa's grounding discipline, again.** It measured rather than asserted: ran `tsc` to count
  31 breaking fixture sites across 9 files, traced `validate_property_defs.sh:15` into the default
  preflight's `set -e` path, checked which `migration_helpers_test.go` helpers actually have external
  consumers (and **corrected round 1's stated reason** for the same trap), verified
  `doors_external_id_source_system_key` is non-partial. It also **volunteered the decomposition** for
  everything unblocked, unprompted — that is the behaviour F1 is throwing away.
- **[OK] The orchestrator's routing discipline.** Phase resumed correctly from `preparing`;
  `reclaim-stale` clean; sprint re-checked on disk before halting; telemetry recorded. It did not
  invent a route to wf-sa that doesn't exist, and did not edit the slice or sprint to force progress.
- **[OK] Round 1's note predicted round 2.** F1's *"once that context window is gone, so are the
  findings"* and F4's *"the file accumulates without bound"* both came true within two hours. The
  note was right; it just wasn't actioned before the next lap. That is the argument for actioning it
  now.

---

## Recommendation

Ordered by (cost to fix) ÷ (cost of not fixing):

1. **F1 — persist default-mode blockers + working notes to disk.** Smallest change, certain payoff,
   already reproduced twice. Do this one regardless of everything else.
2. **F6 — document the boundary-vs-atomic-edit-set tie-breaker** and the additive-method escape
   hatch, then sweep wf-swa's halt conditions for the missing slice-defect cases. This is a *pure
   toolkit defect* that demonstrably caused a live escalation.
3. **F5 — add a decomposition dry-run to wf-sa Phase 4** (before human ratification). Biggest
   structural win: a ~15-minute autonomous check gating a 92-minute human session.
4. **F2 — branch §1.1 on the F1 artifact** and fix the halt's wording.
5. **F4 — make `sweep-transients` honest** about the DI file before F1's artifact lands in it.

Suggest CANDIDATES entries for F5 and F6; F1/F2 are straightforward fixes to already-filed findings.

---

## Meta — this note's predecessor is untracked

`doc/notes/wf-orchestrate-slice-rejection-gap-20260714.md` is currently `??` in git (untracked), as
is this file. The record of a gap that has now reproduced twice exists only in an uncommitted working
file. Worth committing both.
