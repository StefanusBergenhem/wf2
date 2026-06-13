---
name: wf-init
description: Bootstraps a project's .wf/ workspace (config.yaml, transient dir, gitignore) after install. Run once on a fresh install, or any time to repair the workspace — it is idempotent.
---

# wf-init — bootstrap the .wf/ workspace

Run after `install.sh` has rendered the skills. It scaffolds the project's `.wf/`
workspace: `config.yaml` (committed), `transient/` (gitignored output), and the
`.gitignore` entry. Idempotent — it never overwrites an edited config nor
duplicates the gitignore line.

From the project root, run:

```sh
bash {{WF_SKILLS_DIR}}/wf-init/scripts/scaffold.sh --target {{WF_TARGET}}
```

That writes `.wf/config.yaml` (project name defaults to the directory name) and
creates `.wf/transient/`. To set an explicit name, pass `--name <name>`.

After it runs, open `.wf/config.yaml` and confirm `project.name` and
`project.target`. The config grows as you add skills; see the `wf-basics` skill for
what each field governs.
