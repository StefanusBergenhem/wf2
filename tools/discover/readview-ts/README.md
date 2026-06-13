# readview-ts — reference read-view extractor (TypeScript / JavaScript)

The TypeScript port of [`../readview-go`](../readview-go), built on the official
**TypeScript Compiler API** (`ts.createProgram` + the type checker). It derives a
compressed, always-fresh map of a TS/JS codebase **purely from source** and emits
the common IR ([`../../read-view-extractor-guide.md` §4](../../read-view-extractor-guide.md))
**verbatim** — the same JSON field names/shape the Go extractor's `-mode ir`
produces — so one language-agnostic spine can union Go + TS IRs into a single
multi-language system view.

Like the Go reference it is **project-agnostic**: every layout assumption is a
flag, and it carries no domain knowledge. Point it at any TS/JS repo.

## Build

```sh
npm install     # installs `typescript` (the toolchain) + @types/node
npm run build   # tsc -> dist/extract.js
```

## Run

```sh
# A repo with a tsconfig (preferred — path aliases like @/ resolve correctly):
node dist/extract.js -repo /path/to/repo -roots src -tsconfig tsconfig.json -mode ir

# DEMS frontend:
node dist/extract.js -repo /path/to/dems \
  -roots frontend/src -tsconfig frontend/tsconfig.app.json -mode ir
```

### Flags

| flag | meaning |
|---|---|
| `-repo` | repo root (default `.`) |
| `-roots` | comma-separated dirs under `-repo` to scan (default `src`) |
| `-tsconfig` | path (under `-repo`) to a `tsconfig.json` — loads the project's compiler options + path aliases via `ts.getParsedCommandLineOfConfigFile`. If omitted, root files are discovered by walking `-roots`. |
| `-exclude` | comma-separated **globs** (repo-relative) whose components are dropped from the IR — for generated/vendored trees you don't want in the read-view. Glob tokens: `**` (any path segments, incl. none), `*` (any chars within a segment), `?` (one char); everything else is literal. A bare dir (`a/b` or `a/b/**`) excludes that dir and everything under it. Edges *to* an excluded component are silently dropped, so no dangling `depends_on` remains. Project-agnostic — the mechanism is generic, the patterns are per-run. |
| `-mode` | `ir` (mandatory; the only mode — single-language render modes are the Go binary's job, the spine renders the merged view) |

### Excluding generated code

Tool-generated trees (protobuf codegen, ORM type dumps, vendored deps) are still
real code, so by default they become components — often large, type-only ones the
intent overlay has nothing to say about (they land in the "uncovered" bucket).
When that noise outweighs its signal, drop it at extraction time:

```sh
# A Buf/protoc-gen-es codegen tree under src/types/proto:
node dist/extract.js -repo /path/to/repo -roots src -tsconfig tsconfig.json \
  -exclude 'src/types/proto/**' -mode ir

# Multiple trees:
-exclude 'src/types/proto/**,src/gen/**,vendor/**'
```

This is a per-run decision: keep the tree in when you *want* the generated surface
mapped, exclude it when it's pure noise. No path is baked into the extractor.

Output (`-mode ir`) is the common IR JSON on stdout — pipe it to a file and feed
it, together with one or more other-language IRs, to the spine.

## The component-unit rule

**A component is a directory containing ≥ 1 non-test, non-declaration `.ts`/`.tsx`
(or `.js`/`.jsx`/`.mjs`) source file.** This mirrors the Go reference's "one
component per package/dir". Concretely:

- A directory owns **only its own files** — it does **not** absorb nested
  subdirectories. `frontend/src/components` and
  `frontend/src/components/projects` are two separate components (this matches how
  a heavy spec layer typically allocates them separately).
- `id` = `path` = the **repo-relative directory path** (e.g. `frontend/src/api`).
  This is stable across edits, so the hand-authored intent overlay can match on
  it. `name` = the last path segment.
- Test files (`*.test.*`, `*.spec.*`, anything under `__tests__/`) and `.d.ts`
  declaration files **never create a component** and are excluded from `loc`.

## Health-signal heuristics

TypeScript has no first-class "package doc" concept, so `has_doc` / `synopsis`
use a documented proxy:

- **`has_doc`** is true iff the component's **entry file** carries a leading
  file-level comment block (a `/** … */` or `//` banner before the first
  statement), **OR** any exported symbol in the component carries JSDoc. The
  *entry file* is `index.*` if present, else the file named after the directory,
  else the alphabetically-first source file.
- **`synopsis`** = the first line/sentence of that leading file comment, taken
  **verbatim**, or `""` when absent. The extractor never invents prose — an empty
  synopsis is a real signal that the intent overlay must supply the *why*.
- **`has_tests`** is true iff a sibling test file (`*.test.*` / `*.spec.*`) or a
  `__tests__/` directory exists alongside the component.
- **`loc`** counts non-blank source lines in the component's own files (tests and
  `.d.ts` excluded).

(The entry-file heuristic means `synopsis` can read like "doorSourceCounts.ts —
…" when a multi-file dir has no `index.*`: it is the banner of whichever file
sorts first. Honest, but a reason to prefer an `index.*` banner per dir, or to let
the intent overlay own the one-liner.)

## What gets reshaped (the compression)

Per component, from the **exported** surface only (enumerated via the type
checker's `getExportsOfModule`, which uniformly handles `export {}`, re-exports,
default exports, and `export const`/arrow functions):

- **types** — `interface` (property + method signatures), `class` (public members
  + method signatures, private members dropped), `enum` (members), `type` alias
  (object-literal members, or a single `= …` line for unions/mapped types). Each
  carries the leading-line of its JSDoc as `doc`.
- **functions** — `function` declarations and exported `const`/arrow **function
  values** (plain data constants are omitted as implementation detail). Each is a
  one-line signature with the body stripped — that stripping *is* the compression.
- **depends_on** — intra-repo import/export-from edges, resolved to component ids
  via the program's module resolution (so tsconfig path aliases like `@/*` are
  honoured). `node_modules` / stdlib edges are dropped.

Everything is **lossy on purpose** — a map, not a mirror. Deterministic: no
network, no clock, no LLM; outputs are sorted for stable diffs; an unparseable
file is skipped, never crashing the run.

## TS-specific gotchas (the durable lesson for future ports)

1. **Use the type checker, not the AST, to enumerate exports.** `export { X } from
   "./y"`, default exports, barrel re-exports, and `export const f = () => …` all
   need different AST handling but are uniform through
   `checker.getExportsOfModule(moduleSymbol)`. Follow `SymbolFlags.Alias` with
   `getAliasedSymbol` to reach the real declaration, then **attribute each symbol
   to the file that *declares* it** (skip if the declaration lives in another
   file) so re-exports don't double-count.
2. **Load the project's `tsconfig`.** Bundler-mode path aliases (`@/* → src/*`)
   only resolve if you parse the real config via
   `ts.getParsedCommandLineOfConfigFile`. Without it, alias imports look
   unresolvable and dependency edges vanish.
3. **No package-doc concept.** Go gives you a package doc comment for free; TS does
   not. You must pick a proxy (see above) and document it — this is the single
   biggest fidelity gap vs Go.
4. **React component "props" leak into signatures.** A function component's
   signature surfaces its destructured-props object (`Foo({ a, b }: Props)`).
   That is honest and informative, but noisier than a Go function signature.
5. **Generated code is still a component.** Tool-generated dirs (e.g. a kanel
   `generated/` tree) show up as large type-only components with `has_doc:true`
   from their `@generated` banner. That is correct (it *is* code), but the intent
   overlay typically has nothing to say about it — expect it in the "uncovered"
   bucket unless intent explicitly allocates it.
6. **Inline object types compact loosely.** Multi-line inline object types
   (`{ a: T\n b: U }`) are whitespace-collapsed to one line, which drops the
   newline separators; readable but not perfectly punctuated. Top-level
   interfaces are unaffected.

## Status

Proof of concept, paired with `../readview-go`. The IR is a proposal, not a frozen
schema. Single-language render modes (`system`/`component`/`diagram`/`html`) are
deliberately **not** implemented here — rendering is the spine's job, and the
spine renders the *merged* multi-language view from IR JSON alone.
