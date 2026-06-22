# wf-swa — default mode

Build the sprint from the design slice: author the acceptance criteria that make each slice
requirement testable, then decompose them into a per-task dependency graph in `$SPRINT`. The
requirements and component boundaries are fixed — you consume them, you do not change them;
the acceptance criteria and the task breakdown are yours.

**Component boundaries are law.** Every file a task touches belongs to that task's declared
component. Cross-component work is separate tasks.

## Phase 1 — Ground

1. Read `$DESIGN_SLICE` — one **buildable increment** wf-sa cut from the design backlog:
   the component requirements (each with its owner and driver), the architecture moves, and
   the governing ADRs. Its requirements are your whole scope. **HALT if it is absent** — ask
   the user to run `wf-sa` first.
2. Read `$BRIEF` for system shape, and the relevant `$ADRS` for the rationale you must
   respect when writing `implementation_notes`.
3. Read the **source** of every component the slice's requirements name. The slice has
   already bounded the work to these components — their code is your depth; read it
   directly rather than working from a summary.

## Phase 2 — Author the acceptance criteria

**Load `references/task-contract.md` before writing any criterion or contract.** For
each requirement in the slice, write the **acceptance criteria** that prove it: each a
concrete, file-level testable condition with named inputs and expected outputs, given
an id `REQ-N.AC-M` and tracing to its requirement. The bar: the build phase must be
able to write a failing test from the criterion alone.

A requirement whose failure or boundary behavior the criteria don't cover is an
incomplete set — add the missing criterion. A requirement you cannot make testable
from the source is a flag to the SA, not a guess.

## Phase 3 — Decompose into tasks

Cut the criteria into tasks — each one cohesive, testable unit of work that satisfies
one or more requirements and carries their criteria. Every criterion lands in exactly
one task; every requirement is fully covered across the task set.

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

## Phase 5 — Present & write

1. Present the task list and the dependency shape (what runs in parallel, what blocks
   what) to the user for a sanity check. The slice's scope was already approved at the
   SA gate, so this is a decomposition check, not a scope gate.
2. Write `$SPRINT` from `assets/sprint.yaml.tmpl`. It is transient and gitignored —
   there is nothing to commit; the build pipeline consumes the working-tree file
   directly.
3. **Clear `$DESIGN_SLICE`.** You have refined it into the sprint, so drain your input —
   delete the slice (it is transient and gitignored; the backlog it was cut from persists).
4. Report: the task count, the dependency shape, and the suggested next step.

## Halt conditions

Stop and surface to the user if:

- `$DESIGN_SLICE` is absent (run `wf-sa` first).
- A component's source directory does not exist at the path the brief names — the
  structure has drifted; ask for a discover re-run.
- A requirement cannot be made testable, or its criteria cannot be turned into a task
  without crossing a component boundary — flag to the SA.
- The tasks form a dependency cycle that cannot be broken by splitting or merging.
