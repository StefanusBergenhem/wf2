# Writing a read-view extractor for a new language

An extractor turns one language's source into the **common IR** (a plain JSON shape).
The spine (`spine.py`) merges N IRs into one model regardless of which language each
extractor was written in, so adding a language is a self-contained job: write the
adapter, emit the IR, and wire it into the orchestrator (`discover.py`). The spine,
clustering, renderers, and scout never change.

Two worked references ship in this folder — read them before starting:

- **`readview-go/main.go`** — Go, on the stdlib `go/doc` toolchain.
- **`readview-ts/extract.ts`** — TypeScript, on the `tsc` compiler API.

They are the *shape to match*, not code to translate — your toolchain differs; the IR
is the same.

## The two rules that make it work

1. **Never store what the code can tell you — derive it.** The read-view is regenerated,
   never committed as truth. If you want to "cache and keep it in sync," stop — that is
   the drift problem this approach exists to kill. (The one cached layer, scout
   descriptions, is owned by the scout, not the extractor.)
2. **Call the language's own toolchain — never write your own parser.** Every mainstream
   language ships an authoritative way to read its structure with doc comments attached
   (see the cheat sheet). Your extractor is a *thin reshaper* of that tool's output. If
   you are writing a tokenizer, you have taken a wrong turn.

Corollary: an extractor carries **no project or domain knowledge.** It knows packages,
types, imports, and doc comments — never what the system *does*. Layout (scan roots,
tsconfig path) is *configuration* passed as flags, never constants baked into the code.

## The contract: the common IR

This is the only interface you must satisfy. Your job is "source → this JSON," emitted on
stdout (the reference extractors use `-mode ir`).

```jsonc
{
  "module":   "github.com/acme/orders",   // repo/module identity — derive it, don't hardcode
  "language": "go",                        // tag the spine namespaces ids by (lang:id)
  "components": [
    {
      "id":         "internal/pricing",    // STABLE, unique within the language; the spine
                                           //   resolves depends_on against this (and name)
      "name":       "pricing",             // short display name (usually last path segment)
      "path":       "internal/pricing",    // repo-relative dir path (used to match across langs)
      "loc":        612,                   // non-blank source lines, tests excluded
      "kind":       "package",             // package | module | crate | namespace — your call
      "synopsis":   "Computes order totals and tax.",  // FIRST LINE of the doc, or "" — never invent
      "depends_on": ["catalog", "money"],  // INTRA-REPO edges only; ids or short names
      "has_doc":    true,                  // carries a module/package-level doc comment?
      "has_tests":  true,                  // a sibling test file/target exists?
      "types": [
        { "name": "Quote", "kind": "struct",
          "doc": "A priced basket snapshot.",
          "members": ["Subtotal Money", "Lines []LineItem"],
          "methods": ["Total() Money", "WithCoupon(c Coupon) (Quote, error)"] }
      ],
      "functions": [
        { "name": "Price",
          "signature": "Price(basket Basket, rules []Rule) (Quote, error)",
          "doc": "Prices a basket against the active rule set." }
      ]
    }
  ]
}
```

Field notes:

- **`synopsis` / `doc`** come *verbatim* from the language's doc comments. Where the author
  wrote nothing, leave it empty — do **not** invent prose. Empty synopses are a feature:
  they are exactly the components the scout must describe. (If the language has no
  package-doc concept — e.g. TypeScript — pick a documented proxy and write it down; see
  `readview-ts` for the file-banner/JSDoc proxy.)
- **`signature`** is the symbol's signature with the **body stripped**. Stripping bodies
  *is* the compression.
- **`depends_on`** holds component identities, not raw import paths — resolve and shorten.
  The spine matches each entry against component `id`, then `name`, then last path
  segment (intra-language only), so emitting either the full `id` or the short name works;
  be consistent.
- Only the **exported** surface (public types/functions). Private members are
  implementation detail — drop them.
- Everything is **lossy on purpose.** You are building a map, not a mirror.

(`spine.py` consumes `module`, `language`, and `components` today. You may emit extra keys
— e.g. a `surfaces` block for HTTP routes / migrations — but the current spine ignores
them; add the consumer before relying on it.)

## The recipe

1. **Adopt the native toolchain.** Find the official API that reads your language's
   structure with doc comments (cheat sheet below). Spike "parse one package, print its
   exported names" before anything else.
2. **Define your unit of "component"** — the smallest cohesive grouping the ecosystem
   already uses: package (Go), directory/module (TS), package/module (Python),
   module/crate (Rust), package (Java). Be consistent; one component per unit.
3. **Per component, pull from the toolchain:** the doc synopsis, the exported types (with
   members + method signatures), the exported free functions (with signatures + doc).
4. **Derive dependency edges** from imports — intra-repo only.
5. **Compute health signals:** `has_doc`, `has_tests`, and `loc`.
6. **Emit the IR and stop.** Hand it to the spine. Resist adding more.

Pseudo-code — it is barely more than a reshaper:

```
program = toolchain.load(repoRoot, roots, config)   # config/roots are FLAGS
for unit in program.components():
    emit Component(
      id=unit.path, name=lastSeg(unit.path), path=unit.path, loc=unit.loc,
      synopsis=firstLine(unit.doc), has_doc=(unit.doc != ""), has_tests=existsTestFor(unit),
      depends_on=[shorten(i) for i in unit.imports if isIntraRepo(i)],
      types=[reshape(t) for t in unit.exportedTypes()],
      functions=[reshape(f) for f in unit.exportedFuncs()])
```

## Wire it into `discover.py`

Emitting the IR is half the job — the orchestrator (`discover.py`) is what invokes the
extractor and feeds the spine. It dispatches one **explicit branch per packed extractor**;
add yours alongside the `readview-go` / `readview-ts` branches (they are the template — Go
shows the compiled case, TS the interpreted one):

1. **Add the extractor's flags** to the argparse — at least `--<lang>-roots`, plus any
   config flag your extractor needs (e.g. a `--<lang>-config` for a build manifest). Layout
   is configuration, never a constant.
2. **Add an invocation branch**, guarded by that roots flag, mirroring the existing two:
   resolve the built artifact under `<tools>/readview-<lang>/` and **exit with the build
   command** if it is absent (a missing build is then a clear message, not a crash); run the
   extractor in `-mode ir`, capturing stdout to `<out>/<name>-<lang>.ir.json`; append that
   path to `irs`.

```python
if a.<lang>_roots:
    art = os.path.join(TOOLS, "readview-<lang>", "<built artifact>")   # binary, or e.g. dist/extract.js
    if not os.path.exists(art):
        sys.exit(f"<lang> extractor not built: run `cd {TOOLS}/readview-<lang> && <build cmd>`")
    ir = os.path.join(a.out, f"{a.name}-<lang>.ir.json")
    # compiled -> [art, ...] ; interpreted -> [interpreter, art, ...]  (cf. the go vs ts branches)
    run([<run art>, "-repo", a.repo, "-roots", a.<lang>_roots, "-mode", "ir"], out_path=ir)
    irs.append(ir)
```

3. **Update the module `Usage` docstring** to list the new flag-group.

Nothing downstream of the merge changes — the spine, clustering, scout, and renderers
consume the merged IR, which is language-blind by construction.

## Non-negotiables (do / don't)

- ✅ Call the native toolchain.  ❌ Never hand-roll a parser/regex over source for structure.
- ❌ No project/domain knowledge or app-specific paths baked in. Layout is configuration.
- ✅ Intra-repo edges only. Drop stdlib/third-party imports — that noise isn't architecture.
- ✅ Fail soft. Unparseable file → skip it; never crash the whole run for one bad file.
- ✅ Deterministic. No LLM, no network, no clock. Same code in → same map out. Sort outputs
  for stable diffs.
- ✅ Stable component `id`s. The scout map and any intent overlay match on them; churning
  ids makes downstream falsely stale.
- ❌ Never persist the output as truth. It is regenerated on demand.

## Per-language cheat sheet

Map fidelity tracks how much the language exposes *statically* — static, doc-tooled
languages give the richest maps.

| Language | Authoritative toolchain | Component unit | Notes |
|---|---|---|---|
| **Go** | `go/doc` + `go/parser` (stdlib) | package | Reference impl. Hands you synopsis, exported types, methods, signatures directly. |
| **TypeScript / JS** | TypeScript Compiler API (`ts.createProgram` + checker), or `ts-morph` | dir / module | Reference impl. Use the checker's `getExportsOfModule` (handles re-exports/barrels/defaults). Load the real `tsconfig` so path aliases resolve. No package-doc → use a banner/JSDoc proxy. |
| **Python** | `ast` (stdlib) + `inspect` | package / module | Module docstring = synopsis; `ast` for signatures; resolve intra-repo imports to packages. |
| **Rust** | `rustdoc --output-format json` (nightly) or `syn` | crate / module | rustdoc JSON gives docs + signatures; module tree = components. |
| **Java / Kotlin** | language server / `javadoc` doclet / `KSP` | package | Package-info docs; public types + method signatures. |

The fastest way to a new extractor: copy the structure of `readview-go` or `readview-ts`,
swap the toolchain calls, keep the IR emission identical.

## Checklist for a new extractor

- [ ] Uses the native toolchain (no hand-rolled parser).
- [ ] Emits the IR exactly (field names + shape above); validated by feeding it to
      `spine.py merge` and getting a clean `model.json`.
- [ ] Wired into `discover.py` — flag-group + invocation branch + `Usage` docstring
      updated; a `discover.py --<lang>-roots …` run produces `model.json` + `clusters.json`.
- [ ] Component unit is consistent; `id`s are stable and unique.
- [ ] `depends_on` is intra-repo only and resolves (no dangling edges after merge).
- [ ] `synopsis` is verbatim-or-empty; no invented prose.
- [ ] All layout is flags, no domain/project constants.
- [ ] Deterministic and fail-soft; outputs sorted.
- [ ] Ran against a foreign repo of that language with flag-only changes (the generality
      test the references passed).
