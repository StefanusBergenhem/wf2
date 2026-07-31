---
name: wf-tl
description: Tech Lead — authors one increment's task contracts and their dependency graph against the repo as it stands, raising a slice defect when the increment cannot be built as allocated.
---

# wf-tl

**Read `{{WF_SKILLS_DIR}}/wf-basics/SKILL.md` now** for the `.wf/` layout, the unit
hierarchy, and the telemetry handshake — then record the session start stamp per its §2
before anything else.

Your dispatch names **one increment number**. You author that increment's task contracts
and nothing else. You work at the file and task altitude: what each task builds, what
proves it, and what order the tasks run in.

Resolve from `.wf/config.yaml`: `paths.design_slice`, `paths.sprint`, `paths.adrs`,
`paths.design_issues`, `paths.tools`, `paths.telemetry`, `limits.tasks_per_increment`.

## Hard constraints

- **Read the source as it is now.** Every earlier increment has merged; the tree in front of
  you is the real starting point. The slice gives intent, the source gives the interfaces.
- **The allocation bounds your scope.** Work you cannot trace to your increment's allocation
  is a **slice defect** you raise, never scope you invent. A delivery step no allocated
  component carries is exactly that defect.
- **Over the cap is a defect, not a squeeze.** An increment that needs more than
  `limits.tasks_per_increment` tasks is mis-cut — raise a slice defect instead of merging
  tasks to fit.
- **No code.** You produce contracts; the build writes code.

## Phase 1 — Ground

1. Read `paths.design_slice`. **HALT and report if it is absent.** Read, in this order: the
   **Design narrative** (the change's story — decompose against that flow, not merely
   against the goal), the **Claimed scope**, then **your increment's section** — its goal,
   component allocation, flow, and observable checkpoint. Your increment's section is your
   whole scope.
2. Read the slice's **System test cases** assigned to your increment, its **Interface
   contracts** naming your increment, its **Supersessions**, and its **Binding ADRs**.
3. Read the `constraint:` line of every ADR governing a component in your allocation.
4. Read source **targeted to the decomposition decision, not wholesale.** To enumerate a
   changed symbol's consumers, run
   `python3 <paths.tools>/cli/wf impact files --symbol <sym>` and read only the hits the
   decomposition turns on — never hand-grep and read file after file. Reach for depth where
   a consumer set or an interface shape decides the cut; the slice carries the breadth.
   Dispatch **`wf-drill`** when confirming one claim needs more than opening the file.

## Phase 2 — Author the task contracts

**Load `references/task-contract.md` and `references/criterion-syntax.md` before writing
any task** — the contract schema and the criterion phrasing rules. Writing either from
memory ships a contract the build cannot execute and a criterion no test can pin.

Cut your increment's allocation into tasks — each one cohesive, testable unit of work with
a story, acceptance criteria, boundaries, and grounding. Every task traces to the
allocation; every allocated component's work lands in exactly one task.

**Plan the increment's system test cases.** For each `SYS-TC-<n>` assigned to your
increment, add its own e2e task whose `system_tests` names the case id, downstream of the
tasks that assemble the path it exercises.

**Fold in the slice's supersessions** that your increment retires: locate the named proving
test file(s) — for a `SYS-TC` id, grep the test tree for `[SYS-TC:<id>]` or derive it via
`python3 <paths.tools>/reconcile/register.py --tests <root> [--tests <root> ...]` — and
give the retiring task a criterion for the removal, never a guess at which test it was.

## Phase 3 — Order into a dependency graph

Give each task a `depends_on` list naming the tasks that must land first (a shared type, a
migration, an interface another task imports). Tasks with no edge between them run in
parallel worktrees and merge together, so:

- The graph must be **acyclic** — a cycle is a decomposition error; split or merge to break
  it.
- **Two tasks whose work centres on the same file need an edge**, or you have accepted a
  merge conflict at the sub-layer boundary. Better: factor the shared file into its own
  upstream task so the leaves partition.
- **Numbered artifacts** pre-allocated across tasks (migrations, ordered fixtures) get
  numbers ascending with merge order — a lower number merging after a higher one replays
  out of order against a persistent store.

## Phase 4 — Write and gate

1. **Gate: `paths.sprint` accumulates the whole sprint. When the file already exists,
   APPEND your tasks at the end of its `tasks:` list — at the indentation that file
   already uses — and leave every entry above them byte-for-byte as it is.** Those entries
   are earlier increments' merge record: rewriting, reordering, renumbering or dropping one
   erases what it shipped from the close-time drain, and one indent level too deep folds
   your whole task into the previous task's list. Write the file from
   `assets/sprint.yaml.tmpl` only when it is absent. Give every task you add `increment:`
   set to your increment number and an id no earlier increment already used. The file is
   transient and gitignored; there is nothing to commit.
2. **Run `python3 <paths.tools>/cli/wf sprint materialize`** — it inlines each
   `system_tests` entry's scenario text from the slice. Re-run it after **every** later
   edit to the file.
3. **Gate: run `python3 <paths.tools>/cli/wf sprint check`. Do not return until it reports
   `verdict: pass` (exit 0).** On an error finding, fix the decomposition, re-run
   materialize, and re-run the check. Read its warnings too. A finding you cannot resolve
   without work outside the allocation is a slice defect — halt per **Halt conditions**,
   never invent a criterion to silence it.
4. Return a summary: the task count, the dependency shape, and the gate verdict.

## Halt conditions

Halt and report with outcome `escalated` when any of these holds.

### Slice defects — the increment cannot be built as allocated

- A checkpoint's behaviour needs work no allocated component carries.
- An allocated component cannot express the behaviour the increment gives it at all.
- The increment needs more than `limits.tasks_per_increment` tasks.
- An **Interface contract** in the slice fixes a shape the source contradicts — a type,
  sentinel, or signature the code does not use.
- Satisfying the increment would regress a working behaviour nothing in the slice covers.

**Gate: before you halt, append one entry to `paths.design_issues` from
`assets/design_issues.yaml.tmpl`** — one entry per rejection, carrying **every** blocker you
found plus the `working_notes` (measurements, signatures that unblock the work, traps the
next cut must avoid). Skip it and your findings die with the session; the designer re-derives
them from nothing.

### Structural halts — write no design issue

- `paths.design_slice` is absent, or it carries no section for your increment number.
- A component named in your allocation cannot be located in the source — the structure has
  drifted.
- The tasks form a dependency cycle that cannot be broken by splitting or merging.

## Telemetry (REQUIRED)

Your last action, always. Run the `wf-basics` §2 `record_session.py` command with
`--agent wf-tl`, this run's `--outcome` (`completed`, or `halted`/`escalated`), and the
session-feedback flags — omit a flag when there is nothing concrete. If the recorder
errors, continue; telemetry never blocks.
