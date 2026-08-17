---
name: wf-retrospective
description: Distils a run into actionable learnings — session telemetry feedback plus, when present, the cross-task patterns in the run's pipeline state. Run at every sprint close or after a stretch of work to compile feedback into the learnings streams.
---

# wf-retrospective

**Read `{{WF_SKILLS_DIR}}/wf-basics/SKILL.md` now** for the `.wf/` layout and the telemetry
handshake. Record the session start stamp now per its §2. Every path below is a line in
your dispatch envelope — read it there, do not open `.wf/config.yaml`:

- `TELEMETRY`      = `paths.telemetry`       (append-only session log — read)
- `REPO_STATE`     = `paths.repo_state`      (the id high-water marks — read + write)
- `PIPELINE_STATE` = `paths.pipeline_state`  (the finished run's state — read if present)
- `OBSERVATIONS`   = `paths.observations`    (the admission buffer — read + write)
- `LEARNINGS`      = `paths.learnings`       (project-code learnings — read + append)
- `WF_LEARNINGS`   = `paths.wf_learnings`    (wf-toolkit learnings — read + append)
- `RETRO_REPORT`   = `paths.retro_report`    (the human-facing digest of this run — write)

You distil a finished run into learnings, from two sources:

- **session telemetry** (`$TELEMETRY`) — two kinds of row, told apart by each row's `kind`:
  - **session records**, one per agent session — their feedback fields, routed by field:
    - `feedback.repo_observation` → `$LEARNINGS` — a learning about **the project's code**.
    - `feedback.wf_friction` (clustered by `feedback.friction_kind`) → `$WF_LEARNINGS` — a
      learning about **the wf toolkit itself**.
    - `feedback.gotcha` and `feedback.had_to_find` → an **AGENTS.md edit you make**
      (Phase 5) — never a learnings entry.
  - **driver events** — the dispatch, routing, and stop rows the loop appends as it runs.
    They carry no feedback; they are the exact join that says which session belonged to
    which task, where each design issue routed, and why the loop stopped. Use them in
    Phase 3, never as a learning of their own.
- **the run's execution** (`$PIPELINE_STATE`, when the run produced one) — the
  **cross-task patterns** no single session can see. Absent it, the driver events carry
  the run's shape.

## The entry

Both learnings files hold the same shape:

```yaml
- id: L-001
  statement: "<one actionable sentence naming a concrete artifact, field, or step>"
  sources: ["<session ended_at, or sprint:<sprint_id>>"]   # what this was distilled from
```

Entries live under the file's `learnings:` key.

`$OBSERVATIONS` holds the same shape **without an id**, under an `observations:` key:

```yaml
- statement: "<the same one actionable sentence>"
  sources: ["<session ended_at, or sprint:<sprint_id>>"]
```

It carries no id because it is not a learning yet — minting one for something that may
never be seen twice moves `id_counters.learning` for nothing.

You only ever **create, reinforce and promote** entries — never remove one.

## Admission — the rule that decides which file an observation lands in

**A first sighting goes to `$OBSERVATIONS`. A second sighting promotes it to a
learnings file.** One run's lone friction is noise; a learning is what every later design
dispatch reads whole, so the bar to enter that context is being seen twice.

This applies to both learnings streams — `$LEARNINGS` and `$WF_LEARNINGS` alike. The
routing rule in Phase 2 still decides *which* stream a promotion lands in; admission
decides *whether* it lands in one at all.

Two things are exempt, because neither is a repeated-sighting judgment:

- an observation you are **reinforcing on an entry already in a learnings file** — it is
  past the gate; append the source there as before, and touch the buffer not at all;
- a **cross-task pattern** from Phase 3 — it is already evidence from several tasks in one
  run, which is what the second sighting is a proxy for. It goes straight to a learnings
  file.

## Process

### Phase 1 — Load

1. Read `$TELEMETRY` — one JSON row per line.
2. Read `$PIPELINE_STATE` if it exists — the finished run's `task_states`, `design_issues`,
   and per-stage summaries. If absent, work the run patterns from the driver events
   alone.
3. Read `$LEARNINGS`, `$WF_LEARNINGS` and `$OBSERVATIONS` (each may not exist yet). For
   each, collect the union of every entry's `sources`: that union is what has already
   been compiled.

### Phase 2 — Select what's new

- **Telemetry:** walk the session records. Skip the record if its `ended_at` already
  appears in **either** file's `sources` set, and skip a feedback field that is empty.
- **Route each field by its SUBJECT, not by the field it arrived in.** The field names
  which one an agent reached for; what the entry is *about* decides which file it lands
  in. An observation about the wf toolkit — a role's skill text, a `wf` CLI verb, the
  driver, **this skill and this run's own process included** — is a `$WF_LEARNINGS`
  entry however it arrived; friction that is really about the project's own code is a
  `$LEARNINGS` entry. An observation about the wf toolkit that names no wf file, step,
  or verb to change is dropped as non-actionable, not filed to the nearer stream.
- **Run patterns:** if `$PIPELINE_STATE` or any driver event is present and
  `sprint:<sprint_id>` is not yet in either channel's `sources`, this run's patterns are
  unprocessed.

What remains is this run's unprocessed input.

### Phase 3 — Find the cross-task patterns

A single session reports its own friction; only the whole run reveals a pattern **across
tasks**. Read these signals — from `$PIPELINE_STATE` and the driver events, keyed to each
other by task id — and keep only what repeats or clusters; one task's lone hiccup is noise,
not a learning. With neither source present, skip this phase:

- **Recurring rejection** — several tasks that took more than one `wf-build` dispatch for
  the **same reason**. Count the driver `dispatch` events per task and role; a task's
  `attempt_counter` moves only on a review rejection, so a task re-dispatched after a
  resume, a refused launch or a design-issue repair reads as first-try and the pattern
  stays invisible. The shared cause is the learning (a process/toolkit gap →
  `$WF_LEARNINGS`, or a code smell the tasks share → `$LEARNINGS`), never the count.
- **Design-issue cluster** — multiple `design_issues` of the same `fix_kind` against related
  contracts: a systematic gap in how the work was specified → `$WF_LEARNINGS`.
- **Escalation / block cause** — an `escalated` or `blocked` task, or the stop event that
  ended the run: what defeated it, stated as something to change next time.
- **Repair churn** — stages whose close needed repair, or design issues answered after
  dispatch: what the design missed at cut time → `$WF_LEARNINGS`.

Velocity and per-task counts are run telemetry, not learnings — they belong in the Phase 6
summary, not the streams.

### Phase 4 — Distil

**Cluster friction mechanically before judging any of it:** group the unprocessed records'
`wf_friction` entries by `feedback.friction_kind` — a record without the field, or with
`none` beside non-empty prose, goes in the `none` group. Distil each group as a unit:
several sessions reporting the same kind usually share one cause. The kind is the grouping
key only — the learning's statement comes from the prose.

**Then split each group by root cause, and write one learning per cause — never one per
group.** A kind is a coarse bucket: `skill_gap` holds every skill defect there is, so one
group routinely carries two unrelated causes, and collapsing them loses one. Read the
prose of every record in the group and ask whether one fix would resolve all of them; if
not, the group is more than one learning.

**Then verify every observation against the merged tree before distilling it.** A session
writes its feedback before the run is over, so a later task in the same run — or a later
stage — routinely closes what an earlier session flagged, and the telemetry text can
never show it. For each, read what it names in the code as it stands now (`git log`
the run's merge commits when the observation points at a defect rather than a file);
if it is already closed, drop it — no entry, counted with the non-actionable drops.

Turn each surviving observation and each cross-task pattern into a learning, holding the bar:

- **Actionable and concrete.** Names a real artifact, field, or step and implies an action.
  Drop the vague — "could be cleaner" is noise, not a learning, and produces no entry.
- **Dedup against the learnings files AND `$OBSERVATIONS`, in that order.** Match on what
  the statement is *about* — one fix resolving both is the test, not shared wording. Then:
  - it restates an entry **already in a learnings file** → reinforce there: append the
    source (the session `ended_at`, or `sprint:<sprint_id>` for a run pattern) to that
    entry's `sources`, add no duplicate, and write nothing to the buffer;
  - it restates an entry **in `$OBSERVATIONS`** → **promote it**: mint its id, write it to
    the learnings file its subject routes to (Phase 2) carrying **both** sources — the
    buffered one and this run's — and delete it from the buffer. This is the second
    sighting the gate waits for;
  - it restates **nothing** → append it to `$OBSERVATIONS` with this run's source, and
    mint no id. Phase 3 cross-task patterns skip this arm and go straight to a learnings
    file.
- Mint each promoted entry's `L-NNN` id from `max(<lane counter in $REPO_STATE>, highest
  id in the file) + 1` — `id_counters.learning` for `$LEARNINGS`, `id_counters.wf_learning`
  for `$WF_LEARNINGS`; never renumber, never reuse a retired number.

### Phase 5 — Gotchas and searches → the AGENTS.md files

You **maintain** the repo's `AGENTS.md` files: you edit them, and what you leave is what
the next run's agents read. Walk every telemetry record with a non-empty
`feedback.gotcha` or `feedback.had_to_find` (neither has a `sources` ledger — dedup
happens against the target file). For each:

1. **Pick the target file** — the nearest `AGENTS.md` at or above the directory it
   concerns; the repo-root `AGENTS.md` when it is repo-wide or no nearer one exists.
   Create the file if it does not exist.
2. **Pick the section.** A `gotcha` is a trap — what goes wrong and the exact fix. A
   `had_to_find` is a **location** — it goes under a `## Test harness` heading when it
   names test scaffolding (fixture builders, stub/fake types, the integration bootstrap,
   a proof pattern to extend), and under the file's own conventions heading otherwise.
3. **Dedup against the target.** Read it; if it already covers the same trap or names the
   same location in any wording, add nothing.
4. **Write the lines.** One self-contained line each, in the file's existing voice: a
   command, a trap, a convention, a location. Every line must change what an agent does —
   never architecture narrative, never spec prose, and never a requirement id (a persisted
   id rots invisibly and is the thing these files must not carry).
5. **Hold the cap.** `hygiene.agents_md_max` bounds every one of these files, and it is
   the reason they stay worth loading. Over it, cut before you add: drop the line that has
   gone stale against the code, or fold two into one. Leaving it over cap is not an option
   — the gate reports it and the next run pays for it in every agent's context.

`wf hygiene check` reports a test directory whose `AGENTS.md` carries no `## Test harness`
section; if this run gave you nothing to put there, leave it — the finding is a prompt for
the next run, not a reason to invent content.

### Phase 6 — Write, summarize & commit

1. Append the promoted and reinforced entries to `$LEARNINGS` and `$WF_LEARNINGS`, creating either
   from its template (`<role_dir>/assets/learnings.yaml.tmpl`, `<role_dir>/assets/wf-learnings.yaml.tmpl`) if absent.
   If you minted any id, bump its lane's counter in `$REPO_STATE`
   (`id_counters.learning` / `id_counters.wf_learning`) to the highest id minted.
   **Appending is the only write you make to either file.** Never remove an entry and
   never annotate one as drained, archived, or closed — draining happens at sprint close
   off the slice's `serves:` header and the merge record, and it snapshots to the archive
   as it goes. A drain note you write is believed by the next reader, who then skips a
   drain that never happened.
2. **Write `$OBSERVATIONS`** — the first sightings you appended, minus every entry you
   promoted in Phase 4 — creating it from `<role_dir>/assets/observations.yaml.tmpl` if
   absent. Then bound it:
   ```sh
   python3 <paths.tools>/cli/wf observations age
   ```
   Skip this and the buffer grows every run and drains only by promotion, which makes it
   the accumulator the admission gate exists to prevent. It archives what it drops.
3. **Write the run's digest to `$RETRO_REPORT`**, overwriting it — it holds one run. This
   file is the deliverable that reaches the maintainer; a return value alone reaches no
   one. Include: the new and reinforced entries per stream; the count dropped as
   non-actionable; every `AGENTS.md` file Phase 5 edited, with the lines added and any
   line cut to hold the cap; the **per-role context report** from
   `python3 <paths.tools>/cli/wf telemetry roles` (most-concerning role first — read
   `context_max` as what a role actually held at once; a `footprint` far above it is
   cache churn from slow turns, not over-loading, so attribute cost accordingly); the
   **repo-hygiene debt summary** from `python3 <paths.tools>/cli/wf hygiene check
   --format json` — finding counts by rule plus the worst files, the planning input for
   split/cleanup tasks; and,
   when `$PIPELINE_STATE` was
   present, a one-glance execution summary — tasks completed/escalated/blocked, design issues
   by `fix_kind`, per-stage durations, and the rebuild count as tasks that took more than
   one `wf-build` dispatch event. Do not commit `$RETRO_REPORT` — it is transient. In your
   return, name `$RETRO_REPORT` and the headline counts; the file carries the detail.
4. **Archive and drain `$TELEMETRY`** — after step 3 has read the log for the roles report.
   Snapshot the cycle's telemetry into the maintainer archive and empty the live log, so it
   holds only the next cycle and this role's own read stays bounded as history grows. Run
   only when `$TELEMETRY` exists:
   ```sh
   python3 <paths.tools>/cli/wf archive add $TELEMETRY --label <sprint-id> --move
   ```
5. Commit the durable outputs — the learnings, the observations buffer, the AGENTS.md
   edits, and the archived telemetry snapshot —
   leaving them uncommitted is one `git clean` from gone. Stage explicit paths, never
   `git add .` (include `$REPO_STATE` only when you bumped a counter). **Do not stage
   `$TELEMETRY` or its directory**: the live log is gitignored, and `git add` refuses an
   explicitly-named ignored pathspec instead of skipping it — it exits non-zero having
   staged *nothing*, so the archive snapshot in the same command is lost too. The drain
   needs no staging; the snapshot is the record:
   ```sh
   git add $LEARNINGS $WF_LEARNINGS $OBSERVATIONS $REPO_STATE
   git add <every AGENTS.md Phase 5 edited>
   git add -A -- <paths.archive>          # the snapshot step 3 just wrote
   git commit -m "learnings + AGENTS.md + telemetry drain: <sprint-id or session range>"
   ```
   If the commit fails (hook, identity), report the exact error and halt — never `--no-verify`.

### Phase 7 — Telemetry (REQUIRED)

Your last action, always. Run the `wf-basics` §2 `record_session.py` command with
`--agent wf-retrospective`, this run's `--outcome`, and the feedback answers (omit a
flag when there is nothing concrete). If it errors, continue.

## Hard constraints

- **A run that distils nothing is a valid run.** When every observation is already compiled
  or judged noise, report "nothing new" and append no learning — never invent one to look
  productive. Phase 6's report, telemetry drain, and its commit still run: the rows were
  read, so they are archived and the live log is drained regardless.

## Halt conditions

- `$TELEMETRY` is absent or empty **and** `$PIPELINE_STATE` is absent — nothing to distil.
  Report and exit.
