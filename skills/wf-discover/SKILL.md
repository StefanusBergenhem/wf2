---
name: wf-discover
description: Derive an interactive subsystem read-view of any repo for human planning — a mechanical structure spine (extract → merge → cluster) augmented with LLM-scout descriptions, rendered to a transient interactive HTML. Use when starting on an unfamiliar repo or re-orienting before planning.
---

# discover — interactive subsystem read-view

Produces one self-contained, interactive HTML map of a codebase, stored in a
**transient folder** (regenerated on demand, never committed). The human uses it to
orient and plan.

The pipeline is **mechanical spine + LLM augmentation**:

```
extract (per language, in its own toolchain)   ─┐
                                                 ├─► model.json   (deterministic, free)
spine.py merge                                  ─┘
cluster.py  → clusters.json   (3 candidate clusterings: folder · depgraph · git-cochange)
   ↓
SCOUT (LLM) → subsystems.json (reconcile candidates + 1-2 sentence description per
                               subsystem AND per component)
   ↓
render.py   → view.html       (interactive: subsystem graph → drill to component view)
```

Nothing here is durable. `model.json` and the read-view are derived from code; the
scout's `subsystems.json` is a machine-owned **semantic cache** (regenerated, never
hand-edited). All of it lives under a transient `--out` dir.

## One-time setup (per checkout)

The Python machinery (`spine.py`, `cluster.py`, `render.py`, `discover.py`) is
**stdlib-only** — the venv needs no third-party packages. The language extractors live
in their own language and are built once (a Go dev has Go; a TS dev has Node):

```sh
cd tools && python3 -m venv .venv                          # no pip install needed (stdlib only)
mkdir -p discover/vendor && curl -fsSL \
  https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js \
  -o discover/vendor/vis-network.min.js                    # graph lib → inlined → offline HTML
cd discover/readview-go && go build -o readview .          # if the repo has Go
cd ../readview-ts && npm install && npm run build          # if the repo has TS/JS
```

The graph library is **vendored and inlined** into `view.html`, so the output opens
with no internet. If `tools/discover/vendor/vis-network.min.js` is absent, `render.py`
falls back to a CDN `<script>` (needs internet) — fetch it once for fully-offline views.

The mechanical scripts and how to point them at an unfamiliar repo are documented in
[`tools/discover/README.md`](../../tools/discover/README.md); adding a new-language
extractor is in [`tools/discover/EXTRACTORS.md`](../../tools/discover/EXTRACTORS.md).

## Step 1 — mechanical spine (deterministic, no LLM)

Run `discover.py` with one flag-group per language present in the target repo. Roots
are repo-relative; for Go in a subdir pass `--go-mod`; exclude generated trees.

```sh
OUT=.tmp/<name>
tools/.venv/bin/python tools/discover/discover.py --repo <REPO> --out $OUT --name <name> \
  [--go-roots cmd,internal --go-mod go.mod] \
  [--ts-roots src --ts-tsconfig tsconfig.json --ts-exclude 'src/generated/**']
```

Produces `$OUT/model.json` (merged component graph) and `$OUT/clusters.json` (the
three candidate clusterings + damped hubs + git stats). Note: the git-cochange signal
needs real history — on a shallow clone it is empty and the scout falls back to
folder+depgraph.

## Step 2 — scout augmentation (LLM)

Dispatch ONE scout subagent. Give it `$OUT/model.json` + `$OUT/clusters.json` and this
contract:

- **Reconcile, don't pick a winner.** folder = author intent, depgraph = static
  coupling, git-cochange = temporal coupling. Synthesize into ONE grouping; surface
  the places they disagree (especially cross-language couplings git shows but static
  imports cannot). Target ~6–10 subsystems; a full partition (every uid in exactly one
  subsystem); a "Shared / cross-cutting" bucket for damped hubs is fine.
- **Describe every component** in 1–2 grounded sentences. Prefer the component's
  existing `synopsis` (doc-derived); else derive from its `types`/`functions`
  signatures in `model.json`; read a source file only when signatures are
  insufficient (budget ~10–15 reads). No generic filler.
- **Write `$OUT/subsystems.json`** with: `system_summary`, `subsystems[]`
  (`name`, `summary`, `members`, `basis`), `component_descriptions{uid: text}` for
  EVERY uid, and `disagreements[]`. Verify the partition and that every uid has a
  description before writing.

## Step 3 — render (human view + agent brief)

```sh
tools/.venv/bin/python tools/discover/render.py --model $OUT/model.json \
  --subsystems $OUT/subsystems.json --out $OUT/view.html --title "<name>"   # human, interactive
tools/.venv/bin/python tools/discover/brief.py  --model $OUT/model.json \
  --subsystems $OUT/subsystems.json --out $OUT/brief.md  --title "<name>"   # agent, compact markdown
```

Open `$OUT/view.html` (a human orienting):

- **System level** — a graph of subsystems and how they connect; click a subsystem to
  enter it.
- **Component level** — the subsystem's components and their interactions: solid edges
  are internal, dashed edges cross to another subsystem (the external component is
  shown as a ghost node tagged with its subsystem; click it to jump there).
- The side panel always shows the selected subsystem/component's description, health
  (doc/tests), and its dependencies + dependents.

`$OUT/brief.md` is the **agent-facing** projection of the same data — a compact markdown
digest (system summary + subsystems with per-component one-liners + cross-cutting
couplings) for a downstream planning agent to load at the top of its session. Same
semantic-cache rules: derived, regenerated, never hand-edited.

## When to re-run

Re-run from Step 1 whenever the code has moved enough that the map feels stale — it is
cheap and disposable. The structure half is always fresh by construction; only the
scout descriptions are cached, and re-running regenerates them.
