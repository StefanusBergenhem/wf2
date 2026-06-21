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

## Step 2 — Ensure every language has a built extractor

Identify the repo's primary language(s). wf packs one extractor per supported language as a
`readview-<lang>/` directory under `<paths.tools>/discover/` — list that directory to see
which languages are packed. For each language the repo uses:

- **Packed** (a `readview-<lang>/` directory exists) — if its build output is not already
  present, build it with the language's own toolchain per that extractor's `README.md`
  ("Build" section).
- **Not packed** — author and wire an extractor for the language following
  `<paths.tools>/discover/EXTRACTORS.md`, then build it.

If a build fails, or the language's toolchain is not installed, **halt and report the exact
error** — each extractor is compiled with its own language's toolchain, and the spine cannot
run without one for every language in scope.

## Step 3 — Run the mechanical spine

Pass `discover.py` one flag-group per language whose extractor you built — run
`python3 <paths.tools>/discover/discover.py --help` for the flags each packed extractor
takes. Roots are repo-relative:

```sh
python3 <paths.tools>/discover/discover.py --repo . --out "$DIR" --name "$NAME" \
  [--go-roots cmd,internal --go-mod go.mod] \
  [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'src/generated/**']
```

Writes `$MODEL` (component graph) and `$CLUSTERS` (three candidate clusterings:
folder · depgraph · git-cochange) into `$DIR`.

## Step 4 — Scout augmentation (subagent)

Dispatch ONE subagent with `$MODEL` + `$CLUSTERS` — `$MODEL`'s `nodes` is a dict keyed
by uid; full artifact shapes are in `<paths.tools>/discover/README.md`:

- **Reconcile, don't pick a winner.** Synthesize the three clusterings into ONE
  partition (~6–10 subsystems; every uid in exactly one subsystem; a "Shared /
  cross-cutting" bucket is fine). Surface where they disagree.
- **Describe every component** in 1–2 grounded sentences — prefer its existing
  `synopsis`, else its `types`/`functions` signatures; read source only when
  signatures are insufficient.
- **Write `$SUBSYSTEMS`** to the shape in `<paths.tools>/discover/subsystems.example.json`:
  `system_summary`, `subsystems[]` (`name`, `summary`, `members`, `basis`),
  `component_descriptions{uid}` for EVERY uid, and `disagreements[]` (each entry
  `{finding, components}`). Verify the partition and full coverage before writing.

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
