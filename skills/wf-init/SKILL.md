---
name: wf-init
description: Bootstraps a project's .wf/ workspace from the config template after install. Idempotent — run once on a fresh install, or any time to repair the workspace.
---

# wf-init — bootstrap the .wf/ workspace

Run after `install.sh` has rendered the skills. It writes `.wf/config.yaml` from
the template, then creates the directories and telemetry sink that config defines
and adds a gitignore entry. Idempotent — it never overwrites an edited config nor
duplicates the gitignore line.

From the project root, run:

```sh
bash {{WF_SKILLS_DIR}}/wf-init/scripts/scaffold.sh --target {{WF_TARGET}}
```

The project name defaults to the directory name; pass `--name <name>` to override.

After it runs, open `.wf/config.yaml` and confirm `project.name` and
`project.target`. The config grows as you add skills; see the `wf-basics` skill for
what each field governs.
