# validation-repo

A synthetic **target repo** used to verify wf2's install output by eye. wf2 is
installed into it for **all three harnesses** (`.claude/`, `.pi/`, `.opencode/`)
so the rendered skills can be compared side by side, and `.wf/` is scaffolded so
the workspace layout is visible.

It also carries a tiny multi-language source tree (Go + TypeScript, with a real
dependency edge) so it can double as a discover target.

## Refresh

From this directory, re-render all three harnesses and re-scaffold:

```sh
for t in claude pi opencode; do ../install.sh --target "$t"; done   # run from validation-repo/
bash .claude/skills/wf-init/scripts/scaffold.sh --target claude
```

## Known limitation

`.wf/config.yaml`'s `project.target` records a single harness, while all three
are installed here. Multi-harness config is a deferred bare-bones gap, not a bug
in this fixture.
