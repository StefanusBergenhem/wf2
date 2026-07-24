# wf-spec-fix — slice re-cut

You classified the issue as a **slice re-cut**: the design slice cannot be decomposed as
written (the entry carries `scope: slice`). Its `blockers[]` are the whole set to resolve, and
they interact — resolve **all** of them in one run, never one at a time. This runs in
`preparing`, before any sprint branch exists.

Execute **wf-sa's design Process** with the deltas below. **Read `{{WF_SKILLS_DIR}}/wf-sa/SKILL.md`**
and run its **Process**, Phases 1–3, 5, and 6. Wherever it names a `references/…` file, read it
under `{{WF_SKILLS_DIR}}/wf-sa/references/`. You run **autonomously**: **Phase 4 does not run**,
and no human aligns the design.

## Grounding — replaces Phase 1

Read each blocker's `requirement`, `summary`, `evidence`, and `needs`; the
`paths.design_backlog` design the slice was cut from; `paths.design_slice`; and each ADR in
`paths.adrs` whose `governs_components` names a component a blocker implicates. Reuse what the
entry's `working_notes[]` already settle rather than re-deriving them. Drill what the blockers
implicate (wf-sa's **Scouting & the drill-cache**). **Derive the requirement register and read
its in-scope entries**, as Phase 1's grounding does. Reconcile nothing, and drain nothing.

## Deciding — replaces Phase 4

Take every decision yourself and record each in the decision report (SKILL.md Step 4):

- **Non-obvious decisions and ADRs** — reshape boundaries and mint requirements as the blockers
  demand; write each ADR that meets `adr-rules.md`'s threshold.
- **Assumptions** — where a driver's wording admits more than one reading, pick the reading,
  mark it **CONFIRMED** in the slice, and record the chosen-vs-rejected reading in the report.
  `wf slice check` passes only on CONFIRMED assumptions; you are the authority that confirms
  them.
- **Supersessions** — where the re-cut retires or changes a shipped requirement (one the
  register tags `[REQ:<id>]` / `[SYS-TC:<id>]`), record it in the slice's **Supersedes** list
  and name it on the report's **Superseded** line.

The **only** decision you do not take is a change to the driving capability itself — that is the
capability halt (SKILL.md Step 2).

## Record & commit — Phase 6, adapted

Run Phase 6 steps 1–3: finalize the ADRs (its step 1 applies `adr-rules.md`'s amend-vs-supersede
rule to each), amend the backlog design in `paths.design_backlog` **in place**, and re-cut
`paths.design_slice`, including its `wf slice check` gate. Do **not**
close the `di_id` entry in step 3 — SKILL.md Step 4 does that. A gate **failure** means the
re-cut still rests on something unresolved: treat it as the over-scope halt (SKILL.md Step 2) —
write `paths.decision_prep` and halt.

Then **commit, skipping Phase 6 step 4's human confirm** — no sprint branch exists yet, and the
orchestrator gates it on a clean working tree, so leaving the durable spec uncommitted blocks the
run. Stage and commit exactly `paths.adrs` + `paths.design_backlog` (never `paths.design_slice`
— it is transient). Report what you committed.

Return to SKILL.md Step 4 to write back `fix_kind: slice_recut`, mark the entry resolved, and
append the report.
