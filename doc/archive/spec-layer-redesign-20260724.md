# Spec-layer redesign — drain on proof, not coverage (2026-07-24)

The change record for the capability-drain / requirement-tag redesign. Evidence base:
the dems CAP-022/CAP-023 saga (four drains, three re-instatements) plus a four-agent
sweep of the dems tree the same day.

## The evidence, compressed

1. **Every false drain happened with real, passing, CI-run system tests present.**
   The SYS-TC lane's quality was never the problem (no mocked seams, positive-control
   pattern, full set runs on every PR). Each scenario proved a *narrower slice* than
   the capability's promise: SYS-TC-25 proved the per-entity `/validate` read path was
   project-scoped, not the `autoValidate` write paths; nothing covered the fresh-project
   empty-library case (dems L-067) or the startup-memoized vocabulary (dems L-068).
   The REQ-tag drain criterion is *circular*: the design defines what "done" means
   (its REQ decomposition), the tests prove the REQs, reconcile confirms the tags —
   nothing ever checks back against the capability. A decomposition miss passes every
   gate by construction.
2. **What actually ended the loop was adversarial, source-grounded review.** dems'
   drain #4 commit: "verified at source this time rather than from tag presence
   alone", citing `project.go:58` and the `VocabularyRefresher` port. That method is
   C37's role-half, run by hand.
3. **The persisted REQ statement layer is 86–90% dead weight and rots invisibly.**
   150 distinct tagged ids in dems, ~130 pure orphans; REQ-61 carried two
   contradictory statements for two weeks; the harvester is line-wrap-blind; the
   AC-annotation prose caused 839 of 1234 hygiene findings (dems L-074); a hygiene
   sweep mechanically erased two requirements' comment-borne evidence. And persisted
   statements carry spec authority: an agent refactoring works *around* them instead
   of replacing them.
4. **Non-product work never fit the tag model.** Lint-gate/tooling requirements have
   no test home, so reconcile reports them permanently missing (wf-learnings L-024,
   L-063); dems invented "drain by inspection" ad hoc.

## Decisions

### D1 — A capability drains on proof, not coverage

Old: capability drains when its last serving design's `[REQ:]` tags are all present.
New, two gates in wf-sa Phase 1:

- **Mechanical precondition:** every SYS-TC the draining design claims for the
  capability carries its `[SYS-TC:]` tag in the test tree (reconcile), and passing is
  the merge gate's job as before. A capability with no covering system test anywhere —
  neither in the draining design nor in the shipped register — cannot drain
  mechanically at all.
- **Judgment gate:** a dispatched **wf-adequacy** review returns `adequate` — an
  adversarial, source-grounded check that the scenario *set* covers the promise
  (write paths, composition/startup, empty-state, all entity kinds…), never a check
  against the design's own decomposition (that would rebuild the circularity). The
  dispatch carries the draining design's SYS-TC ids as **claimed** scenarios plus the
  register's remaining SYS-TC lane as **candidates** — a capability served by several
  designs across several runs must get credit for proof an earlier design shipped, or
  the drain false-fails forever on paths that are already covered.

`inadequate` → the capability stays open, residual-scoped from the findings — the
dems D-5/D-6 re-instatement pattern moved *before* the drain instead of after it.
Learnings keep the existing drain rule (a learning is a narrow fact; no adequacy
gate).

### D2 — REQ tags become id-only; the statement layer goes transient

`[REQ:<id>]` with **nothing else on the tag line**. The tag's only job is the
backlog-drain signal (reconcile set-membership, unchanged code — the harvester
already tolerates bare tags). The EARS statement lives in the transient chain
backlog → slice → contract and dies with it. After its backlog item drains, the tag
is an inert breadcrumb with no spec authority — nothing to rot, nothing for a
refactoring agent to guiltily preserve, no prose for the hygiene gate to fight.

`[SYS-TC:<id>] <scenario description>` keeps its verbatim description: a system
test's description describes the test itself (governor-clean), and the scenario set
is now the durable proof-of-capability record — the one place user-voice behaviour
survives in the tree.

Consequence: `register.py`'s REQ lane stops being a statement register going
forward (legacy statements still render). The SA grounds new-requirement triage
against the **SYS-TC register + capabilities + ADRs + drills**, not a REQ prose
register.

### D3 — The inspection-proof lane (non-product work)

A requirement whose proof is a gate/config/tooling fact — a lint rule, a CI check, a
doc convention — has no test home by nature. It is declared in the backlog with
`proof: inspection — <the source fact that proves it>` and drains when the SA
verifies that fact **at source and cites `<path>:<symbol-or-line>` in the drain
commit**. This makes dems' ad-hoc "drain by inspection" first-class and dissolves
the reconcile-blindness pair (wf-learnings L-024 + L-063): reconcile is no longer
asked to see proof it structurally cannot.

### D4 — wf-adequacy: the spec-layer reviewer (C37, pilot-side deployment)

New dispatched agent (`agents/wf-adequacy.md`, `model: opus`), read-only on source
(digest is its only write), modeled on wf-drill's dispatch/digest shape. Given a
capability, its claimed scenarios, and the candidate shipped scenarios, it:

1. enumerates from **source** every path that could falsify the capability's promise,
   each with file:line — the sweep classes live in the shared
   `skills/wf-sa/references/promise-sweep.md`, file-Read by both this agent and the
   SA's scenario-authoring reference so author and judge use one taxonomy;
2. maps each path to the scenario (claimed or candidate) that exercises it, or names
   it a **residual**;
3. flags scenarios that are obsolete or superseded-worthy (the pruning direction —
   the e2e suite must not become the new accumulating spec layer);
4. returns `adequate` / `inadequate` + residuals, and appends a digest to
   `paths.drill_cache`.

Dispatched at two points in wf-sa: **Phase 1** (the drain gate — mandatory) and
**Phase 5** (design-time: the slice's new SYS-TC set is checked before build, so a
narrow set is caught before code exists, not four sprints later).

C37 scope note: this ships the **pilot-side** deployment only. The wf2-skill-prose
reviewer and the `wf skills check` linter halves of C37 remain open; C38's
task-contract residuals are *not* subsumed (different altitude).

### D5 — What stays durable

Capabilities (until **proven**, per D1 — no longer until designed or tag-covered),
ADRs, AGENTS.md, **system tests with their SYS-TC tags**, tooling. The REQ layer —
ids aside — is working state with a slice-to-ship lifetime.

## What deliberately did NOT change

- **reconcile/register/retired/next_id code.** The harvester already tolerates bare
  tags; set-membership drain semantics for the backlog are unchanged; retired.py's
  supersession sweep works on ids. Doc contracts updated only.
- **wf-tl's decomposition flow.** ACs, contracts, materialize, sprint check — the
  statement chain through the contract is untouched (it is already transient).
- **The backlog's REQ drain signal.** Still tag-presence via reconcile. Only the
  *capability* drain stops keying on it.
- **Learnings drain.** Still drains with the last serving design (plus D3's
  inspection proof where applicable).

## Touched files

- `agents/wf-adequacy.md` (new)
- `skills/wf-sa/references/promise-sweep.md` (new — shared sweep taxonomy)
- `skills/wf-sa/SKILL.md` (drain pipeline, Phase 1, Phase 3 triage, Phase 5, Phase 6)
- `skills/wf-sa/references/requirement-syntax.md`, `references/system-testcase-syntax.md`
- `skills/wf-sa/assets/design-backlog.md.tmpl`
- `skills/wf-po/SKILL.md`, `skills/wf-po/assets/capabilities.yaml.tmpl`
- `skills/wf-build/SKILL.md`, `skills/wf-review/SKILL.md`
- `tools/reconcile/README.md`, `reconcile.py` docstring
- `CLAUDE.md`, `skills/wf-init/assets/config.yaml.tmpl` (comments)
- `doc/CANDIDATES.md` (C37 pilot-side ship; C10's tag-text trace premise retired)

## Migration note (dems, not done here)

Existing statement-bearing REQ tags in a target repo stay valid — the harvester
reads the id and ignores the rest; they decay naturally as supersession sweeps and
file churn touch them. No mass rewrite. dems additionally carries stale
drain-at-design doctrine in its live `CAPABILITIES.yaml` header — the next
re-install + hand-sync replaces it.
