---
name: wf-retrospective
description: Distils the session telemetry log into actionable learnings — project-code improvements the architect designs against, and wf-toolkit friction for the maintainer. Run after a stretch of work to compile session feedback into the learnings streams.
---

# wf-retrospective

**Read `wf-basics` first** for the `.wf/` layout and the telemetry handshake.
Capture `TS_START` now. Resolve every path below from `.wf/config.yaml`:

- `TELEMETRY`    = `paths.telemetry`     (append-only session log — read)
- `LEARNINGS`    = `paths.learnings`     (project-code learnings — read + append)
- `WF_LEARNINGS` = `paths.wf_learnings`  (wf-toolkit learnings — read + append)

You distil the raw session log into learnings. Each session record carries two
feedback fields; route each by field:

- `feedback.repo_observation` → `$LEARNINGS` — a learning about **the project's code**.
- `feedback.wf_friction` → `$WF_LEARNINGS` — a learning about **the wf toolkit itself**.

## The entry

Both files hold the same shape:

```yaml
- id: L-001
  statement: "<one actionable sentence naming a concrete artifact, field, or step>"
  sources: ["<session ended_at>"]   # the session(s) this was distilled from
```

You only ever **create and reinforce** entries. A learning is **drained by its
consumer**.

## Process

### Phase 1 — Load

1. Read `$TELEMETRY` — one JSON record per line.
2. Read `$LEARNINGS` and `$WF_LEARNINGS` (each may not exist yet). For each,
   collect the union of every entry's `sources`: that union is the set of sessions
   already compiled into that stream.

### Phase 2 — Select what's new

Walk the session records. For each channel (`repo_observation` → `$LEARNINGS`,
`wf_friction` → `$WF_LEARNINGS`):

- skip the record if its `ended_at` is already in that channel's `sources` set;
- skip it if that feedback field is empty.

What remains is this run's unprocessed feedback.

### Phase 3 — Distil

Turn each unprocessed observation into a learning, holding the bar:

- **Actionable and concrete.** A learning names a real artifact, field, or step
  and implies an action. Drop the vague — "could be cleaner" is noise, not a
  learning, and produces no entry.
- **Dedup against the entries present in the file.** If an observation restates an
  existing learning, reinforce it: append the session `ended_at` to its `sources` and add
  no duplicate.
- Mint ids monotonically per file (`L-NNN`); never reuse a retired number.

### Phase 4 — Write & commit

1. Append the new and reinforced entries to `$LEARNINGS` and `$WF_LEARNINGS`,
   creating either from its template (`assets/learnings.yaml.tmpl`,
   `assets/wf-learnings.yaml.tmpl`) if absent.
2. Report what you distilled: new entries per stream, reinforcements, and the
   count dropped as non-actionable.
3. Commit both files — they are durable, and leaving them uncommitted is one
   `git clean` from gone. Stage explicit paths, never `git add .`:
   ```sh
   git add $LEARNINGS $WF_LEARNINGS
   git commit -m "learnings: distil <session range>"
   ```
   If the commit fails (hook, identity), report the exact error and halt — never
   `--no-verify`.

### Phase 5 — Telemetry (REQUIRED)

Your last action, always. Run the `wf-basics` §2 `record_session.py` command with
`--agent wf-retrospective`, this run's `--outcome`, and the two feedback answers
(omit a flag when there is nothing concrete). If it errors, continue.

## Hard constraints

- **A run that distils nothing is a valid run.** When every observation is already
  compiled or judged noise, report "nothing new" and commit nothing — never invent
  a learning to look productive.
## Halt conditions

- `$TELEMETRY` is absent or empty — nothing to distil. Report and exit.
