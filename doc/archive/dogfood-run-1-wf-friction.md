# Dogfood run 1 — wf friction findings

**Date:** 2026-07-09
**Source:** `dems/.wf/telemetry/sessions.jsonl` (first end-to-end dogfood run, 2026-07-06 → 2026-07-08)
**Scope:** the `wf_friction` channel only — friction with the wf toolkit itself. Repo-specific
observations about the dems code (the `repo_observation` channel) are excluded; they belong in
dems' own backlog, not here.

> `wf-retrospective` never ran on this telemetry (both `LEARNINGS.yaml` and `wf-learnings.yaml`
> are still empty), so these findings have not been distilled or drained yet. This report is the
> manual stand-in. When retrospective does run, the friction findings here are `wf-learnings.yaml`
> material.
>
> **Accepted, not tracked:** the wf-po Phase 1 cross-checkout / cross-repo state-recovery gap also
> surfaced on this run. It is a **consciously accepted** wf shortcoming — do not re-file it.

---

## TL;DR

Two classes of finding:

**In-session friction (F-1..F-3)** — 14 recorded sessions, **2 surfaced actionable wf friction**
(both in planning roles, SA and SWA). Each names a concrete file or field to change.

**Telemetry-pipeline integrity (F-4..F-5)** — the telemetry record this report is built on is
itself **lossy and incomplete**: build telemetry was written into the worktree and lost, and
review wrote nothing at all. These outrank F-1..F-3 in priority — you cannot distill findings you
never captured, and the session counts above are themselves undercounts because of them.

| # | Role | One-line | Fix surface | Status (2026-07-09) |
|---|------|----------|-------------|---------------------|
| F-1 | wf-sa | `render_design.py` has no entity-relationship diagram mode | `render_design.py` | **Fixed** — entity mode (entities + labelled relations + cardinality), rendered to `paths.domain_view` |
| F-2 | wf-swa | Task `serves:` is single-valued but tasks legitimately serve several capabilities | `sprint.yaml.tmpl`, `task-contract.md` | **Fixed** — `serves` is a list of every distinct driver |
| F-3 | wf-swa | *(derived)* SA folds a shared composition root into every endpoint task, serializing parallel work | SA decomposition heuristic | **Parked** — CANDIDATES.md C20; trigger: a second run reproduces it |
| **F-4** | **wf-build** | **Telemetry written into the worktree, lost when the worktree closed — must target repo root** | **build telemetry-write path** | **Fixed** — `record_session.py` anchors a relative sink to the main checkout root |
| **F-5** | **wf-review** | **Wrote no telemetry at all — the role is invisible to the pipeline** | **`wf-review` telemetry-write step** | **Fixed by the F-4 fix** — the skill's telemetry step existed all along; the append died with the worktree. Row-presence check parked as C21 |

---

## F-1 — render_design.py has no entity-relationship diagram mode

**Role:** wf-sa · **Session:** 2026-07-06 → 07-07

`render_design.py` renders only components / dependencies / allocation. It has **no labelled
entity-relationship edges**, so the domain-model deliverable the human explicitly asked for (a
UML-ish object diagram) had to be **hand-authored as HTML outside the tool**
(`.wf/transient/domain-model.html`).

**Why it matters:** a hand-authored deliverable is un-derivable, un-refreshable, and rots — the
opposite of the "derive, don't store" promise. If the SA needs an entity diagram, the tool should
emit it.

**Fix surface:** add an entity-diagram mode to `render_design.py` (labelled ER edges over the
domain objects), TDD'd like the other tools.

---

## F-2 — task `serves: CAP-NNN` is single-valued, but tasks serve several capabilities

**Role:** wf-swa · **Session:** 2026-07-08, 25 min

`sprint.yaml.tmpl` and `task-contract.md` give each task a **singular** `serves: CAP-NNN`. But the
SWA's own decomposition rules legitimately group requirements with *different* capability drivers
into one task:
- **T7** = REQ-38 (CAP-003) + REQ-42 (CAP-018)
- **T13** = REQ-53 (CAP-010) + REQ-55 (CAP-001)

The single-driver field forces a **"primary driver" fudge** — one capability is picked and the
others' trace is silently dropped, weakening the per-requirement traceability wf2 is supposed to
be strongest on.

**Fix surface:** make `serves:` a list (or move the capability link down to per-requirement level
inside the task), and update `task-contract.md` to match. Small schema change; direct
traceability payoff.

---

## F-3 — (derived) SA decomposition folds a shared composition root into every endpoint task

**Role:** wf-swa · reported on the `repo_observation` channel, but the *cause* is a wf pattern.

`backend/cmd/server/main.go` is a single ~600-line composition root, and the SA folds
`cmd/server` route+adapter wiring into **each** endpoint task's acceptance criteria. Because every
handler task edits the same file, the SWA had to **serialize** four otherwise-parallel tasks
(T13→T14→T15→T16) to avoid worktree-merge conflicts.

**Why it matters:** this is a *dems* code observation on its face, but the lever is on the wf side:
when the SA's decomposition routes many parallel tasks through one shared file, it defeats the
worktree-parallelism the build stage is built for. Worth a heuristic in SA decomposition — factor
out (or stub) shared composition roots so leaf tasks stay independent.

**Status:** softer than F-1..F-2 (it's an inference, not a direct friction report). Log it; don't
act until a second run reproduces the serialization.

---

## F-4 — wf-build writes telemetry into the worktree, where it is lost on close

**Role:** wf-build · **Class:** telemetry-pipeline integrity

Build runs in a git worktree. It wrote its telemetry **into that worktree's tree** instead of the
repo root, so when the worktree was closed the telemetry went with it — **silently lost**. The
build sessions that *did* survive in `sessions.jsonl` are the incomplete remainder; the recorded
build history is not trustworthy as a count or as content.

**Why it matters:** telemetry is the input to `wf-retrospective` and to reports like this one.
Writing it to a disposable location means the run's own observability leaks exactly at the stage
(build) that does the most work. This is a data-loss bug, not a papercut.

**Fix surface:** resolve the telemetry path to the **main repo root**, not the worktree cwd.
Anchor it to an absolute, config-derived path (or `git rev-parse --git-common-dir`) so the append
lands in the real `.wf/telemetry/sessions.jsonl` regardless of which worktree the agent runs in.

---

## F-5 — wf-review writes no telemetry at all

**Role:** wf-review · **Class:** telemetry-pipeline integrity

There is **no `wf-review` session anywhere** in `sessions.jsonl` — the role ran (or should have)
but emitted nothing. It is entirely invisible to the telemetry pipeline: zero friction signal,
zero duration, zero record it happened.

**Why it matters:** review is a judgement-heavy role, exactly the kind whose friction you most want
captured. A silent role can never contribute a learning, so any friction it hit on this run is gone.

**Fix surface:** add the telemetry-write step to `wf-review` (same session-record emit the other
roles use), and — because F-4 shows a single missing/misrouted write goes unnoticed — consider a
mechanical check that every dispatched role left a telemetry row.

---

## Meta-observations

- **The telemetry loop is leaky end to end.** Collection loses data (F-4), one role never reports
  (F-5), and distillation never ran (retrospective). Fix the loop before trusting any run's numbers
  — three of the four learning-pipeline stages misfired on run 1.
- **Retrospective is the missing link.** These findings sat in raw telemetry with no distillation
  step run. The manual summary works once; at steady state `wf-retrospective` should own it.
- **"Friction concentrates in planning roles" is only half safe to conclude.** The judgement-heavy
  handoffs (SA deliverables, SWA decomposition) clearly are where the toolkit is thin. But "build
  produced zero friction" is partly a **data-loss artifact** (F-4) — its record is incomplete — and
  review's silence is total (F-5). Read the read-only roles' clean record as real; treat build's and
  review's as unknown.
- **wf-sa wall-clock (~25 h elapsed) spans an overnight gap** and is not pure work time — don't
  read it as a cost signal without session-level timing.
