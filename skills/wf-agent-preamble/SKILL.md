---
name: wf-agent-preamble
description: Cross-cutting rules every per-task subagent follows — worktree path discipline, test-output piping, the suppression ban, scope discipline, and the halt-report format. Loaded by build and review so the rules are stated once, not per role skill.
---

# Subagent Preamble — Universal Rules

These rules govern every per-task subagent dispatched into a worktree. Your role skill
carries the procedure; this carries the environment rules common to all roles. If a role
skill states a rule that contradicts one here, the role skill wins for its own scope — flag
the contradiction in your halt report.

## Worktree path discipline

Parallel worktrees share their parent repo's directory tree as ambient context. A sloppy
relative path silently mutates the wrong copy. The dispatch envelope provides `worktree` —
an absolute path to this task's worktree root.

1. **Read `worktree` before any Edit or Write.** If absent, HALT — the envelope is malformed.
2. **Validate cwd before the first mutation.** Before the first Edit, Write, or file-mutating
   bash command, verify cwd begins with `worktree`. If not, HALT immediately.
3. **Every `file_path` to Edit/Write and any file-mutating tool MUST be absolute and begin
   with `worktree`.** Paths outside the worktree are forbidden.
4. The same rule applies to file-mutating bash (`mv`, `>`, `tee`, `sed -i`, append
   operators, `git add` of out-of-tree files).
5. **`paths.<x>` from `.wf/config.yaml` resolve to worktree-rooted paths.** "Write
   `paths.review_ready`" means `<worktree>/<configured paths.review_ready>`, never the
   parent repo's copy.
6. **Read-only operations are exempt** — `Read`, `Grep`, `cat`, and other non-mutating tools
   may reference files outside `worktree`. The restriction is write-only.

A write outside `worktree` lands an uncommitted change in the parent repo that the
orchestrator reverts — your work is lost.

## Test & gate output piping

Pipe every test / build / preflight command output to a file, then read the **outcome**,
not the whole log:

```bash
<command> > /tmp/wf-<role>-<task-id>-<gate>.log 2>&1; echo "exit=$?"
```

Never read raw terminal output for these commands — long output blows context, and the log
is the artifact a reviewer or post-mortem can cite. Use task-scoped names — `<task-id>` is
the `task_id` from `paths.current_task` (`/tmp/wf-build-<task-id>-test.log`,
`/tmp/wf-build-<task-id>-preflight.log`, `/tmp/wf-review-<task-id>-preflight.log`) — so
other steps can find them and concurrent tasks never overwrite each other's logs.

**Read the exit code first, and pull only what you need into context.** On a clean exit,
the exit code is the whole result — do not read the log body; a green gate carries no
information you must hold. On a failure, read only the failing portion — grep the failing
cases or read the tail (`grep -nE 'FAIL|Error|panic|✗' <log>`, `tail -n 40 <log>`), never
the entire file.

## Suppression-directive ban

Never add `@ts-ignore`, `// nolint`, `# type: ignore`, `eslint-disable`, `noqa`,
`# noinspection`, `@SuppressWarnings`, or any equivalent suppression comment to source code.

If the code cannot pass checks without suppression, the design is wrong — HALT and write a
design issue per your role skill's halt protocol. Pre-existing suppressions outside the
task's scope stay untouched. A suppression introduced by the task itself, even one
character, rejects.

## Scope discipline

- **The contract bounds the work, not the file list.** `files_to_touch` in
  `paths.current_task` is the expected write set — start there, and write beyond it when
  the task genuinely needs it (a consumer that won't compile otherwise, a test-file home,
  a regenerated file). Every file you change must serve the contract's `covers`/acceptance
  criteria; `out_of_scope` is binding, and an unrelated drive-by change is a review
  rejection.
- **Commit every file you create** with the task's work — the merge to the sprint
  branch carries only committed files.

## Halt-report format

When a HALT fires, the report MUST contain:

1. **The exact trigger** — quote the rule (worktree path discipline, suppression ban, …).
2. **Minimal evidence** — file path + line, or the exact command + last 20 lines of output,
   or the contract field that contradicts reality. Citations, not narrative.
3. **The artifact you wrote** — most halts produce one (`design_issues`, `feedback`).
   Name the file and summarize its contents.
4. **What's safe to do next** — re-dispatch with an amended contract / route the design
   issue to `wf-spec-fix` / escalate to human. Match your role skill's halt protocol; do not invent states.

A halt report missing any field is incomplete — the orchestrator may re-dispatch you to
expand it before routing.
