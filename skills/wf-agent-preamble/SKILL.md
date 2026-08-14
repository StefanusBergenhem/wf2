---
name: wf-agent-preamble
description: Cross-cutting rules every per-task subagent follows — worktree path discipline, test-output piping, reading discipline, the suppression ban, scope discipline, and the halt-report format. Loaded by build and review so the rules are stated once, not per role skill.
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
5. **`paths.<x>` from your dispatch envelope resolve to worktree-rooted paths.** "Write
   `paths.<x>`" means `<worktree>/<the envelope's paths.<x>>`, never the parent repo's
   copy.
6. **Read-only operations are exempt** — `Read`, `Grep`, `cat`, and other non-mutating tools
   may reference files outside `worktree`. The restriction is write-only.

A write outside `worktree` lands an uncommitted change in the parent repo, which is
reverted rather than merged — your work is lost.

## Test & gate output piping

Pipe every test / build / preflight command output to a file, then read the **outcome**,
not the whole log:

```bash
<command> > /tmp/wf-<role>-<task-id>-<gate>.log 2>&1; echo "exit=$?"
```

Never read raw terminal output for these commands — long output blows context, and the log
is the artifact a reviewer or post-mortem can cite. Use task-scoped names — `<task-id>` is
the `task_id` your dispatch envelope names (`/tmp/wf-build-<task-id>-test.log`,
`/tmp/wf-build-<task-id>-preflight.log`, `/tmp/wf-review-<task-id>-preflight.log`) — so
other steps can find them and concurrent tasks never overwrite each other's logs.

**Read the exit code first, and pull only what you need into context.** On a clean exit,
the exit code is the whole result — do not read the log body; a green gate carries no
information you must hold. On a failure, read only the failing portion — grep the failing
cases or read the tail (`grep -nE 'FAIL|Error|panic|✗' <log>`, `tail -n 40 <log>`), never
the entire file.

**Pass an explicit tool timeout of at least 600000 ms on every one of these calls.** A
test suite, build, install or gate routinely outruns the Bash tool's ~120 s default, and
past that the tool backgrounds the run on its own however you invoked it. Whether you then
get a usable completion notification is the harness's call, not yours — sessions have been
lost waiting for one that never came. If you do end up backgrounded, do not park: poll the
log file and carry on from what it says.

## Reading discipline

Every byte a tool returns stays in your context for the rest of the session; every
re-read adds it again.

- **Locate, then window.** Find a symbol with a **match-lines-only** search — the Grep
  tool when your toolset has one, otherwise `grep -rn` with **no** `-A`/`-B`/`-C` — then
  read only the surrounding region with Read's offset/limit. Read a file whole only when
  it is a few hundred lines or less, or the task rewrites most of it.
- **Do not page file content through bash.** `cat`, `sed -n '1,200p'`, and `grep -A/-B/-C`
  pipelines dump whole files as command output, where no line limit applies and the
  content lands in your context twice if you then Read it. Locate with a search, view
  with Read.
- **Re-read only what changed.** After an edit, re-read the edited region, not the whole
  file. A region already in your context needs no re-read to "check" it.

## Suppression-directive ban

Never add `@ts-ignore`, `// nolint`, `# type: ignore`, `eslint-disable`, `noqa`,
`# noinspection`, `@SuppressWarnings`, or any equivalent suppression comment to source code.

If the code cannot pass checks without suppression, the design is wrong — HALT and write a
design issue per your role skill's halt protocol. Pre-existing suppressions outside the
task's scope stay untouched. A suppression introduced by the task itself, even one
character, rejects.

## Scope discipline

- **The contract bounds the work, not the file list.** The `grounding` pointers in
  `paths.current_task` are a starting set, not a fence — write beyond them when the task
  genuinely needs it (a consumer that won't compile otherwise, a test-file home, a
  regenerated file). Every file you change must serve the contract's `covers`/acceptance
  criteria; `boundaries` is binding, and an unrelated drive-by change is a review
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
4. **What's safe to do next** — re-dispatch with an amended contract / repair of the design
   issue you wrote to `paths.design_issues` / escalate to human. Match your role skill's
   halt protocol; do not invent states.

A halt report missing any field is incomplete — write all four before you exit.
