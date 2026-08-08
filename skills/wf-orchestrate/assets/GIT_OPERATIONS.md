# Git operations

Every git command the controller runs. Tasks build in **per-task worktrees** off the
sprint branch; approved tasks **batch-merge** at the stage boundary; the run **pushes
once**, at ship.

## Sprint branch

Created once in `preparing`. The base is the **local** `project.base_branch` from
`.wf/config.yaml` — never `origin/<base>`; do not fetch or consult the remote here.
Gate first, then branch:

1. Working tree clean (`git status --porcelain` empty). If not, HALT and ask the user.
2. `git checkout -b <sprint-branch> <base>` — cut from the local `<base>`; name it
   `sprint/<sprint_id>` (the `sprint_id` from `paths.sprint`); on resume, re-derive the same
   name — never invent a variant.

## Worktree (per task)

`wf pipeline next` gives each dispatch entry a `worktree` path — use it verbatim. Create
it off the **current sprint-branch HEAD** so a task sees prior stages' merged work:

```
git worktree add <worktree> -b <task-branch> <sprint-branch>
```

Name the task branch from the task id. Write each task's artifacts (`current_task`,
`feedback`, …) INTO its worktree — a worktree cannot read gitignored host transients, so
any run-level fact its agent needs (e.g. `sprint_branch`) travels in the envelope.

If the path already exists (left by an interrupted run), verify it before reuse:

1. **Stale base** — run `git -C <worktree> merge-base --is-ancestor <sprint-branch> HEAD`.
   Non-zero exit → the sprint branch moved past this worktree's base: recreate it —
   `git worktree remove --force <worktree>`, `git branch -D <task-branch>`, then
   `git worktree add` as above. A re-dispatched build restarts from zero, so nothing in
   the stale worktree is worth keeping.
2. **Same base** → reuse it as-is.

Before any dispatch into a worktree (fresh or reused): if `commands.preflight` needs
gitignored dependency dirs (e.g. `node_modules`) and they are absent, provision them —
run the project's install command, or copy the dir from the main checkout.

## Merge (stage boundary, batch)

For each `approved` task, from the sprint branch:

```
git checkout <sprint-branch>
git merge --no-ff <task-branch> -m "<task-id>: merge"
```

`--no-ff` keeps each task a reviewable unit on the branch. On a conflict: leave the merge in
progress (do NOT `git merge --abort`) and route it to `wf-stage-repair` per SKILL.md §2c
step 2 — never auto-resolve the conflict yourself. After a clean merge, `wf pipeline
complete-task <id> --commit <build-sha> --merge <merge-sha>`, then remove the worktree:

```
git worktree remove <worktree>
```

Worktree cleanup is mandatory — never leave an orphan.

## Ship (closeout)

Close the sprint first, then publish it in the run's **one** push. `complete-sprint`
archives and drains the sprint's working set into `<paths.archive>/<sprint_id>/`, runs
the close-time drain — draining served learnings from `paths.learnings` and writing
`paths.drain_report` (transient, for the next wf-sa run) — and resets the run state.
Committing the snapshots **and the drained learnings** before the push carries the drain
into the PR instead of stranding it as an uncommitted, un-pushed dirty tree:

Capture the spec-fix decision report **before** `complete-sprint` drains it: read
`paths.spec_decisions` if present — its blocks go in the PR body under **Spec decisions**.

```
wf pipeline complete-sprint
git add -A -- <paths.archive> <paths.learnings>
git commit -m "sprint close: archive + drain <sprint-id>"   # skip if nothing changed (empty stage)
git push -u origin <sprint-branch>
gh pr create --base <base> --head <sprint-branch> --title "<sprint summary>" --body "<body>"
```

The PR body lists completed tasks, any escalated/blocked tasks, design issues, the
`drain` summary complete-sprint emitted (emptied designs, drained learnings, proof-gate
candidates), and — under
**Spec decisions** — the `paths.spec_decisions` blocks captured above, so the human reviews
every autonomous spec fix (and any shipped behaviour it superseded) alongside the diff.

`complete-sprint` resets the run state to `idle`, so it is the point of no return: if the
push or PR then fails (auth, remote, conflict), the sprint is already closed locally —
**HALT and report** that the sprint branch (archive commit included) needs a manual
`git push` + PR. Do not retry `complete-sprint`, and do not re-run the sprint from scratch
(its `paths.sprint` and design-slice have been drained).
