# wf-swa — default mode

Build the sprint from the design slice: author the acceptance criteria that make each slice
requirement testable, then decompose them into a per-task dependency graph in `$SPRINT`. The
requirements and component boundaries are fixed — you consume them, you do not change them;
the acceptance criteria and the task breakdown are yours.

**Component boundaries are law.** Every file a task touches belongs to that task's declared
component. Cross-component work is separate tasks.

## Phase 1 — Ground

1. Read `$DESIGN_SLICE` — one **buildable increment** wf-sa cut from the design backlog:
   the component requirements (each with its owner and driver), the **system test cases**
   (wf-sa wrote them; you plan each into a task), the architecture moves, the governing ADRs,
   and the **risks wf-sa flagged for you** (fragile seams, ordering constraints, external deps
   — they shape your decomposition). Its requirements are your whole scope. **HALT and report
   if it is absent** — it is wf-sa's output.
2. Read the relevant `$ADRS` for the rationale you must respect when writing
   `implementation_notes`.
3. Read the **source** of every component the slice's requirements name — locate each in
   the repo. The slice has already bounded the work to these components; their code is your
   depth, read it directly rather than working from a summary.

## Phase 2 — Author the acceptance criteria

**Load `references/task-contract.md` before writing any criterion or contract** — it holds
the AC rules (testable-from-source, the `REQ-N.AC-M` id scheme, failure/boundary
completeness, staying within the requirement) and governs the contract you build in the
later phases. For each requirement in the slice, author the acceptance criteria that prove
it, per those rules.

## Phase 3 — Decompose into tasks

Cut the criteria into tasks — each one cohesive, testable unit of work that satisfies
one or more requirements and carries their criteria. Every criterion lands in exactly
one task; every requirement is fully covered across the task set.

For each task, author its **complete** contract per `references/task-contract.md`:
`files_to_touch`, the `testing_mandate` (unit positive + negative per target; an integration
test per real seam — external dep or cross-component wiring), `out_of_scope`,
`implementation_notes` (source patterns + governing ADRs), and `serves`.

**Plan the slice's system test cases.** For each `SYS-TC-<n>` case wf-sa wrote, add an e2e
task whose `system_tests` is that case. The case `Covers` a **capability**, so its
`depends_on` names the tasks building the requirements **driven by that capability** (read
the drivers off the slice's component requirements) — putting it downstream of the assembled
path. It exercises (imports) the components without owning them; the build stamps
`[SYS-TC:SYS-TC-<n>]` in the e2e test.

**Sizing.** Keep a task to roughly **≤ 5 files** and **≤ 250 lines** of change. A task
larger than that hides gaps and costs the build/review cycle its leverage — split it.
A task far smaller than that (a one-line change standing alone) usually belongs merged
with an adjacent one; the per-task dispatch overhead is roughly fixed. Group work that
cohesively belongs together.

## Phase 4 — Order into a dependency graph

Give each task a `depends_on` list naming the tasks that must land first (a shared
interface, a migration, a type another task imports). Tasks with no edge between them
run in parallel. The graph must be acyclic — a cycle is a decomposition error; resolve
it by splitting a task or merging two.

## Phase 5 — Write

1. Write `$SPRINT` from `assets/sprint.yaml.tmpl`. It is transient and gitignored —
   there is nothing to commit; the build pipeline consumes the working-tree file
   directly.
2. **Clear `$DESIGN_SLICE`.** You have refined it into the sprint, so drain your input —
   delete the slice (it is transient and gitignored; the backlog it was cut from persists).
3. Return a summary: the task count and the dependency shape.

## Halt conditions

Halt and report with outcome `escalated` if:

- `$DESIGN_SLICE` is absent (it is wf-sa's output).
- A component named in the slice cannot be located in the source — the structure has
  drifted (needs a discover re-run).
- A requirement cannot be made testable, or its criteria cannot be turned into a task
  without crossing a component boundary — flag to the SA.
- The tasks form a dependency cycle that cannot be broken by splitting or merging.
