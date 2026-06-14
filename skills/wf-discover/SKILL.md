---
name: wf-discover
description: Derives a transient subsystem read-view of a repo — a mechanical structure spine plus LLM-scout descriptions, rendered to an interactive HTML map and an agent brief. Run when orienting on an unfamiliar repo before planning.
---

# wf-discover

Read `wf-basics` first for the `.wf/` layout and config rules. Resolve every path
below from `.wf/config.yaml`:

- `TOOLS` = `paths.tools`/discover (default `.wf/tools/discover`)
- `OUT`   = `paths.discover` (default `.wf/transient/discover`)
- `NAME`  = `project.name`

Pipeline: `extract → spine merge → cluster` (mechanical) → scout (LLM) → `render`.
Tool docs: `$TOOLS/README.md`; new-language extractors: `$TOOLS/EXTRACTORS.md`.

Reading `wf-basics` first captures `TS_START`; the **Final** step below records the
session. Both are mandatory.

## Step 1 — Clear previous output

```sh
rm -rf "$OUT" && mkdir -p "$OUT"
```

## Step 2 — Verify extractors

Determine the repo's languages (Go → `go.mod`; TS/JS → `package.json`/`tsconfig.json`).
For each present language, confirm its extractor is built:

- Go: `$TOOLS/readview-go/readview` exists
- TS/JS: `$TOOLS/readview-ts/dist/` exists

If a present language's extractor is missing, **halt and ask the human** whether to
build it (build steps in `$TOOLS/EXTRACTORS.md`). Do not continue without it.

## Step 3 — Run the mechanical spine

Pass one flag-group per present language. Roots are repo-relative.

```sh
python3 "$TOOLS/discover.py" --repo . --out "$OUT" --name "$NAME" \
  [--go-roots cmd,internal --go-mod go.mod] \
  [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'src/generated/**']
```

Writes `$OUT/model.json` (component graph) and `$OUT/clusters.json` (three candidate
clusterings: folder · depgraph · git-cochange).

## Step 4 — Scout augmentation (subagent)

Dispatch ONE subagent with `$OUT/model.json` + `$OUT/clusters.json`:

- **Reconcile, don't pick a winner.** Synthesize the three clusterings into ONE
  partition (~6–10 subsystems; every uid in exactly one subsystem; a "Shared /
  cross-cutting" bucket is fine). Surface where they disagree.
- **Describe every component** in 1–2 grounded sentences — prefer its existing
  `synopsis`, else its `types`/`functions` signatures; read source only when
  signatures are insufficient.
- **Write `$OUT/subsystems.json`**: `system_summary`, `subsystems[]` (`name`,
  `summary`, `members`, `basis`), `component_descriptions{uid}` for EVERY uid,
  `disagreements[]`. Verify the partition and full coverage before writing.

## Step 5 — Render and report

```sh
python3 "$TOOLS/render.py" --model "$OUT/model.json" --subsystems "$OUT/subsystems.json" --out "$OUT/view.html" --title "$NAME"
python3 "$TOOLS/brief.py"  --model "$OUT/model.json" --subsystems "$OUT/subsystems.json" --out "$OUT/brief.md"  --title "$NAME"
```

Point the human at `$OUT/view.html` (interactive map) and `$OUT/brief.md` (compact
agent digest).

## Final — record telemetry (REQUIRED)

Your last action, always — do not exit before it. Run the `wf-basics` §2
`record_session.py` command now with `--agent wf-discover` and this run's
`--outcome` (`completed`, or `halted`/`escalated` if you stopped early). If the
command itself errors, continue — telemetry never blocks.
