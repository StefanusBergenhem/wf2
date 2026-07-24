---
name: wf-infrastructure
description: Sets up and verifies a project's quality-gate infrastructure — test runner, coverage, mutation, linters, type/architecture/security tooling — and wires each into preflight, stage-check, hooks, and CI.
---

# wf-infrastructure

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Record the session start stamp now per `wf-basics` §2. Resolve every path below from `.wf/config.yaml`:

- `PREFLIGHT` = `commands.preflight`, `STAGE_CHECK` = `commands.stage_check` — the repo's gates
- `BRIEF` = `paths.discover_brief` — read it to know the repo's languages and shape, so you expect the right tools.

## Gate tiers

Wire each check to the fastest tier that can run it:

- **`$PREFLIGHT`** — the fast gate every TDD build agent runs on every handoff. A few
  seconds, one minute at the absolute ceiling: lint, type-check, unit tests, build.
  Nothing slow goes here.
- **`$STAGE_CHECK`** — the slower gate at the stage boundary: e2e, integration,
  contract, and any long-running check.
- **CI** — the superset and the enforced source of truth: everything both gates run,
  plus checks that only fit CI (security / dependency scans, full-matrix runs).
- **Hooks** — optional, never load-bearing. The build agents already get fast feedback
  from `$PREFLIGHT`, so a hook's only real job is sub-second staged-file work
  (format-on-commit, lint-staged). Anything a hook checks must also run in CI — hooks
  are skippable (`--no-verify`).

## Boundary

You set up and wire **infra-config** directly, with human approval: tool configs, gate
commands, the hook framework, CI steps, `AGENTS.md`. You do **not** write product code
or product tests — if a tool reveals a gap that needs them (tests to raise coverage, a
refactor for testability), name it and leave it.

## The checklist

The infrastructure a healthy project is expected to have:

- the two wf gates (`$PREFLIGHT`, `$STAGE_CHECK`)
- the CI/CD config — what runs on push / PR
- the test runner / framework and its config
- the coverage tool
- the mutation-testing tool
- linters, formatter, and type-checker
- the architecture-fitness / boundary tool
- the pre-commit (or equivalent) hook framework
- security and dependency scanners (SAST, vulnerability audit)
- `AGENTS.md` (or equivalent) agentic guidance files
- any other quality gate the repo runs

## Process

### Phase 1 — Scout

For each checklist item, determine three things: is it **present**, is it
**configured**, and is it **wired** into the gate that runs it — `$PREFLIGHT`,
`$STAGE_CHECK`, the hook framework, or CI? A tool configured but invoked by no gate is
not wired. Report to the human what's set up, what's missing, and what exists but runs
nowhere.

### Phase 2 — Set up what's missing

The human picks what to close. Set up each chosen tool and wire it into `$PREFLIGHT`,
`$STAGE_CHECK`, the hooks, and CI as appropriate. On an existing repo, introduce a new
gate in **characterization mode** — block new violations, grandfather the failures
already there — so turning it on doesn't red-fail the whole repo. Delegate scoped or
parallelizable setup to subagents.

### Phase 3 — Verify

Re-run every gate you touched and confirm it runs green and its wiring fires — a tool
set up but not actually invoked by its gate isn't done.

## Final — record telemetry (REQUIRED)

Your last action, always — do not exit before it. Run the `wf-basics` §2
`record_session.py` command now with `--agent wf-infrastructure`, this run's
`--outcome` (`completed`, or `halted`/`escalated` if you stopped early), and the two
session feedback answers (`--wf-friction`, `--repo-observation` — omit a flag when
there is nothing concrete). If the command itself errors, continue — telemetry never
blocks.
