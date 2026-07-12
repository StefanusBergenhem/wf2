# wf-infrastructure — WIP handover

**Branch:** `wf-infrastructure` · **Date parked:** 2026-07-12

We are building/refining the **wf-infrastructure** skill by walking it
**paragraph by paragraph** with the user, fixing each as we go. This note lets a
fresh session resume mid-walk.

## Process rules the user set (follow these exactly)

1. **Report per paragraph; the user decides edits.** Read a paragraph back, say
   what it's *supposed* to do, give an honest critique. Then stop.
2. **Never edit without an explicit go-ahead.** "Give me a report" ≠ "make the
   change." Wait for the instruction, and edit only what was named.
3. **Walk in flow order** (SKILL.md top-to-bottom, then the reference).
4. **The user says when to proceed** to the next paragraph.

Tone: direct, brutally honest, no fluff. Apply the CLAUDE.md **behavior test** to
every line ("would the agent act differently if this line were absent?").

## The role's settled design (decisions locked — do not relitigate)

- **What it is:** a **main-context, human-driven steward** (same execution context
  as wf-po / wf-sa default mode — see the CLAUDE.md "Two execution contexts" bullet,
  which is on this branch). It **audits** the repo's testing & quality-gate
  infrastructure against best practice → **suggests** improvements → **implements**
  the ones inside its Boundary.
- **Boundary:** edits **infra-config** in-session with human approval (gate commands,
  coverage *tool* config, CI steps, lint/format/type/arch-tool config, AGENTS.md
  gotchas). **Never** touches product source or product tests — those route to the
  build pipeline (test-writing is already wired into orchestration).
- **AUDIT, never MAINTAIN.** The role inspects the repo's **native** gates/tools; wf
  does not own or store a quality policy. This is why we **killed** the
  `.wf/coverage.yaml` policy + the `wf audit coverage` CLI tool (over-engineering;
  violated audit-not-maintain and the dogfood law — the dems friction was a native
  *allow-list* misconfig, curable by switching the native coverage tool to
  *default-floor* style, not by a wf policy file). `audit.py` + `audit_test.sh` were
  deleted; `main.py` + `config.yaml.tmpl` reverted to HEAD (net-zero on tracked files).
- **The wf-enforced spine it must verify is set up:** `commands.preflight` +
  `commands.stage_check` — plus **CI/CD** and the repo's own tool configs. Those two
  wf gates are the only wf-owned "tools" it cares about.
- **Rhythm: one concern at a time** (not batched).
- **Structure: phases-as-chapters** (`## Process` → `### Phase N — <title>` + a
  `## Final — record telemetry (REQUIRED)` section), matching wf-po/wf-sa.

## Where the walk is

`SKILL.md` walked and edited through the Boundary section; converted from a numbered
list to phases-as-chapters. **Currently mid-review of `### Phase 1 — Take stock`.**

**Proposed Phase 1 rewrite — NOT yet applied, awaiting user approval:**
- drop "which components they cover" (dead coverage-policy mindset)
- enumerate the concrete audit surface to locate: the wf gates
  (`$PREFLIGHT`/`$STAGE_CHECK`), the **CI/CD config**, and the repo's **tool configs**
  (coverage tool, linters, formatter, type-checker, arch-fitness tool)
- name `$BRIEF`'s purpose: the map of what the repo *is* → what the gates *should* cover
- gate-word the `references/testing-infra.md` read as a Phase-3 precondition

## Standing flags (open work in the walk)

1. **Phase 4 still says "together" (batched)** — contradicts the one-concern rhythm.
   Likely Phase 4+5 (Propose / Apply) collapse into a per-concern propose→apply loop.
2. **`references/testing-infra.md` still carries kill-related dead refs** — the intro
   line names `wf audit coverage`; **all of §1a** is built on the dead tool and must be
   **reframed** to "audit the native coverage tool's config for default-floor style so
   no component is silently unguarded"; the **§7** line lists `wf audit coverage` as a
   required check. §1b (patch coverage / diff-cover) and the caveat are native and
   survive as-is.

## Files

- `skills/wf-infrastructure/SKILL.md`
- `skills/wf-infrastructure/references/testing-infra.md` (the 7-dimension best-practice
  yardstick; still needs the §1a/intro/§7 reframe above)

## Repo-state note

This branch carries `CLAUDE.md` (two-exec-context bullet) + `skills/wf-infrastructure/`
+ this handover. **Master intentionally keeps** the `C29` (wf-discover groupings) and
`C30` (`reconcile.harvest` bug) candidates in `doc/CANDIDATES.md`, plus
`doc/notes/wf-sa-script-findings-20260712.md` — all unrelated to wf-infrastructure.
