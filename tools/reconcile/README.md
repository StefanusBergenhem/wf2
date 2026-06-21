# reconcile — requirement-tag harvester (wf2 reconciliation-by-grep)

`reconcile.py` is wf2's drift check. It answers **"which of the design's open
requirements are actually built?"** by grepping the test tree for requirement tags.
Completion is **derived from the tests, never stored** — there is no `done` flag to
drift out of sync with the code. This replaces wf1's whole durable-spec reconciliation
apparatus (the STATE ledger, basis-hashes, `wf check drift`, coverage-verdict) with a
grep.

## What it does

- Reads the design's open requirements, grouped into slices (`--slices <json>`).
- Greps the test tree (`--tests <root>`) for `[REQ:<id>]` tags.
- A requirement is **covered** when a test carries its tag; a **slice** is complete
  when all its requirements are covered; the **backlog** is empty (the design can be
  released) when every slice is complete.
- Reports per-slice completion plus any **orphan/historical tags** — tags for
  requirements not in the current design (e.g. breadcrumbs left by already-retired
  slices). Orphans are informational, never errors.

## The tag contract — what the build/review *writer* must satisfy

When a test proves a requirement, stamp the requirement's id in that test:

```
[REQ:<id>]
```

- A plain comment token — **any language, any comment style** (`//`, `#`, `/* */`,
  `<!-- -->`). The harvester greps text, so it is language-agnostic.
- **No hash.** wf2 verification is hash-free: a reworded requirement does not
  invalidate its tag, because completion is set-membership, not content-equality.
  (This is the deliberate departure from wf1's basis-hash drift mechanism.)
- `<id>` is a **repo-unique** requirement id (`REQ-<n>`, monotonic over the whole repo,
  never reused — a design-local id would collide with a retired design's lingering tag).
  reconcile rejects a duplicate id across the slices it is given.
- After a design retires, its tag remains in the test as a historical breadcrumb (and the
  seed of a future compliance trace); reconcile reports it as an orphan, never an error.

## Allocating ids — `next_id.py` (the writer side)

reconcile is the *reader* ("is this id built?"); `next_id.py` is the *writer* ("what id is
free?"). The SA mints ids with it rather than by hand:

```sh
python3 next_id.py --scan <test-tree> --scan <design-backlog> --scan <adrs> [--count N]
```

It greps every `--scan` path for `REQ-<n>` mentions and prints `max+1 .. max+N`. The scan is
**broad on purpose** — any mention, not only `[REQ:...]` tags — because over-counting only
skips a number (free) while under-counting reuses an id and collides with a retired design's
lingering tag (the one failure). Nothing is stored: the next id is *derived* from what
exists, so there is no high-water-mark counter to maintain. Missing paths are skipped (a
greenfield first call returns `REQ-1`); a `\b` boundary keeps lookalikes like `PREQ-9` out.

## Coverage is not correctness

A tag proves a proving test **exists and is committed** — nothing more. Two gates
outside this tool make "covered" trustworthy:

1. **Passing** is the merge gate's job — you do not merge red tests.
2. **Test quality** is the review gate's job (`review` + `testing-anti-patterns`) — a
   vacuous tagged test would otherwise falsely retire a slice.

Reconcile is necessary but not sufficient; pair it with both.

## Usage

```sh
python3 reconcile.py --slices slices.json --tests <test-root> [--json]
```

`slices.json`:

```json
{ "slices": [
    { "id": "foundation", "requirements": ["REQ-1", "REQ-2"] },
    { "id": "endpoint",   "requirements": ["REQ-3"] }
] }
```

Exit `0` on success (report produced), `2` on input error. In `--json` mode the
top-level `all_complete` field is the **"backlog empty → design releasable"** signal;
each slice carries `complete`, `covered`, and `missing`.

## What it drives

reconcile drives **design-backlog retirement**: the SA removes a requirement (and then an
emptied design) from the committed design backlog once reconcile shows its `[REQ:<id>]` tag
present. When the backlog empties, every designed thing has shipped — its structure is now
the codebase's, re-derived by `discover`.

It does **not** drive capability removal. A capability leaves `CAPABILITIES.yaml` when the
SA *designs* it (drains it into the backlog) — upstream of any test, so that transition is
the SA's, not reconcile's.
