# Handover — what sprint s1 cost, and the four fixes it forced

**Date:** 2026-08-08. **Run:** dems `sprint/s1`, the first full `wf-driver --once`.
Shipped: 34/34 tasks, 4 increments, 0 escalated, 0 left blocked.

The measured baseline below is the reason for it. It came out of the dispatch logs in
`.wf/transient/driver-logs/` (transient, gitignored, will be wiped) and a telemetry sink
that has since drained to `paths.archive` — so the numbers are written down here rather
than left to be re-derived from artifacts that no longer exist.

---

## The baseline

**34 tasks · 165 files · +13,630 / −2,587 · $460 · 43.1 h wall / 17.0 h working**

### Wall clock

| | hours | share |
|---|---|---|
| span (first dispatch → increment 4 done) | 43.1 | 100% |
| at least one agent running | 17.0 | 39% |
| **dead — nothing running** | **26.6** | **61%** |

Every dead hour is a halt waiting for a human: 12.8 h `tasks_blocked` overnight, 7.4 h
rate-limit overnight, 3.1 h `tl_no_contracts`, 2.1 h `launch_failed`, 1.2 h other. The
"two days" is human availability, not driver throughput.

### Per role (from the dispatch logs — the models differ by role)

| role | model | n | cost | $/disp | startup ctx | median peak ctx | max peak | median dur |
|---|---|---|---|---|---|---|---|---|
| wf-build | sonnet-5 | 62 | $335.79 | 5.42 | 33,434 | 157,086 | 418,198 | 19.9 min |
| wf-review | sonnet-5 | 51 | $70.28 | 1.38 | 33,096 | 92,517 | 162,038 | 3.2 min |
| wf-tl | opus-5 | 5 | $42.25 | 8.45 | 34,789 | 267,630 | 281,005 | 18.3 min |
| wf-designer | opus-5 | 4 | $8.84 | 2.21 | 33,862 | 127,064 | 162,784 | 9.9 min |
| wf-stage-repair | sonnet-5 | 2 | $2.88 | 1.44 | 33,006 | 89,232 | 115,114 | 6.3 min |

Build is 73% of spend and 83% of machine time. `wf telemetry roles` now reproduces this
(it was blind to all five of these roles before this session's fix).

### Token mix

98.0% cache read (947 M), 1.6% cache creation, 0.4% output (4.2 M), 0.0% fresh input.
224 cache-read tokens per output token. 5,406 Bash calls, 1,628 Reads across the run.

### Shape

- per increment — 1: 8.3 h wall / 4.2 h busy / 31 dispatches · 2: 3.0 / 2.9 / 22 ·
  3: 16.1 / 3.5 / 24 · 4: 15.7 / 6.1 / 43
- concurrency: **1.57× average** against `max_parallel: 4`; 11.1 h with one agent in
  flight, 1.5 h with four
- task contract: median 5.5 KB, median 4 acceptance criteria (115 total, 0 for a
  system-test task)
- landed diff per task: median ~330 insertions, range 8 → 1,113
- per-task cost: median $9.27, mean $12.26, max $32.26 (T27), min $1.85 (T34)
- rework: **$96 of $417 task spend (23%)** across 45 repeat dispatches; 14 of 34 tasks
  took more than one build

**Unit economics: $0.034 per landed line, $13.50 per task.** That is not the problem.

---

## Shipped this session

wf2 `8d83612`, dems `69d9e13d`.

1. **A rate limit is a rejection, not a heartbeat.** `_RATE_LIMITED_RE` matched
   `"rateLimitType":` — emitted as a `status: allowed` heartbeat in 122 of 125 logs — so
   any dispatch that exited non-zero for its own reasons slept to the next window
   rollover, twice. Up to ten hours of dead clock on a crash. Now keyed on a
   `rate_limit_info` whose own status is `rejected`, reset read from that same object.
2. **A dispatched role is not a subagent.** `wf telemetry roles` joined only
   `SubagentStop` rows; a driver-launched role runs as its own top-level session and fires
   `Stop`. The report showed the five roles that ran inside interactive sessions and none
   of the four the driver runs. Now 111 of 146 rows attribute.
3. **A sub-layer summary outlived its plan.** Stage numbers restart at 1 each increment
   and summaries are keyed by that number alone, so increment 4's stage 1 inherited
   increment 1's `started_at` — four "28-hour" sub-layers, and the retrospective reasoned
   on top of them. `compute-stages` now drops the old plan's summaries on an increment
   change; a same-increment re-layer keeps them.
4. **Rework counted from `attempt_counter` undercounts threefold** — the counter moves
   only on a review rejection, so s1 read as 5 rebuilt tasks against 14 in the dispatch
   record. The retrospective counts dispatch events now.

Plus: wf-build passes an explicit ≥600000 ms budget to `commands.preflight` (it outruns
the Bash default and is backgrounded regardless of intent — the most-repeated friction in
the whole telemetry history, 5 sources across 3 sprints); two `paths.design_backlog`
references retired with the design backlog; `drain-capability` names an unstamped digest
it declines instead of leaving a capability silently undrained; dems learnings L-124
(fixed) and L-078 (its subject skill deleted) drained.

---

## Ruled out — do not re-propose

**A gate on non-task commits to `sprint/*`.** The s1 retrospective named it the single
highest-leverage toolkit fix in the run's data: two such commits broke the repo-wide lint
gate for every task cut afterward, costing 2 design issues, 1 follow-up task, 2
stage-repair dispatches and 6 independent rediscoveries. The evidence is real and it is
still not a reason to build the gate — only a maintainer debugging wf2 commits to a live
sprint branch, and the product's operator never does. Report it when the next retro
surfaces it again; do not spend driver logic on it.

**The two-drivers race (C41)** — parked until it recurs. The pidfile is designed in the
candidate; build nothing now.

---

## Next session — four decisions, with the evidence

### 1. Sub-layer width (the biggest throughput lever)

1.57× concurrency against a cap of 4. Increment 4's plan was
`[T34] [T26,T27] [T28] [T29] [T30] [T31,T32,T33]` — six sub-layers for nine tasks, a chain
rather than a fan. Three of four lanes idle most of the run. This is a wf-designer /
wf-tl dependency-cutting question, not a driver one. Worth roughly **17 machine-hours →
~8**; it does not touch the 26.6 h of human-wait, which is the larger number.

### 2. Task size

T31: 37 files, +1,113/−738, 3 builds, $25.59. T29: 34 files. T19: 18 files, 4 builds,
$26.16. All three reworked. Median task is 1–3 files and ~330 insertions; these are
increments wearing a task costume. Open question: a mechanical cap at cut time, or a
judgment call left to the designer.

### 3. `wf sprint append-task`

dems `wf-learnings.yaml` L-120: repair mode must author a follow-up task into
`paths.sprint` but the CLI offers no append verb, so every repair hand-writes the live
file — and one such rewrite **truncated `sprint.yaml` to 0 bytes**, momentarily losing
every merged increment's contracts. Severity is data-loss; frequency is once.

### 4. The fixed harness context tax

Every dispatch starts at ~33 k tokens before doing anything: 147 tools, 44 skills, 12
agents, a plugin, and the railway MCP — the maintainer's whole personal Claude Code
environment. For a review peaking at 92 k that is a third of the context. Related trap
worth deciding at the same time: the Bash tool's block message tells the agent *"To wait
for a condition, use Monitor"*, and `driver.agent_cmd` disallows `Monitor`. Agents
attempted it 13 times this run. `Monitor` stays disallowed (ruled 2026-08-08); the
wf-build timeout instruction is the mitigation shipped so far.

---

## Reproducing the analysis

`wf telemetry roles --sink <archived sessions.jsonl>` covers per-role context, tool calls
and duration. Cost and per-dispatch wall time live **only** in the raw
`.wf/transient/driver-logs/*.log` stream — one `{"type":"result"}` line per dispatch
carrying `total_cost_usd`, `duration_ms`, `num_turns` and `usage`. No verb reads them; see
C42.
