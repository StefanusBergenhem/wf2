# wf2 — Candidates

Deferred observations about the wf2 toolkit itself: improvements, risks, and
thresholds worth acting on later. Not scheduled work — a parking lot so a
forward-looking note isn't lost. Promote an entry to real work when its trigger
fires; delete it when resolved.

---

## C1 — A shared config reader (the `wf config get` threshold)

**Date:** 2026-06-14
**Context:** `skills/wf-init/scripts/scaffold.sh` now derives the transient dir,
telemetry sink, and gitignore line from `.wf/config.yaml` instead of hard-coding
them (the "config is the single source of truth" ground rule). It reads config
with a small awk helper, `cfg_path`.

**Observation:**
1. `cfg_path` is **not a real YAML parser.** It works only because the `paths:`
   block is flat, one level deep, with double-quoted values. If the config schema
   ever grows nested or multi-line path values, it breaks — silently. This
   constrains how the config schema may evolve while the awk reader is the only
   reader.
2. It is the **second** place config is read mechanically-ish (skills read it as
   LLM prose; scaffold now parses it). One hand-rolled reader is fine. The moment a
   **second script** needs to read config, two hand-rolled parsers will drift.

**Trigger to act:** a second script needs to read `.wf/config.yaml`. At that
point, fold config reads into one shared helper (e.g. `wf config get <key>`) — the
wf1 lesson that a read with ≥2 callers belongs in one tool. Until then, the single
awk reader is the right amount of machinery.

---

## C2 — `brief.md` vs the text map overlap (discover)

**Date:** 2026-06-14
**Context:** discover can emit two transient text views of the system — the
agent-facing `brief.md` and a plain text system map (from `spine.py`). They
overlap on the component list + LOC + a description field, but each carries
something the other lacks: the text map uniquely has **per-component dependency
adjacency**; `brief.md` uniquely has **subsystems + scout descriptions +
cross-cutting couplings**.

**Observation:** having two near-duplicate agent text views invites drift and
forces a downstream planner to choose. Likely the right move is to fold a terse
dependency-adjacency view into `brief.md` so it is the *single* agent file, and
demote the text map to a debug / no-scout fallback.

**Trigger to act:** the first real dogfood where a planning agent (PO→SA→SWA)
consumes `brief.md`. If it reaches for adjacency the brief doesn't carry, fold it
in; if it never misses it, leave the split. Do not pre-decide before that run —
this is exactly what dogfooding the brief→planner contract is meant to settle.

---

## C3 — Telemetry: capture tokens + tool counts via per-harness hooks

**Date:** 2026-06-14
**Context:** Telemetry records time + outcome + structured feedback (skill-written,
harness-agnostic). It does **not** capture token cost or tool-call counts, which
was the originally-intended "main purpose." Three harnesses were researched
(Claude Code, pi, opencode) to find how.

**Finding — capture is irreducibly harness-specific.** Every harness keeps
token/tool data in a per-session transcript/store, **none exposes that store to an
in-session bash command** (so the skill-invoked recorder structurally cannot read
it), and there is no cumulative total anywhere (always aggregate). The native
capture point differs per harness:

- **Claude Code** — `Stop` / `SubagentStop` hook receives `transcript_path` on
  stdin; parse the JSONL, sum `usage.{input,output,cache_read}` tokens, count
  `tool_use` blocks. Subagents get their own `agent_transcript_path`.
- **pi** (earendil-works) — `session_shutdown` extension aggregates in-process
  (`ctx.sessionManager.getEntries()`), **or** post-run parse of the session JSONL
  under `~/.pi/agent/sessions/` (`usage.totalTokens`, `toolCall` blocks) with the
  session pinned via `--session` / `PI_CODING_AGENT_SESSION_DIR`. No in-session env
  var. (Confirm pi identity: earendil Pi vs oh-my-pi — they differ.)
- **opencode** — read the on-disk store (`~/.local/share/opencode`; JSON tree
  pre-1.2, SQLite `opencode.db` 1.2+) keyed by a session id pinned via
  `opencode run --session <id>`; sum `tokens.{input,output,reasoning,cache.*}`,
  count `type:"tool"` parts. `OPENCODE_SESSION_ID` in tool env is unmerged — do not
  rely on it.

Even wf1 never solved this from the skill: its telemetry left token columns
`(hook)` / null, "fill via an optional host Stop hook."

**Recommended shape when built — two layers:** (1) the skill writes the
agnostic record it already does (agent/time/outcome/feedback); (2) a small
**per-harness adapter** (Claude Stop hook · pi `session_shutdown` · opencode
store-reader), installed per target, enriches with tokens+tools. This is the one
genuinely harness-coupled piece of wf2 — keep it isolated in the adapters.

**Trigger to act:** when token cost actually needs measuring (e.g. a dogfood run
where context budget or per-agent cost is the question being asked). Until then its
absence does not hurt — defer. Build the Claude adapter first (the dogfood harness).

---

## C4 — wf-swa fix-mode (orchestrator-dispatched contract amendment)

**Date:** 2026-06-14
**Context:** `wf-swa` ships **default-mode only** (design-slice → sprint.yaml). wf1's
SWA had a second **fix mode**: the orchestrator dispatched it to surgically amend a
single task contract when a build/review raised a `contract_amendment` design issue
mid-execution.

**Observation:** fix-mode needs two things wf2 does not have yet — the orchestration
layer that dispatches it, and a design-issue artifact + routing for it to consume.
Building it now would be speculative machinery wired to an absent caller (dogfood
law). The lifecycle reason SA|SWA stay split (SWA is the re-dispatched, surgical
contract-fixer) still holds; only the mechanism is unbuilt.

**Trigger to act:** when the orchestration layer and its design-issue routing are
built. Then add a fix-mode flow to `wf-swa` — single DI, minimum-amendment scope,
flip the DI to resolved — mirroring wf1's `mode-fix`, generalized.

---

## C7 — Vendored vis-network is duplicated across two tools

**Date:** 2026-06-14
**Context:** the SA design view (`tools/design_view/render_design.py`) inlines a
vendored `vis-network.min.js` for an offline self-contained page — the same lib
`tools/discover/render.py` already vendors. The 673 KB blob is now **committed
twice** (`tools/discover/vendor/` and `tools/design_view/vendor/`).

**Observation:** duplicating was the scoped choice — folding it into a shared
`tools/vendor/` would mean editing discover's `render.py` path and re-validating its
output, dragging discover into a planning-layer change mid-review. The duplication is
tracked tech-debt, not an accident.

**Trigger to act:** next time either renderer is touched, or on a cleanup pass. Move
the lib to a shared `tools/vendor/vis-network.min.js`, point both renderers at
`../vendor/…`, and confirm both still emit offline pages. (`install.sh` copies
`tools/*/` so a new `tools/vendor/` ships automatically.)

---

## C6 — How SA knows which capabilities are in scope this round

**Date:** 2026-06-14
**Context:** `wf-sa` Phase 1 step 1 says *"Read `$CAPABILITIES` and identify the
capabilities this change serves."* That is vague — it does not say how SA learns
which capabilities are new / in-scope for this round vs already handled. Two storage
options were floated: (1) a per-capability status (`new | designed | implemented`),
(2) an `ongoing` + `completed` capabilities file pair.

**Analysis (governor lens):** both stored options are the wrong shape.
- **`implemented`/`done` is derivable** — coverage = `[REQ]` test tags ⟷ capabilities
  set-diff. Storing it stores what code reports (governor violation).
- **`designed` is rot-prone** — the design-slice is ephemeral and *free to
  regenerate*, so a durable "designed" flag has no backing artifact. Not tracking it
  costs nothing: if a capability was designed but not built, SA just re-designs it.
- **`ongoing`/`completed` file pair** is the wf1 sync-tax — a maintained second copy
  of lifecycle with entries shuttled between files by hand. Hard no.
- The only **durable, non-derivable** status is intent: `planned` vs `deferred`
  (already in PO's scaffold).

**Likely resolution when picked up:**
1. "Which capabilities this round" is a **session input** — SA is invoked with a
   change-to-design (a capability id / feature / free-text ask) and resolves it to
   the capability set it serves. SA does not autonomously scan for "what's new."
2. **The backlog is the derived gap** — `planned` capabilities with no proving test
   yet. Computed on demand from the `[REQ]` coverage harvest; no backlog file, no
   per-capability build status. This is consistent with "no backlog tier; the slice
   is the unit of work."
3. **Prune PO's status values to `planned | deferred`** (drop `in_progress` =
   transient, `done` = derivable) — `capabilities.yaml.tmpl` + the two PO status
   references.

**Trigger to act:** when build/review land the `[REQ]` coverage harvester (so the
derived gap is actually computable), or when a multi-driver / orchestrated model
needs to avoid re-picking an in-flight capability. Until then the interactive
human-names-the-scope flow is sufficient and the current vague wording is harmless.

---

## C8 — Agent frontmatter is Claude-format; pi/opencode untested

**Date:** 2026-06-15
**Context:** `install.sh` now renders the `agents/` category per harness (the first
agent is `wf-drill`). The render path (copy + token-subst) is harness-agnostic, but
the agent **frontmatter** (`name`/`description`/`tools: Read, Grep, Glob, Bash`) is
written in **Claude's** schema. Rendering to `.pi/agents/` or `.opencode/agents/`
copies that Claude frontmatter verbatim — pi and opencode may expect a different
agent-definition shape (tool-grant syntax especially).

**Observation:** only the Claude target is dogfooded, so this is deferred like the
telemetry adapters (C3) — the one genuinely harness-coupled part of an agent is its
frontmatter. The body is harness-agnostic prose and renders fine everywhere.

**Trigger to act:** when pi or opencode becomes a real target. Then verify each
harness's agent-definition schema and, where it differs, guard the frontmatter with
`wf:if <target>` blocks in `agents/wf-drill.md` (the renderer already supports them).

---

## C9 — Retrospective ships the thin telemetry-distil slice; sprint analysis deferred

**Date:** 2026-06-20
**Context:** `wf-retrospective` ships as the **dogfoodable slice**: read the telemetry
session log, distil `repo_observation` → `paths.learnings` (project learnings wf-sa
reads as drivers) and `wf_friction` → `paths.wf_learnings` (toolkit friction), dedup by
the `sources` provenance set, create-only `open` entries. It runs against the telemetry
PO/SA/SWA/drill already write — no orchestration needed.

**Deferred (no producer yet):** wf1's retrospective also did sprint-execution analysis —
`pipeline_state` attempt-counts / rejection-pattern / velocity / design-issue triage —
and `continuous-learning` maintained a `MEMORY.yaml` lessons store (dedup, capacity-cap,
confidence, reinforcement). None of that has a producer in wf2 (no orchestrator, no
review role), and the maintained `MEMORY.yaml` was wf1's governor-ish overreach. Building
it now would be machinery wired to absent callers.

**Also deferred — `handled` is an optimistic close.** wf-sa flips a learning to
`handled` when it *designs* the fix, not when build *lands* it; nothing downstream
confirms the code shipped. Recoverable — an abandoned fix's smell re-surfaces in a later
observation and re-distils. When the `[REQ]`-style coverage harvester exists, `handled`
can become *derived from commit citations* instead of a stored flag — the same move
deferred for capability "done" in C6.

**Trigger to act:** when the orchestration + review layer lands (so `pipeline_state`,
rejections, and design-issues exist to analyse), grow `wf-retrospective` to consume them;
and when the coverage harvester lands, switch `handled` to derived.
