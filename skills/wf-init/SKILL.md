---
name: wf-init
description: Bootstraps a project's .wf/ workspace from the config template after install, then captures the project's real build/test commands into config. Idempotent — run once on a fresh install, or any time to repair the workspace.
---

# wf-init — bootstrap the .wf/ workspace

Run after `install.sh` has rendered the skills. It writes `.wf/config.yaml` from the
template — with the harness-specific `driver.agent_cmd` baked in — then creates everything
the roles assume exists at the paths config defines: the transient dir, the telemetry sink,
the durable ADR dir, and the capabilities, charter, architecture, plan, and learnings
homes. It also adds
a gitignore entry. After init, no role should hit a missing file. Idempotent — it never
overwrites an edited config or an existing home, nor duplicates the gitignore line.

From the project root, run:

```sh
bash {{WF_SKILLS_DIR}}/wf-init/scripts/scaffold.sh --target {{WF_TARGET}}
```

The project name defaults to the directory name; pass `--name <name>` to override.

After it runs, open `.wf/config.yaml` and confirm `project.name`, `project.target`, and
`driver.agent_cmd` — the driver launches every role through that command, so a harness
whose headless invocation differs from the rendered default halts the loop on its first
dispatch. See the `wf-basics` skill for what each config field governs.

## Capture the project commands — GATE: init is not done while they sit empty

The scaffolded config carries `commands.preflight`, `commands.stage_check` and
`commands.provision` as empty placeholders, run as mechanical gates by the build and
review roles and by the driver at every stage close — leave `commands.preflight` empty
and the first build task halts at its gate mid-sprint. Populate them now, before
reporting init complete:

1. **Probe the repo for evidence — read what exists, never guess from the stack:**
   - Script/task definitions: `package.json` `scripts` (root and workspaces),
     `Makefile` / `justfile` targets, a `scripts/` directory, `pyproject.toml` /
     `tox.ini` / `noxfile.py`, Gradle/Maven wrappers.
   - CI config (`.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, …) — the
     commands CI runs to gate a merge are the strongest evidence of the real gate.
   - The heavier test layer: where integration/e2e tests live (their directories, any
     `docker-compose*.yml` they depend on) and the exact command that invokes them.
   - **What a fresh clone needs before it can build.** Read `.gitignore` for dependency
     dirs (`node_modules`, `vendor`, `.venv`, `target`, build caches) and find the
     command that populates each — a lockfile names its installer (`package-lock.json` →
     `npm ci`, `go.sum` → `go mod download`, `poetry.lock` → `poetry install`). The
     driver builds every task in its own git worktree, which carries none of them.
   - Where test files actually sit: run a find for the test-file name patterns
     (`*_test.*`, `*.test.*`, `*.spec.*`, `*_spec.*`, `test_*.*`) and note the smallest
     set of roots that covers every hit.
2. **Propose a concrete value for each key, citing its evidence:**
   - `commands.preflight` — the fast per-task gate: lint + unit tests + build, chained
     with `&&` into one command that exits non-zero on any failure.
   - `commands.stage_check` — the heavy stage-close check: the integration/e2e
     invocation, plus where those tests live. A repo with no such layer gets an
     explicit "leave empty" proposal — empty skips the check by design.
   - `commands.provision` — what a fresh worktree needs before it can build, chained
     with `&&`. A repo whose build needs nothing gitignored gets an explicit "leave
     empty" proposal. Prefer the reproducible-install form (`npm ci` over `npm install`)
     and keep it non-interactive.
   - `paths.tests` — the roots covering every test file found, as a list (e.g.
     `["backend", "frontend/src", "e2e"]`). Where tests sit beside their source, the root
     is that source root. Keep `["."]` only when no smaller set covers them.
3. **Confirm with the human before writing.** Present each proposal with its evidence
   and ask. Never write an unconfirmed guess into the config — a wrong preflight fails
   every task it gates, a wrong `commands.provision` halts the loop at the first task
   worktree, and a `paths.tests` that misses a root under-reports the shipped
   system-test coverage every adequacy verdict is judged against.
4. **Write the confirmed values into `.wf/config.yaml`**, then run the confirmed
   `commands.preflight` once (pipe output to a file, read the file) to prove it exits
   zero on the untouched repo. A red baseline is a finding to report to the human, not
   a reason to weaken the command.
