# wf-swa — default mode

Build the sprint from the design slice: author the acceptance criteria that make each slice
requirement testable, then decompose them into a per-task dependency graph in `paths.sprint`. The
requirements and component boundaries are fixed — you consume them, you do not change them;
the acceptance criteria and the task breakdown are yours.

**Components do not bound a task's file set.** `files_to_touch` is the atomic edit set — it
crosses component boundaries whenever the change does, and that is never a reason to split
a task.

## Phase 1 — Ground

1. Read `paths.design_slice` — one **buildable increment**:
   the component requirements (each with its owner and driver), the **system test cases**
   (wf-sa wrote them; you plan each into a task), the architecture moves, the governing ADRs,
   and the **risks wf-sa flagged for you** (fragile seams, ordering constraints, external deps
   — they shape your decomposition). Its requirements are your whole scope. **HALT and report
   if it is absent**.
2. Read the relevant `paths.adrs` for the rationale you must respect when writing
   `implementation_notes`.
3. Read source **targeted to the decomposition decision, not wholesale.** The slice already
   names the components and bounds the work — you do not need every named component's full
   source in context to decompose it. To enumerate a changed symbol's consumers, run
   `python3 <paths.tools>/cli/wf impact files --symbol <sym>` and read only the hits the
   decomposition decision turns on — never hand-grep and read file after file to build the
   list yourself. Reach for depth where a file-set, a consumer set, or an
   `implementation_notes` pattern turns on it; the slice carries the breadth.

## Phase 2 — Author the acceptance criteria

**Load `references/task-contract.md` before writing any criterion or contract** — it holds
the AC rules (testable-from-source, the `REQ-N.AC-M` id scheme, failure/boundary
completeness, staying within the requirement, the per-criterion `tests` levels) and governs
the contract you build in the later phases. For each requirement in the slice, author the
acceptance criteria that prove it, per those rules.

## Phase 3 — Decompose into tasks

Cut the criteria into tasks — each one cohesive, testable unit of work that satisfies
one or more requirements and carries their criteria. Every criterion lands in exactly
one task; every requirement is fully covered across the task set.

For each task, author its **complete thin** contract per `references/task-contract.md`:
`covers`, `files_to_touch` (the expected write set, cut with the impact tool), the
per-criterion `tests`, `out_of_scope`, and pointer-only `implementation_notes`. Do **not** write `requirements`,
`serves`, or `interface_contract` by hand — `wf sprint materialize` (Phase 5) inlines them
from the slice. When a task builds a component or widens a seam the slice's **Interface
contracts** section fixes a shape for, set `interface_contract_ref` to that contract's
name — the build implements the agreed shape, never one it discovers.

**Fold in the slice's Supersedes list.** For each superseded id the slice's **Supersedes**
section carries, locate its proving test file(s) mechanically — grep the test tree for
`[REQ:<old-id>]` / `[SYS-TC:<old-id>]` (or derive them via
`python3 <paths.tools>/reconcile/register.py --tests <root> [--tests <root> ...]`, one for every
root in `paths.tests`) — never guess. Add those
files to `files_to_touch` of the task covering the successor requirement (a dedicated
removal task when the entry has no successor), with an explicit note in its
`implementation_notes`: update or delete the old proving test and its tag — a superseded
tag must not survive the sprint.

**Plan the slice's system test cases.** For each `SYS-TC-<n>` case wf-sa wrote, add an e2e
task whose `system_tests` names that case's id (the materializer fills its text). The case
`Covers` a **capability**, so its `depends_on` names the tasks building the requirements
**driven by that capability** (read the drivers off the slice's component requirements) —
putting it downstream of the assembled path. It exercises (imports) the components without
owning them; the build stamps `[SYS-TC:SYS-TC-<n>]` in the e2e test.

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

**Two tasks whose `files_to_touch` overlap need an edge between them, or an accepted
risk** — unordered, they land in the same parallel stage and edit the same file in
separate worktrees, which can conflict at the stage merge. `wf sprint check` warns (C10)
on any such pair: add the edge (or, better, factor the shared file into its own upstream
task so the leaf tasks partition), or accept the merge-conflict risk — the stage boundary
repairs a conflicted merge on demand.
When numbered artifacts are pre-allocated across tasks (migrations, ordered fixtures), give
them numbers that ascend with merge order — a lower number merging after a higher one
replays out of order against a persistent store.

## Phase 5 — Write and gate

1. Write `paths.sprint` from `assets/sprint.yaml.tmpl` — thin fields only. Mint its top-level
   `sprint_id` as `sprint-<yyyymmdd>-<short-scope-slug>` — today's date plus a short slug
   of the sprint's scope, `[a-z0-9-]` only. The sprint is transient and gitignored — there
   is nothing to commit; the build pipeline consumes the working-tree file directly.
2. **Run `python3 <paths.tools>/cli/wf sprint materialize`.** It inlines the slice's
   verbatim fields (requirements, serves, interface contracts, SYS-TC text). An error
   names a `covers` id, contract name, or SYS-TC id the slice does not carry — fix the
   reference, or halt as a slice defect if the slice genuinely lacks it.
3. **Gate: run `python3 <paths.tools>/cli/wf sprint check`. Do not proceed until it
   reports `verdict: pass` (exit 0).** It checks the sprint against the slice — every
   slice requirement covered, every criterion carried by exactly one task and carrying
   tests (or gate-verified via `verified_by`), every requirement's driver in the task's
   `serves`, every SYS-TC carried by an e2e task, no UNCONFIRMED assumption, an acyclic
   DAG. On an error finding, fix the decomposition in `paths.sprint`, **re-run materialize**,
   and re-run the check. Read its warnings too — an undeclared test home or an unordered
   `files_to_touch` overlap is a planning-quality hint worth fixing while you are here. A finding you cannot resolve without minting or changing a requirement is a
   slice defect — halt per **Halt conditions**, never invent a criterion to silence it.
4. Return a summary: the task count, the dependency shape, and the gate verdict. Leave
   `paths.design_slice` in place — it is drained at sprint close, not here.

## Halt conditions

Halt and report with outcome `escalated` if any condition below holds.

### Slice defects — the slice cannot be decomposed as written

- A requirement cannot be made testable.
- A requirement no component in the slice owns, or whose declared owner cannot express the
  behaviour at all. A requirement whose edit set spans several components is **not** this —
  an atomic edit set crossing component boundaries is normal, never a defect.
- The slice's **Interface contracts** section fixes a shape the source contradicts — a type,
  sentinel, or signature the code does not use.
- Satisfying a requirement would regress a working behaviour no requirement owns.

**Gate: before you halt, append one entry to `paths.design_issues` from
`assets/design_issues.yaml.tmpl`.** One entry per rejection, carrying **every** blocker you
found and the `working_notes` — the measurements, the signatures that unblock a requirement,
the traps the next cut must avoid. Skip it and your findings die with the session; wf-sa
re-derives them from nothing.

### Structural halts — write no design issue

- `paths.design_slice` is absent.
- A component named in the slice cannot be located in the source — the structure has
  drifted (needs a discover re-run).
- The tasks form a dependency cycle that cannot be broken by splitting or merging.
