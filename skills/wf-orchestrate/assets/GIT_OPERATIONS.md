# Git operations

Every git command the controller runs. Tasks build in **per-task worktrees** off the
sprint branch; approved tasks **batch-merge** at the stage boundary; the run **pushes
once**, at ship.

## Sprint branch

Created once in `preparing`. Gate first, then branch:

1. Working tree clean (`git status --porcelain` empty) and the base branch not behind its
   remote. If either fails, HALT and ask the user.
2. `git checkout -b <sprint-branch> <base>` — name it `sprint/<sprint_id>` (the
   `sprint_id` from `$SPRINT`); on resume, re-derive the same name — never invent a
   variant.

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

`--no-ff` keeps each task a reviewable unit on the branch. On a conflict: `git merge
--abort` and **HALT** — never auto-resolve. After a clean merge, `wf pipeline
complete-task <id> --commit <build-sha> --merge <merge-sha>`, then remove the worktree:

```
git worktree remove <worktree>
```

Worktree cleanup is mandatory — never leave an orphan.

## Ship (closeout)

The only push of the run:

```
git push -u origin <sprint-branch>
gh pr create --base <base> --head <sprint-branch> --title "<sprint summary>" --body "<body>"
```

The PR body lists completed tasks, any escalated/blocked tasks, and design issues. Then
`wf pipeline complete-sprint`. If the push or PR fails (auth, remote, conflict), HALT and
report — do not retry blindly, and do not `complete-sprint` until the PR is open.
