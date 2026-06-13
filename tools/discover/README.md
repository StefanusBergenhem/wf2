# discover — mechanical scripts (agent reference)

This is the **mechanical half of discovery**: deterministic, LLM-free Python that turns
any repo into a structured component graph + candidate subsystem clusterings. An agent
(or the `discover` skill) runs these, then adds the one LLM step (the scout) and renders.

Everything written here is **derived and disposable** — it lives in a transient output
dir, is regenerated on demand, and is never committed. Do not treat any of it as durable
truth; when in doubt, re-run.

## Data flow

```
per-language extractor (own toolchain)  ─┐
  readview-go (go/doc) → <name>-go.ir.json│
  readview-ts (tsc)    → <name>-ts.ir.json│
                                          ├─► spine.py merge → model.json   (deterministic)
                                          ┘                      │
                                       cluster.py → clusters.json (3 candidate clusterings)
                                                                  │
                            ── LLM step (scout, see ../../skills/discover/SKILL.md) ──
                                       scout reconciles → subsystems.json
                                                                  │
                          render.py → view.html (human)   brief.py → brief.md (agent)
```

Scripts (all stdlib-only Python; run with the shared venv `tools/.venv/bin/python`):

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

## One-time setup (per checkout)

```sh
cd tools && python3 -m venv .venv                    # stdlib only — no pip install
mkdir -p discover/vendor && curl -fsSL \
  https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js \
  -o discover/vendor/vis-network.min.js              # graph lib → inlined → offline HTML
cd discover/readview-go && go build -o readview .    # if the repo has Go
cd ../readview-ts && npm install && npm run build    # if the repo has TS/JS
```

## Run the mechanical half

```sh
OUT=.tmp/<name>
tools/.venv/bin/python tools/discover/discover.py --repo <REPO> --out $OUT --name <name> \
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

1. Dispatch the **scout** (the one LLM step) per `../../skills/discover/SKILL.md`:
   reconcile `clusters.json` into `subsystems.json` (named subsystems + a 1–2 sentence
   description for every component + cross-signal disagreements). Full partition required.
2. Render:
   ```sh
   tools/.venv/bin/python tools/discover/render.py --model $OUT/model.json --subsystems $OUT/subsystems.json --out $OUT/view.html --title "<name>"
   tools/.venv/bin/python tools/discover/brief.py  --model $OUT/model.json --subsystems $OUT/subsystems.json --out $OUT/brief.md  --title "<name>"
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
