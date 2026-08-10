# reconcile — the [SYS-TC:] proving-tag toolset

`reconcile.py` is wf2's one tag harvester, a **library** (no CLI): it greps the test
tree for `[SYS-TC:<id>]` tags — the durable proof-of-capability lane. What it reports
is **derived from the tests, never stored** — there is no `done` flag to drift out of
sync with the code. Its importers: `register.py` (the derived scenario register),
`retired.py` (the superseded sweep), `design_view/render_design.py` (shipped context),
and `wf pipeline complete-sprint` (the close-time superseded sweep).

**Component-level spec has no tag.** Acceptance criteria are planning-time working
state that live in the transient chain (slice → task contract) and die with it;
"built" is derived from the **merge record** at sprint close. A legacy `[REQ:...]`
token in an older tree is inert; the harvester ignores it.

## The tag contract — what the build writer must satisfy

An e2e test proving a system test case stamps the id **plus its scenario description**:

```
[SYS-TC:<id>] <the case's scenario description>
```

- A plain comment token — **any language, any comment style** (`//`, `#`, `/* */`,
  `<!-- -->`). The harvester greps text, so it is language-agnostic.
- **The description may wrap.** It continues onto the comment lines directly under the
  tag and is harvested whole; it ends at the first line that is no longer that comment —
  code, a blank line, a blank comment line, the block's closer, or another tag.
- **The description rides the tag.** A test's description describes the test, so it
  cannot rot apart from it; the shipped scenario set is the durable
  proof-of-capability record. When two occurrences of one id carry **different
  non-empty texts**, the register flags the row divergent — visibility, never an error.
- **No hash.** Completion is set-membership, not content-equality.
- `<id>` is **repo-unique** (`SYS-TC-<n>`, monotonic — the high-water mark is
  `id_counters.sys_tc` in `.wf/config.yaml`, never reused).
- After a scenario retires, a lingering tag is an inert breadcrumb — unless it was
  **superseded**, in which case `retired.py` reports it as a survivor (below).

**Only proving *test files* count.** A file contributes tags only when its name matches
a test glob — the defaults span Go/TS/JS/Python/Ruby (`*_test.*`, `*.test.*`, `*.spec.*`,
`*_spec.*`, `test_*.*`); extend per caller with extra globs. This is deliberate: a
`[SYS-TC:<id>]` token in a **non-test** file — an archived contract, a skill doc, this
README — is *not* proof. Counting it would raise phantom register rows and false
survivors.

## Coverage is not correctness

A tag proves a proving test **exists and is committed** — nothing more. Two gates
outside this tool make it trustworthy:

1. **Passing** is the merge gate's job — you do not merge red tests.
2. **Test quality** is the review gate's job (`wf-review` + `wf-testing-anti-patterns`).

And for a **capability drain**, tag presence supplies only the *mechanical
precondition*: the drain itself takes a dispatched `wf-adequacy` verdict that the
shipped scenario set covers the capability's whole promise. Tag presence alone has
falsely drained capabilities; never key a drain on tags by themselves.

## register.py — the derived system-test register

```sh
python3 register.py --tests <test-root> [--tests <root> ...] [--test-glob '<glob>' ...] [--out <path>]
```

Renders the harvested tags as a read-only markdown register: each shipped scenario's
id, its description, and every proving test file. Derived on demand, never edited —
regenerate when needed; a stale copy is disposable. `--tests` is repeatable (rows
union across split test trees). Divergent descriptions across one id's tags are
flagged on the row.

The SA reads it when grounding a change: the register is what the system already
provably promises end-to-end — the input every new scenario and requirement is triaged
against, and the candidate set a `wf-adequacy` dispatch judges a capability against.

## retired.py — the superseded SYS-TC sweep

Supersession is the one case where a tag must **not** stay as a breadcrumb: when the SA
supersedes a shipped scenario (the design records a `Supersedes` list), the sprint
building the successor must update or delete the old proving test and its tag.
`retired.py` is the mechanical check that it happened:

```sh
python3 retired.py --ids SYS-TC-2 [SYS-TC-...] --tests <test-root> [--tests <root> ...]
```

Exit `0` when every id is gone; exit `1` when any survives, listing each surviving id
with the files still carrying its tag; exit `2` on input error — including a non-SYS-TC
id (REQ ids are not tagged in code, so sweeping one would be a vacuous pass, not a
verification). Exact-id matching (`SYS-TC-2` never matches `SYS-TC-20`).
`complete-sprint` runs this sweep at close from the slice's Supersedes list; a survivor
lands in the drain report as a finding routed into the next slice, never silently
dropped.
