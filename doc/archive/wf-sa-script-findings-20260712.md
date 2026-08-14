# wf-sa script findings — reconcile toolkit (2026-07-12)

**Source:** dems, `wf-sa` session digesting the `sprint-20260711-typed-edge-hardening`
learnings. Findings hit while running the Phase-1 drain (`reconcile.py`) and grounding
the id space (`next_id.py`). Everything here is about the scanning scripts wf-sa drives:
`tools/reconcile/{reconcile,register,retired,next_id}.py`. Line refs are the installed
dems copy (`.wf/tools/reconcile/`), which is a verbatim render of the wf2 source
(`~/repos/wf2/tools/reconcile/`) — fix at source.

Legend: **[NEW]** not previously logged · **[DUP]** already a CANDIDATES entry, second
data point · **[OK]** verified working-as-intended, recorded so it isn't re-investigated.

---

## F1 — [NEW · HIGH] `harvest()` scans *every* file under `--tests`, not just tests → false coverage

**What.** `reconcile.harvest()` (`reconcile.py` L67–86) does `os.walk(tests_root)` and
greps **every** file it finds for the tag regex `TAG_RE` (L50,
`\[(?:REQ|SYS-TC):\s*([\w.:-]+)\s*\]`). There is **no filter** — not by filename
(`*_test.go`, `*.test.ts`), not by an exclude list. Any file anywhere under the given
root that contains a literal `[REQ:REQ-90]` / `[SYS-TC:SYS-TC-19]` token counts as
*coverage* for that id.

**Why it bites.** Several non-test locations carry those literal tokens by design:

- `.wf/archive/<sprint>/…__sprint.yaml` — archived task contracts quote the REQ/SYS-TC
  ids they were built against. Verified in dems: the `sprint-20260711-typed-edge-hardening`
  and `sprint-20260708-physical-layer-import-rebuild` archive snapshots both contain
  bracket-format `[SYS-TC:SYS-TC-19]`-style tokens.
- `.claude/skills/wf-*/…` and `.wf/tools/reconcile/README.md` — skill docs and the tool's
  own README use `[REQ:<id>]` in worked examples.

Measured: harvesting `.wf/archive/`, `.claude/skills/`, and `.wf/tools/` alone yields
**9 distinct REQ/SYS-TC ids** that are *not* proving tests. If an operator points
`--tests` at the repo root, every one of those is reported `covered`.

**Consequence.** `reconcile.py` **drains real backlog requirements it should not** — a
requirement that was never built shows complete because an archived contract or a doc
mentions its tag. This is silent: the drain removes it from `design-backlog.md` and the
work is lost from the plan. The same shared `harvest()` also mis-serves the siblings:

- `retired.py` (imports `harvest`, L23) reports a superseded id as a **surviving tag**
  when it lingers only in an archived contract — a false "the build failed to remove it"
  alarm.
- `register.py` (imports `harvest`, L27) lists doc/archive tokens as real requirements in
  the derived register.

**Why it nearly happened here.** The natural workaround for the split-tree limitation
(F2 below) is `--tests .` — which is *exactly* the contaminated root. The two findings
compound: the single-root limit pushes you toward the repo root, and the unfiltered walk
then punishes it. In this session I avoided it only by writing a custom harness that
harvested `backend/` and `frontend/` and unioned the ids — the skill's one-line
`reconcile.py --tests <the test tree>` gives no such guard.

**Fix options (source: `tools/reconcile/reconcile.py`).**
1. Restrict `harvest()` to test files — a filename predicate (config-driven glob, e.g.
   `*_test.go`, `*.test.ts`, `*_test.py`) is the honest scope of "the test tree."
2. Or an exclude set (skip `.wf/`, `.claude/`, `.git/`, plus a config `paths.transient` /
   `paths.archive`) so machine-owned trees never count as coverage.
3. Minimum: the skill text and the tool README must state that `--tests` **must not**
   include `.wf/archive`, `.claude/skills`, or the tools dir.

Option 1 is the real fix — "test tree" should mean tests, mechanically.

---

## F2 — [DUP of C28] `--tests` takes a single root; split test trees need N invocations + a manual union

**What.** `reconcile.py` / `register.py` / `retired.py` each take one `--tests` root.
dems has two (`backend/` Go tests, `frontend/src/` vitest); a design slice spans both. A
single invocation can't cover them, and no single *real* root does either — the only
common ancestor is the repo root, which trips F1. I worked around it by importing
`reconcile.harvest` as a module and unioning `harvest('backend')` + `harvest('frontend')`
by hand.

**Status.** Already logged as **C28** (2026-07-12) and related to **C15** (the "test
tree" path with ≥3 callers, no config key). This session is a second data point, and it
sharpens the fix: making `--tests` a **repeatable** flag (C28's proposal) *also* mitigates
F1, because it removes the reason to reach for `--tests .`. Do them together.

---

## F3 — [NEW · MED] Reader/writer asymmetry: `next_id.py` is polyglot-safe and over-scan-safe; the readers are neither

**What.** `next_id.py` (the *writer*) already solved both problems the *readers* have:

- `--scan` is **repeatable** (`action="append"`, L71) — it takes `backend/`, `frontend/`,
  the backlog, and the ADRs in one call.
- Broad scanning is **safe by design** (L8–16): it greps any `REQ-<n>` mention, not just
  tags, because over-counting only skips a number while under-counting collides. So
  scanning an archive or a doc is harmless for the writer.

The readers (`reconcile`/`register`/`retired`) have the mirror-image properties: single
root (F2) *and* over-scanning is **unsafe** (F1 — a stray mention is a false completion,
not a skipped number). The design symmetry ("writer mints, reader harvests, same homes")
is broken precisely where it matters: the writer tolerates a broad/multi scan, the reader
is corrupted by one.

**Fix.** Bring the readers up to the writer's interface: repeatable `--tests` (F2) plus a
test-file filter (F1). After that the two sides scan the same way and the mental model
("scan the id homes") holds for both.

---

## F4 — [OK] Divergent-statement warnings fire correctly

`reconcile` emitted divergent-statement warnings for `REQ-61` and `SYS-TC-3` — two tags of
one id carrying different trailing text. Verified this is working as intended
(informational, never an error; L96–107, README "visibility, never an error"). Expected
here: the typed-edge sprint's REQ-90/91 "extend REQ-61", so REQ-61's statement was
restated in more than one place. No action — recorded so it isn't mistaken for a defect
next time.

---

## F5 — [NEW · LOW] Skill Phase-1 text under-specifies the reconcile invocation

`wf-sa/SKILL.md` Phase 1 says *"Reconcile `$DESIGN_BACKLOG` against the test tree"* and
shows `reconcile.py … --tests <the test tree you reconcile against>` — singular, with no
mention that (a) a split-tree repo needs more than one root, or (b) the root must exclude
machine-owned trees carrying literal tags. An SA following the text literally on a
polyglot repo either under-covers (one tree) or over-covers (`--tests .`, F1). Until the
tools are fixed, the skill should name the hazard.

---

## Recommendation

Promote **F1** to a new CANDIDATES entry (added as **C30**, cross-linking this doc) — it
is the one *correctness* bug here (silent backlog drain), distinct from the C28/C15
*ergonomics* limitation. Fix F1 + F2 together in `tools/reconcile/`: repeatable `--tests`
+ a test-file filter (or exclude set), mirroring `next_id.py`'s already-correct
`--scan`. That single change closes F1, F2, F3, and defuses F5.
