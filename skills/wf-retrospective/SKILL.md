---
name: wf-retrospective
description: Distils a run into actionable learnings — session telemetry feedback plus, when present, the cross-task patterns in the run's pipeline state. Run at sprint closeout or after a stretch of work to compile feedback into the learnings streams.
---

# wf-retrospective

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Record the session start stamp now per `wf-basics` §2. Resolve every path below from `.wf/config.yaml`:

- `TELEMETRY`      = `paths.telemetry`       (append-only session log — read)
- `PIPELINE_STATE` = `paths.pipeline_state`  (the finished run's state — read if present)
- `LEARNINGS`      = `paths.learnings`       (project-code learnings — read + append)
- `WF_LEARNINGS`   = `paths.wf_learnings`    (wf-toolkit learnings — read + append)

You distil a finished run into learnings, from two sources:

- **session telemetry** (`$TELEMETRY`) — each record's feedback fields, routed by field:
  - `feedback.repo_observation` → `$LEARNINGS` — a learning about **the project's code**.
  - `feedback.wf_friction` (clustered by `feedback.friction_kind`) → `$WF_LEARNINGS` — a
    learning about **the wf toolkit itself**.
  - `feedback.gotcha` → a **proposed AGENTS.md edit** in your report (Phase 5) — never a
    learnings entry, never a file you write.
- **the run's execution** (`$PIPELINE_STATE`, when an orchestration run produced one) — the
  **cross-task patterns** no single session can see. Absent it, distil telemetry alone.

## The entry

Both files hold the same shape:

```yaml
- id: L-001
  statement: "<one actionable sentence naming a concrete artifact, field, or step>"
  sources: ["<session ended_at, or sprint:<sprint_id>>"]   # what this was distilled from
```

Entries live under the file's `learnings:` key.

You only ever **create and reinforce** entries — never remove one.

## Process

### Phase 1 — Load

1. Read `$TELEMETRY` — one JSON record per line.
2. Read `$PIPELINE_STATE` if it exists — the finished run's `task_states`, `design_issues`,
   and `stage_summaries`. If absent, skip the run-pattern steps; you are distilling telemetry
   alone.
3. Read `$LEARNINGS` and `$WF_LEARNINGS` (each may not exist yet). For each, collect the
   union of every entry's `sources`: that union is what has already been compiled.

### Phase 2 — Select what's new

- **Telemetry:** walk the session records. For each channel (`repo_observation` →
  `$LEARNINGS`, `wf_friction` → `$WF_LEARNINGS`): skip the record if its `ended_at` is
  already in that channel's `sources` set, or if that feedback field is empty.
- **Run patterns:** if `$PIPELINE_STATE` is present and `sprint:<sprint_id>` is not yet in
  either channel's `sources`, this run's patterns are unprocessed.

What remains is this run's unprocessed input.

### Phase 3 — Find the cross-task patterns (only with `$PIPELINE_STATE`)

A single session reports its own friction; only the whole run reveals a pattern **across
tasks**. Read these signals and keep only what repeats or clusters — one task's lone hiccup
is noise, not a learning:

- **Recurring rejection** — several tasks whose `attempt_counter` climbed for the **same
  reason**. The shared cause is the learning (a process/toolkit gap → `$WF_LEARNINGS`, or a
  code smell the tasks share → `$LEARNINGS`), never the count.
- **Design-issue cluster** — multiple `design_issues` of the same `fix_kind` against related
  contracts: a systematic gap in how the work was specified → `$WF_LEARNINGS`.
- **Escalation / block cause** — an `escalated` or `blocked` task: what defeated it, stated
  as something to change next time.

Velocity and per-task counts are run telemetry, not learnings — they belong in the Phase 6
summary, not the streams.

### Phase 4 — Distil

**Cluster friction mechanically before judging any of it:** group the unprocessed records'
`wf_friction` entries by `feedback.friction_kind` — a record without the field, or with
`none` beside non-empty prose, goes in the `none` group. Distil each group as a unit:
several sessions reporting the same kind usually share one cause, so write one learning for
the shared cause, not one per record. The kind is the grouping key only — the learning's
statement comes from the prose.

Turn each unprocessed observation and each cross-task pattern into a learning, holding the bar:

- **Actionable and concrete.** Names a real artifact, field, or step and implies an action.
  Drop the vague — "could be cleaner" is noise, not a learning, and produces no entry.
- **Dedup against the entries present in the file.** If an observation or pattern restates an
  existing learning, reinforce it: append its source (the session `ended_at`, or
  `sprint:<sprint_id>` for a run pattern) to that entry's `sources` and add no duplicate.
- Mint each new `L-NNN` id from `max(<lane counter in .wf/config.yaml>, highest id in
  the file) + 1` — `id_counters.learning` for `$LEARNINGS`, `id_counters.wf_learning`
  for `$WF_LEARNINGS`; never renumber, never reuse a retired number.

### Phase 5 — Gotchas → proposed AGENTS.md edits

Walk **every** telemetry record with a non-empty `feedback.gotcha` (gotchas have no
`sources` ledger — dedup happens against the target file, below). For each:

1. **Pick the target file** — the nearest `AGENTS.md` at or above the directory the gotcha
   concerns; the repo-root `AGENTS.md` when the gotcha is repo-wide or no nearer one exists.
   If the target does not exist yet, the proposal names it as a new file.
2. **Dedup against the target.** Read the target `AGENTS.md`; if it already covers the same
   trap (any wording), drop the gotcha — propose nothing.
3. **Draft the exact edit**: the target path, the section it goes under, and the verbatim
   lines to add — ready to paste, not a paraphrase of the problem.

Collect the drafts for the Phase 6 report. **Do not Edit or Write any AGENTS.md** — it is
human-owned intent; an auto-applied edit ships unreviewed. The proposal in your report is
the whole deliverable, and the human applies or rejects it.

### Phase 6 — Write, summarize & commit

1. Append the new and reinforced entries to `$LEARNINGS` and `$WF_LEARNINGS`, creating either
   from its template (`assets/learnings.yaml.tmpl`, `assets/wf-learnings.yaml.tmpl`) if absent.
   If you minted any id, bump its lane's counter in `.wf/config.yaml`
   (`id_counters.learning` / `id_counters.wf_learning`) to the highest id minted.
2. **Report the run in your return only — store nothing beyond the streams:** new entries per
   stream, reinforcements, the count dropped as non-actionable, every Phase 5 proposal as a
   `PROPOSED AGENTS.md edit` block (target path + verbatim lines, awaiting human approval),
   and (when `$PIPELINE_STATE` was present) a one-glance execution summary — tasks
   completed/escalated/blocked, design issues by `fix_kind`, stage durations. This summary is
   transient; do not write it to a file.
3. Commit the learnings files — they are durable, and leaving them uncommitted is one
   `git clean` from gone. Stage explicit paths, never `git add .` (include
   `.wf/config.yaml` only when you bumped a counter):
   ```sh
   git add $LEARNINGS $WF_LEARNINGS .wf/config.yaml
   git commit -m "learnings: distil <sprint-id or session range>"
   ```
   If the commit fails (hook, identity), report the exact error and halt — never `--no-verify`.

### Phase 7 — Telemetry (REQUIRED)

Your last action, always. Run the `wf-basics` §2 `record_session.py` command with
`--agent wf-retrospective`, this run's `--outcome`, and the feedback answers (omit a
flag when there is nothing concrete). If it errors, continue.

## Hard constraints

- **A run that distils nothing is a valid run.** When every observation is already compiled
  or judged noise, report "nothing new" and commit nothing — never invent a learning to look
  productive.

## Halt conditions

- `$TELEMETRY` is absent or empty **and** `$PIPELINE_STATE` is absent — nothing to distil.
  Report and exit.
