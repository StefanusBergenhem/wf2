---
name: wf-init
description: Bootstraps a project's .wf/ workspace from the config template after install. Idempotent — run once on a fresh install, or any time to repair the workspace.
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
