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
test per real seam — external dep or cross-component wiring), `out_of_scope`, and
`implementation_notes` (source patterns + governing ADRs). Copy each covered
requirement's EARS statement **verbatim** from the slice into the task's `requirements`
(one `{id, statement, serves}` entry per id in `covers` — `serves` is that requirement's
own driver, read off the slice), and set the task's `serves` to the union of those
drivers, never collapsed to one "primary". When a task builds a component or widens a
seam the slice's **Interface contracts** section fixes a shape for, copy that contract
**verbatim** into the task's `interface_contract` — the build implements the agreed shape,
never one it discovers.

**Fold in the slice's Supersedes list.** For each superseded id the slice's **Supersedes**
section carries, locate its proving test file(s) mechanically — grep the test tree for
`[REQ:<old-id>]` / `[SYS-TC:<old-id>]` (or derive them via
`python3 <paths.tools>/reconcile/register.py --tests <test-root>`) — never guess. Add those
files to `files_to_touch` of the task covering the successor requirement (a dedicated
removal task when the entry has no successor), with an explicit note in its
`implementation_notes`: update or delete the old proving test and its tag — a superseded
tag must not survive the sprint.

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

**Two tasks whose `files_to_touch` overlap must have an edge between them** — unordered,
they land in the same parallel stage and edit the same file in separate worktrees, which
collides at the stage merge. `wf sprint check` fails (C10) on any such pair; add the edge
(or, better, factor the shared file into its own upstream task so the leaf tasks partition).
When numbered artifacts are pre-allocated across tasks (migrations, ordered fixtures), give
them numbers that ascend with merge order — a lower number merging after a higher one
replays out of order against a persistent store.

## Phase 5 — Write and gate

1. Write `$SPRINT` from `assets/sprint.yaml.tmpl`. Mint its top-level `sprint_id`
   as `sprint-<yyyymmdd>-<short-scope-slug>` — today's date plus a short slug of the
   sprint's scope, `[a-z0-9-]` only. The sprint is transient and gitignored — there
   is nothing to commit; the build pipeline consumes the working-tree file directly.
2. **Gate: run `python3 <paths.tools>/cli/wf sprint check`. Do not proceed until it
   reports `verdict: pass` (exit 0).** It checks the sprint against the slice — every
   slice requirement covered, every criterion carried by exactly one task and referenced
   by a test (or gate-verified via `verified_by`), every mandated test with a test-file
   home in `files_to_touch`, every requirement's driver in the task's `serves`, every
   SYS-TC carried by an e2e task, no UNCONFIRMED assumption, an acyclic DAG. On an error finding,
   fix the decomposition in `$SPRINT` and re-run. A finding you cannot resolve without
   minting or changing a requirement is a spec defect — halt and escalate to the SA (see
   Halt conditions), never invent a criterion to silence it.
3. Return a summary: the task count, the dependency shape, and the gate verdict. Leave
   `$DESIGN_SLICE` in place — it is drained at sprint close, not here.

## Halt conditions

Halt and report with outcome `escalated` if:

- `$DESIGN_SLICE` is absent (it is wf-sa's output).
- A component named in the slice cannot be located in the source — the structure has
  drifted (needs a discover re-run).
- A requirement cannot be made testable, or its criteria cannot be turned into a task
  without crossing a component boundary — flag to the SA.
- The tasks form a dependency cycle that cannot be broken by splitting or merging.
