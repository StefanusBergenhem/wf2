---
name: wf-discover
description: Derives a transient subsystem read-view of a repo — a mechanical structure spine plus LLM-scout descriptions, rendered to an interactive HTML map and an agent brief. Run when orienting on an unfamiliar repo before planning.
---

# wf-discover

**Read `wf-basics` first for the `.wf/` layout and config rules**. Resolve every path
below from `.wf/config.yaml`:

- `DIR`         = `paths.discover`        (working dir, cleared each run)
- `MODEL`       = `paths.discover_model`
- `CLUSTERS`    = `paths.discover_clusters`
- `SUBSYSTEMS`  = `paths.discover_subsystems`
- `VIEW`        = `paths.discover_view`
- `BRIEF`       = `paths.discover_brief`
- `NAME`        = `project.name`

The discover tools live under `<paths.tools>/discover/`. Pipeline:
`extract → spine merge → cluster` (mechanical) → scout (LLM) → `render`.

## Step 1 — Clear previous output

```sh
rm -rf "$DIR" && mkdir -p "$DIR"
```

## Step 2 — Verify extractors

Determine the repo's languages (Go → `go.mod`; TS/JS → `package.json`/`tsconfig.json`).
For each present language, confirm its extractor is built:

- Go: `<paths.tools>/discover/readview-go/readview` exists
- TS/JS: `<paths.tools>/discover/readview-ts/dist/` exists

If a present language's extractor is missing, **halt and ask the human** whether to
build it (build steps in `<paths.tools>/discover/EXTRACTORS.md`). Do not continue without it.

## Step 3 — Run the mechanical spine

Pass one flag-group per present language. Roots are repo-relative.

```sh
python3 <paths.tools>/discover/discover.py --repo . --out "$DIR" --name "$NAME" \
  [--go-roots cmd,internal --go-mod go.mod] \
  [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'src/generated/**']
```

Writes `$MODEL` (component graph) and `$CLUSTERS` (three candidate clusterings:
folder · depgraph · git-cochange) into `$DIR`.

## Step 4 — Scout augmentation (subagent)

Dispatch ONE subagent with `$MODEL` + `$CLUSTERS`:

- **Reconcile, don't pick a winner.** Synthesize the three clusterings into ONE
  partition (~6–10 subsystems; every uid in exactly one subsystem; a "Shared /
  cross-cutting" bucket is fine). Surface where they disagree.
- **Describe every component** in 1–2 grounded sentences — prefer its existing
  `synopsis`, else its `types`/`functions` signatures; read source only when
  signatures are insufficient.
- **Write `$SUBSYSTEMS`**: `system_summary`, `subsystems[]` (`name`, `summary`,
  `members`, `basis`), `component_descriptions{uid}` for EVERY uid,
  `disagreements[]`. Verify the partition and full coverage before writing.

## Step 5 — Render and report

```sh
python3 <paths.tools>/discover/render.py --model "$MODEL" --subsystems "$SUBSYSTEMS" --out "$VIEW"  --title "$NAME"
python3 <paths.tools>/discover/brief.py  --model "$MODEL" --subsystems "$SUBSYSTEMS" --out "$BRIEF" --title "$NAME"
```

Point the human at `$VIEW` (interactive map) and `$BRIEF` (compact agent digest).

## Final — record telemetry (REQUIRED)

Your last action, always — do not exit before it. Run the `wf-basics` §2
`record_session.py` command now with `--agent wf-discover`, this run's `--outcome`
(`completed`, or `halted`/`escalated` if you stopped early), and the two session
feedback answers (`--wf-friction`, `--repo-observation` — "none" is fine; omit a
flag when there is nothing concrete). If the command itself errors, continue —
telemetry never blocks.
