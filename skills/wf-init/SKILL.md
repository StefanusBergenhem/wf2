---
name: wf-init
description: Bootstraps a project's .wf/ workspace from the config template after install, then captures the project's real build/test commands into config. Idempotent — run once on a fresh install, or any time to repair the workspace.
---

# wf-init — bootstrap the .wf/ workspace

Run after `install.sh` has rendered the skills. It writes `.wf/config.yaml` from the
template, then creates everything the roles assume exists at the paths config defines —
the transient dir, the telemetry sink, the durable ADR dir, and the capabilities,
design-backlog, and learnings homes (each copied from its owning skill's template) — and
adds a gitignore entry. After init, no role should hit a missing file. Idempotent — it
never overwrites an edited config or an existing home, nor duplicates the gitignore line.

From the project root, run:

```sh
bash {{WF_SKILLS_DIR}}/wf-init/scripts/scaffold.sh --target {{WF_TARGET}}
```

The project name defaults to the directory name; pass `--name <name>` to override.

After it runs, open `.wf/config.yaml` and confirm `project.name` and
`project.target`. See the `wf-basics` skill for what each config field governs.

## Capture the project commands — GATE: init is not done while they sit empty

The scaffolded config carries `commands.preflight` and `commands.stage_check` as empty
placeholders. The build, review, and orchestration roles run them as mechanical gates —
leave `commands.preflight` empty and the first build task halts at its gate mid-sprint.
Populate both keys now, before reporting init complete:

1. **Probe the repo for evidence — read what exists, never guess from the stack:**
   - Script/task definitions: `package.json` `scripts` (root and workspaces),
     `Makefile` / `justfile` targets, a `scripts/` directory, `pyproject.toml` /
     `tox.ini` / `noxfile.py`, Gradle/Maven wrappers.
   - CI config (`.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`, …) — the
     commands CI runs to gate a merge are the strongest evidence of the real gate.
   - The heavier test layer: where integration/e2e tests live (their directories, any
     `docker-compose*.yml` they depend on) and the exact command that invokes them.
2. **Propose a concrete value for each key, citing its evidence:**
   - `commands.preflight` — the fast per-task gate: lint + unit tests + build, chained
     with `&&` into one command that exits non-zero on any failure.
   - `commands.stage_check` — the heavy stage-boundary check: the integration/e2e
     invocation, plus where those tests live. A repo with no such layer gets an
     explicit "leave empty" proposal — empty skips the stage check by design.
3. **Confirm with the human before writing.** Present each proposal with its evidence
   and ask. Never write an unconfirmed guess into the config — a wrong preflight fails
   every task it gates.
4. **Write the confirmed values into `.wf/config.yaml`**, then run the confirmed
   `commands.preflight` once (pipe output to a file, read the file) to prove it exits
   zero on the untouched repo. A red baseline is a finding to report to the human, not
   a reason to weaken the command.
