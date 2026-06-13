# readview-go — reference read-view extractor (Go)

The reference implementation for
[`../../read-view-extractor-guide.md`](../../read-view-extractor-guide.md): a
single-file Go program that derives a compressed, always-fresh map of a Go
codebase **purely from source**, using the standard-library `go/doc` toolchain.
Nothing it emits is maintained — it is regenerated on demand and thrown away.

It is **project-agnostic**: every layout assumption is a flag, and it carries no
domain knowledge. Point it at any Go module.

## Build

```sh
go build -o readview .
```

(Stdlib only — no dependencies, no network.)

## Run

```sh
# A standard Go repo (go.mod at root, packages everywhere):
readview -repo /path/to/repo -mode system

# A repo whose module lives in a subdir, scanning chosen roots:
readview -repo /path/to/repo -gomod backend/go.mod \
         -roots backend/internal,backend/cmd -mode system
```

### Modes (`-mode`)

| mode | output |
|---|---|
| `system` | whole-system map: package list by mass + intra-repo dep edges (+ optional surfaces) |
| `component` | one package's exported types & signatures (`-component <name>`) |
| `diagram` | dependency graph as ASCII + Mermaid |
| `html` | one self-contained HTML page (all of the above, browsable) |
| `ir` | the common IR as JSON — the contract a renderer/spine consumes |

### Flags

| flag | meaning |
|---|---|
| `-repo` | repo root (default `.`) |
| `-roots` | comma-separated dirs under `-repo` to scan (default `.`) |
| `-gomod` | path under `-repo` to `go.mod`, for module identity (default `go.mod`) |
| `-component` | package name, for `-mode component` |
| `-intent` | optional intent-overlay file (see the guide §9) |
| `-routes` | optional file scanned for HTTP routes — **example surface extractor** |
| `-migrations` | optional migrations dir — **example surface extractor** |

The `-routes` / `-migrations` extractors encode *framework conventions* (a
chi-style router; `NNN_name.up.sql` files), included only to demonstrate the
guide's §8 "surface extractor" pattern. They are off by default — swap the
patterns for your own stack.

## What to read

- **`buildIR`** + **`funcSig`** / **`typeShape`** — the entire `code → IR` path
  (Appendix A of the guide). Everything else is rendering.
- This is the *shape to match* when porting to another language, **not** code to
  translate. Your toolchain differs (`tsc`, `ast`, `rustdoc`…); the IR is the
  same.

## Status

Proof of concept. The IR is a proposal, not a frozen schema; the renderers and
the intent overlay are baked into this binary rather than factored out as a
shared, language-agnostic spine. See the guide for the open pieces.
