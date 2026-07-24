# wf-spec-fix — contract-layer fix

You classified the issue as **`contract_amendment`** (the task contract diverges from a
correct requirement) or **`component_defect`** (already-merged code violates a correct
contract). Resolve it on the matching path, then return to SKILL.md Step 4.

## contract_amendment — amend the contract

Make the **minimum** change to the `task_id` contract in `$sprint_artifact` that makes it
buildable — the smallest edit that resolves the issue. Never touch another task, a
requirement, an acceptance criterion's traced requirement, or a component boundary.

## component_defect — author a follow-up task

The defective code is already merged, so no existing contract can honestly absorb the fix —
author a **new task** that repairs the component, and gate the parked task behind it.
**Load `{{WF_SKILLS_DIR}}/wf-tl/references/task-contract.md`** for the contract shape, then:

1. Append one task to `$sprint_artifact` with a complete contract: the next unused id in the
   sprint's id scheme; `covers` naming the requirement id(s) the merged code violates — when a
   violated id is not in this slice (code an earlier sprint shipped), carry its
   `{id, statement, serves}` requirements entry verbatim from the task that built it;
   acceptance criteria that name the defective behaviour and the required one, each with the
   `tests` that prove the fix; `files_to_touch` limited to the defective component's files plus
   the mandated tests' homes; `depends_on` only what the fix genuinely needs (usually nothing —
   the code it repairs is already merged). A wiring defect may split the requirement-owner and
   the defect-owner across two merged tasks — `covers` names the violated requirement,
   `files_to_touch` names the defective files, regardless of which task built which. When a
   task in **this** sprint built the files this fix overlaps, list its id in `fixes_origin`:
   the origin is already merged and never re-runs, so `sprint check`'s C10 must not read the
   intended overlap as a missing dependency edge.
2. Add the new task's id to the **`task_id` task's `depends_on`** — the parked task may only
   re-run after the fix has merged. Skip this when `task_id` is `null` (a stage-boundary issue
   parks no task): the follow-up merges and the heavy check re-runs at the next boundary.

## Both paths — inline, then return

Run `python3 <paths.tools>/cli/wf sprint materialize` — an amendment or a follow-up task leaves
thin references the build cannot consume until they are inlined. Then return to SKILL.md Step 4.
