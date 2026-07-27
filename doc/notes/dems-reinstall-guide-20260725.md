# dems re-install guide — target-shape redesign (wf2 `95977a9`)

The checklist for re-installing wf2 into `~/repos/dems` to pick up the 07-24 + 07-25
spec-layer changes (wf-adequacy, no-REQ-tags, close-time drain, design narrative).
`install.sh` never touches an already-scaffolded config and never reconciles stale
renders — every step below exists because a past install missed it.

## 1. Install

```sh
cd ~/repos/dems && git checkout -b wf-toolkit-target-shape   # dems branches off main
bash ~/repos/wf2/install.sh    # (with dems as target, as usual)
```

## 2. Config hand-sync (`~/repos/dems/.wf/config.yaml`)

**Add two path keys** (template wording in wf2's `config.yaml.tmpl`):

```yaml
  design_graph: ".wf/transient/design-graph.json"
  drain_report: ".wf/transient/drain-report.yaml"
```

**Add two id counters**, set to dems' current high-water marks — err high, a skipped
number is free, a reused one is not:

```sh
cd ~/repos/dems
git grep -ohE 'REQ-[0-9]+'    | sed 's/REQ-//'    | sort -n | tail -1
git grep -ohE 'SYS-TC-[0-9]+' | sed 's/SYS-TC-//' | sort -n | tail -1
```

```yaml
id_counters:
  req: <max from grep>
  sys_tc: <max from grep>
```

**Optional comment sync** (cosmetic, but stale comments have misled agents before):
`paths.tests` (scanners are now register/retired/render_design/complete-sprint,
SYS-TC only), `paths.design_backlog` ("complete-sprint trims from the merge record",
not "reconcile drains"), the pipeline-state block ("completion derived from the merge
record", not `[REQ]` tags).

## 3. CAPABILITIES.yaml header replacement

dems' live `.wf/CAPABILITIES.yaml` still carries the pre-07-14 **drain-at-design**
doctrine in its header comments. Replace the header (comments only — keep every
entry and `last_updated`/`updated_by` untouched) with the current
`skills/wf-po/assets/capabilities.yaml.tmpl` header: drain only on proof (SYS-TCs
shipped + adequacy `adequate`).

## 4. Backlog trim-parseability check (load-bearing — do before the first sprint close)

`complete-sprint` now trims `.wf/design-backlog.md` mechanically. It parses:

- block headers `## <title> — serves CAP-NNN / L-NNN` (drivers read from this line),
- lane labels `**Component requirements:**` / `**System test cases:**` / `**Supersedes:**`,
- bullets `- **REQ-<n>**` / `- **SYS-TC-<n>**` with indented continuation lines.

Eyeball the live backlog against those shapes and normalize any hand-grown block
that drifted — a bullet the parser can't see is an id that **never trims**, so its
design never empties and its drivers never drain. Old blocks without a
`**Narrative:**` line are fine (the trim doesn't need it); a narrative gets written
when a design is next re-cut, and every **new** design/slice requires one
(`wf slice check` A6).

## 5. Stale-render / leftover cleanup

- Delete `scripts/hygiene_files.sh` and `scripts/hygiene_filter.py` — obsoleted by
  `wf hygiene check --rule/--path/--summary` (L-060), still owed from that pass.
- Existing `[REQ:...]` and AC-id tags in dems tests are **inert — leave them**; they
  decay with file churn. No strip pass, no sweep.
- Check for stale renders the installer can't reconcile (the wf-swa/​`__pycache__`
  class): nothing was renamed this round, so expect none — a quick
  `ls .claude/skills .claude/agents` sanity glance is enough.

## 6. Verify the install

```sh
cd ~/repos/dems
ls .claude/agents/wf-adequacy.md                       # new agent rendered (model: opus)
ls .claude/skills/wf-sa/references/promise-sweep.md    # shared sweep taxonomy rendered
ls .wf/tools/reconcile/                                # next_id.py GONE; reconcile/register/retired remain
python3 .wf/tools/reconcile/register.py --tests <each paths.tests root>   # single SYS-TC table renders
grep -L "proof gate" .claude/skills/wf-sa/SKILL.md     # prints nothing = new Phase 1 installed
```

Do **not** smoke-test `wf pipeline complete-sprint` by hand — it drains the live
working set (backlog trim, learnings drain, state reset). It runs only at a real
sprint close.

## 7. First-run watchpoints (this IS the dogfood)

- **First sprint close:** read `.wf/transient/drain-report.yaml` and diff the backlog
  trim against the sprint's merged tasks — parser correctness on the real backlog is
  the untested seam.
- **First wf-sa run after a close:** the proof gate consumes the report and dispatches
  wf-adequacy; capabilities drained under the old drain-at-design doctrine (CAP-001..020)
  are already gone and stay gone — the human may name any believed-built capability as
  an extra candidate for the gate.
- **First slice cut:** `wf slice check` now fails without a `## Design narrative`
  (A6) — expected on the first run, not a bug.
- File a wf-friction for anything that grinds; delete this note when the re-install
  has happened and the first close ran clean.
