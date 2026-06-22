# Git operations

Every git command the controller runs. Tasks build in **per-task worktrees** off the
sprint branch; approved tasks **batch-merge** at the stage boundary; the run **pushes
once**, at ship.

## Sprint branch

Created once in `preparing`. Gate first, then branch:

1. Working tree clean (`git status --porcelain` empty) and the base branch not behind its
   remote. If either fails, HALT and ask the user.
2. `git checkout -b <sprint-branch> <base>` — name it for the sprint (e.g.
   `sprint/<sprint-id>`). Record `sprint_branch` and `sprint_branch_base_sha` in state.

All task worktrees branch from the sprint branch; nothing is committed to the base until
the ship PR merges.

## Worktree (per task)

`wf pipeline next` gives each dispatch entry a `worktree` path — use it verbatim. Create
it off the **current sprint-branch HEAD** so a task sees prior stages' merged work:

```
git worktree add <worktree> -b <task-branch> <sprint-branch>
```

A git worktree is a fresh checkout that shares the main checkout's `.git` (objects + refs),
so committed files like `.wf/config.yaml` and every branch ref are present in it — but
**gitignored host transients are not**: the run-level `pipeline_state` does not exist in a
fresh worktree. So write each task's artifacts (`current_task`, `feedback`, …) INTO its
worktree, and pass any run-level fact a worktree agent needs — the `sprint_branch` review
uses as its diff base — in the dispatch envelope, never expecting it to read host state.
Name the task branch from the task id.

If `git worktree add` fails (e.g. the path exists from an interrupted run), reuse the
existing worktree rather than recreating it — a re-dispatched build restarts from zero in
it (it re-reads its contract), so the surviving worktree is just where it runs.

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
