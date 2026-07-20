# reconcile — requirement-tag harvester (wf2 reconciliation-by-grep)

`reconcile.py` is wf2's drift check. It answers **"which of the design's open
requirements are actually built?"** by grepping the test tree for requirement tags.
Completion is **derived from the tests, never stored** — there is no `done` flag to
drift out of sync with the code. This replaces wf1's whole durable-spec reconciliation
apparatus (the STATE ledger, basis-hashes, `wf check drift`, coverage-verdict) with a
grep.

## What it does

- Reads the design's open requirements, grouped into slices (`--slices <json>`).
- Greps the **proving test files** under each `--tests <root>` for `[REQ:<id>]` tags.
  `--tests` is **repeatable** — pass each root of a split test tree (e.g. `--tests
  backend --tests frontend/src`) and coverage is the union across them.
- A requirement is **covered** when a test carries its tag; a **slice** is complete
  when all its requirements are covered; the **backlog** is empty (the design can be
  released) when every slice is complete.

**Only proving *test files* count.** A file contributes tags only when its name matches
a test glob — the defaults span Go/TS/JS/Python/Ruby (`*_test.*`, `*.test.*`, `*.spec.*`,
`*_spec.*`, `test_*.*`); extend per project with a repeatable `--test-glob '<glob>'`. This
is deliberate: a `[REQ:<id>]` token in a **non-test** file — an archived task contract under
the archive dir, a skill doc, this README — is *not* a built requirement. Counting it would
silently mark unbuilt backlog work as covered (a false drain). The same filter keeps
`register.py` and `retired.py` honest (no phantom rows, no false survivors).
- Reports per-slice completion plus any **orphan/historical tags** — tags for
  requirements not in the current design (e.g. breadcrumbs left by already-retired
  slices). Orphans are informational, never errors.

## The tag contract — what the build/review *writer* must satisfy

When a test proves a requirement, stamp the requirement's id **and its full statement**
in that test:

```
[REQ:<id>] <the requirement's full EARS statement, verbatim>
```

(e2e tests likewise: `[SYS-TC:<id>] <the case's scenario description>`.)

- A plain comment token — **any language, any comment style** (`//`, `#`, `/* */`,
  `<!-- -->`). The harvester greps text, so it is language-agnostic.
- **Every tagged test carries the statement** on the tag's line — the tag is where the
  requirement's text survives once the design backlog drains. The harvester captures it
  and reports it per covered id (a bare tag with no text is tolerated for backward
  compatibility); when two occurrences of one id carry **different non-empty texts**, it
  emits a divergent-text **warning** — visibility, never an error.
- **No hash.** wf2 verification is hash-free: a reworded requirement does not
  invalidate its tag, because completion is set-membership, not content-equality —
  the statement is carried and reported, never compared for completion.
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
python3 reconcile.py --slices slices.json --tests <test-root> [--tests <root> ...] \
  [--test-glob '<glob>' ...] [--json]
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
each slice carries `complete`, `covered`, and `missing`. The top level also carries
`statements` (each harvested id → the statement its tags carry) and `warnings`
(the divergent-text entries).

## What it drives

reconcile drives **design-backlog retirement**: the SA removes a requirement (and then an
emptied design) from the committed design backlog once reconcile shows its `[REQ:<id>]` tag
present. When the backlog empties, every designed thing has shipped — its structure is now
the codebase's, re-derived by `discover`.

It also drives **capability removal**, the same way: a capability leaves `CAPABILITIES.yaml`
when the backlog design serving it drains — i.e. when reconcile shows that design's
`[REQ:<id>]` tags shipped and its essence now lives in the code (the tags, plus any ADR). The
SA performs the drain, keyed on that shipped evidence, not on having designed it.

## register.py — the derived requirement register

`register.py --tests <test-root> [--tests <root> ...] [--out <path>]` renders the same harvested tags as a
read-only markdown register — per lane (`REQ`, `SYS-TC`): id, the statement its tag
lines carry, and every proving test file. It answers "what does the system require,
in total, right now?" for a human reader (a new engineer, an auditor) without
reintroducing a hand-maintained spec: the register is derived on demand and never
edited — regenerate it when you need it, and treat a stale copy as disposable.
Divergent statements across one id's tags are flagged on the row.

The SA also reads it when grounding a change: the in-scope entries are what the system
already promises, the input every new requirement is triaged against (unrelated /
extends / supersedes).

## retired.py — the superseded-id sweep

Supersession is the one case where a tag must **not** stay as a breadcrumb: when the SA
supersedes a shipped requirement (the design records a `Supersedes` list), the sprint
building the successor must update or delete the old proving test and its tag.
`retired.py` is the mechanical check that it happened:

```sh
python3 retired.py --ids REQ-4 SYS-TC-2 --tests <test-root> [--tests <root> ...]
```

Exit `0` when every id is gone; exit `1` when any survives, listing each surviving id
with the files still carrying its tag; exit `2` on input error. It sweeps both lanes
with the harvester's exact-id matching (`REQ-2` never matches `REQ-20`). The SA runs it
when draining a backlog design that carried a `Supersedes` list — a survivor is a
finding routed into the next slice, never silently dropped.
