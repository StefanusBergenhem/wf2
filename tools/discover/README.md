# discover — mechanical scripts (agent reference)

The **mechanical half of discovery**: deterministic, LLM-free Python that turns any
repo into a structured component graph + candidate subsystem clusterings. The
`wf-discover` skill drives these scripts (with config-derived paths) and adds the one
LLM step (the scout); this doc covers the scripts themselves and how to point them at
an unfamiliar repo.

Everything written here is **derived and disposable** — it lives in a transient output
dir, is regenerated on demand, and is never committed.

## Data flow

```
per-language extractor (own toolchain)  ─┐
  readview-go (go/doc) → <name>-go.ir.json│
  readview-ts (tsc)    → <name>-ts.ir.json│
                                          ├─► spine.py merge → model.json   (deterministic)
                                          ┘                      │
                                       cluster.py → clusters.json (3 candidate clusterings)
                                                                  │
                            ── LLM step (scout, see the `wf-discover` skill) ──
                                       scout reconciles → subsystems.json
                                                                  │
                          render.py → view.html (human)   brief.py → brief.md (agent)
```

Scripts (all stdlib-only Python; run with `python3`):

| script | role | input → output |
|---|---|---|
| `discover.py` | orchestrates the mechanical half | repo → `model.json` + `clusters.json` |
| `spine.py` | language-agnostic IR merge (the single merge authority) | N `*.ir.json` → `model.json` |
| `cluster.py` | three mechanical clusterings (folder · depgraph · git-cochange) | `model.json` → `clusters.json` |
| `render.py` | interactive two-level HTML (subsystem graph → component drill) | `model.json` + `subsystems.json` → `view.html` |
| `brief.py` | compact markdown digest for an agent to load before planning | `model.json` + `subsystems.json` → `brief.md` |

Per-language extractors live in their own language (a Go dev has Go; a TS dev has Node) —
see `EXTRACTORS.md` to add one. `spine.py`/`cluster.py`/`render.py`/`brief.py` never need
a target toolchain — only Python.

## Artifact shapes

The mechanical half emits two JSON artifacts; the scout reads both and emits a third.
The field names below are what `render.py` / `brief.py` actually consume — produce
exactly these.

**`model.json`** (spine output) — `{languages, title, order, nodes}`. **`nodes` is a
dict keyed by uid** (not a list): `nodes["<uid>"] -> {uid, id, name, path, module, kind,
lang, loc, has_doc, has_tests, deps: [uid], synopsis, types: [...], functions: [{name,
signature, doc}]}`. A uid is `<lang>:<path>` (e.g. `go:internal/auth`). Ground a
component description in `synopsis` first, then the `types` / `functions` signatures.

**`clusters.json`** (cluster output) — `{candidates, depgraph_hubs, git_stats}`.
`candidates` holds the three mechanical clusterings as `{folder, depgraph, gitcochange}`,
each a dict of `cluster-label -> [uid]`. They are candidate groupings for the scout to
reconcile, never a winner to pick.

**`subsystems.json`** (scout output — the contract `render.py` / `brief.py` consume).
The full, copy-pasteable instance is `subsystems.example.json` in this directory;
`_contract.py` validates it at load and fails with a precise message if a required field
is missing. Required vs optional:

| field | shape | required |
|---|---|---|
| `subsystems[]` | `{name, members: [uid], summary?, basis?}` | **yes** — `name` and `members` required per entry |
| `component_descriptions` | `{uid: description}`, one per component | recommended (a blank renders as "no description") |
| `disagreements[]` | `{finding: string, components: [uid]}` | optional list, but `finding` is **required in each entry** |
| `system_summary` | string | optional |

## One-time setup (per checkout)

Run from this directory. The Python scripts are stdlib-only (no venv, no pip). Build the
extractor for each language the repo uses:

```sh
cd readview-go && go build -o readview .     # if the repo has Go
cd ../readview-ts && npm install && npm run build   # if the repo has TS/JS
```

The graph lib for offline views ships with the toolkit (`tools/graphview/vendor/`, shared
by every view) — no fetch needed. If it is somehow absent the views fall back to a CDN
`<script>` (needs internet).

## Run the mechanical half

The `wf-discover` skill supplies `<DIR>`/`<NAME>` from config and orchestrates the run.
The script CLI, run from this directory:

```sh
python3 discover.py --repo <REPO> --out <DIR> --name <NAME> \
  [--go-roots cmd,internal --go-mod go.mod] \
  [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'src/generated/**']
```

### Determining the flags on an unfamiliar repo

- **Go present?** `find <repo> -name go.mod -not -path '*/node_modules/*'`. Pass its path
  (relative to `--repo`) as `--go-mod`; pass the top-level source dirs beside it as
  `--go-roots` (e.g. `cmd,internal`, or `backend/cmd,backend/internal` if the module is in
  a subdir).
- **TS/JS present?** Find the app `tsconfig` (often `tsconfig.app.json` for Vite, else
  `tsconfig.json`) and pass it as `--ts-tsconfig` so path aliases (`@/*`) resolve. Point
  `--ts-roots` at the source root (e.g. `src`, `web/src`, `frontend/src`).
- **Exclude generated/vendored trees** (protobuf codegen, ORM dumps) via `--ts-exclude`
  globs — they are real code but pollute the map.
- **git co-change needs real history.** On a shallow clone the signal is empty; run
  `git -C <repo> fetch --unshallow` first, or accept folder+depgraph only.

## After the mechanical half

The `wf-discover` skill dispatches the scout (the one LLM step) to reconcile
`clusters.json` into `subsystems.json`, then renders:

```sh
python3 render.py --model <MODEL> --subsystems <SUBSYSTEMS> --out <VIEW>  --title "<NAME>"
python3 brief.py  --model <MODEL> --subsystems <SUBSYSTEMS> --out <BRIEF> --title "<NAME>"
```

## Artifacts

| file | layer | who reads it | durability |
|---|---|---|---|
| `model.json` | structure | spine/cluster/render/brief, scout | derived, disposable |
| `clusters.json` | structure | scout | derived, disposable |
| `subsystems.json` | semantics | render, brief, downstream planners | **semantic cache** — machine-owned, regenerated, never hand-edited |
| `view.html` | view | human | derived, disposable (self-contained, offline) |
| `brief.md` | view | **agent** (load before planning) | derived, disposable |

`model.json` and the views cannot rot (regenerated from code). `subsystems.json` is the
only LLM-produced artifact; it self-heals on the next run. None of it is committed.
