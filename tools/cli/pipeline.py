"""wf pipeline — the orchestration decision brain.

The driver holds no scheduling logic of its own: it asks this module what to do next.
A sprint is PR packaging; the unit of work is one **stage** — the set of tasks with no
dependency between them, cut fresh at every design pass and run in one batch:

  - ``load-stage`` reads the stage artifact and records, in the run state, its task list
    and its repo-lifetime-monotonic id.
  - ``next`` returns that stage's dispatch frontier (the pending tasks, capped to free
    slots) plus ``terminal.stage_done``. There is no layering and no dependency graph, so
    a blocked task dooms nothing: the stage closes with what merged and the rest re-enters
    at the next cut.
  - the close-time verbs that drain the working set — ``capability-complete``,
    ``drain-capability``, ``append-residuals``, ``complete-sprint`` — live in ``drain``,
    whose command table is merged into this one so the whole surface stays ``wf pipeline``.

State *mutations* (transition / dispatch / complete-task / …) and return *routing* are
separate verbs. Everything mechanical lives here so the driver never schedules by hand.
"""
from __future__ import annotations

import datetime
import re
from pathlib import Path

import common
import drain

# Effective task status → bucket. Live state (pipeline_state.task_states[id].status)
# wins; otherwise the stage-authored status; otherwise "pending".
_COMPLETED = {"completed", "done"}          # done = the stage's authored terminal-OK
_OCCUPIES_SLOT = {"building", "reviewing", "dispatching"}  # active; counts against the cap
_PARKED = {"design_issue"}                  # hit a design issue; re-enters at the next cut
_ESCALATED = {"escalated"}                  # terminal failure for this task
_BLOCKED = {"blocked"}                      # terminal failure for this task
_APPROVED = {"approved"}                    # passed every review pass; awaiting the batch merge


def _effective_status(task_id, authored_status, task_states):
    """Live state wins; fall back to the stage-authored status; default pending."""
    live = (task_states.get(task_id) or {}).get("status")
    return live or authored_status or "pending"


def _covers_of(entry):
    cov = (entry or {}).get("covers")
    if cov is None:
        return []
    return [str(c) for c in (cov if isinstance(cov, list) else [cov])]


# ── The stage artifact ───────────────────────────────────────────────────────


def _stage_doc(args, paths=None):
    """The stage artifact, or {}. It is archived and deleted the moment its stage merges,
    so every close-time read has to tolerate its absence."""
    paths = paths if paths is not None else (common.config_doc(args.config).get("paths") or {})
    if not paths.get("stage"):
        return {}
    return common.load_yaml(common.resolve_path(args.config, "stage", None), optional=True)


def _stage_tasks(stage_doc):
    return [t for t in (stage_doc.get("tasks") or []) if isinstance(t, dict) and t.get("id")]


def _serves_of(stage_doc):
    raw = stage_doc.get("serves") or []
    return [str(s).strip() for s in (raw if isinstance(raw, list) else [raw]) if str(s).strip()]


def _stage_id(stage_doc):
    """The stage's repo-lifetime-monotonic id, minted from ``id_counters.stage``. It keys
    the task ids, the archive filename and the stage-timing state — a number that restarts
    is what produced the s1 run's phantom multi-day sub-layers."""
    raw = stage_doc.get("stage")
    if raw is None:
        common.die("the stage artifact declares no `stage:` id — mint one from "
                   "id_counters.stage")
    try:
        return int(raw)
    except (TypeError, ValueError):
        common.die(f"stage id {raw!r} is not a number — id_counters.stage is a monotonic "
                   f"integer high-water mark")


def _loaded_stage(doc):
    """(id, task_ids) for the stage the run state carries."""
    stage = doc.get("stage")
    ids = [t for t in ((stage or {}).get("tasks") or []) if t] if isinstance(stage, dict) else []
    if not isinstance(stage, dict) or stage.get("id") is None or not ids:
        common.die("no stage loaded — run 'wf pipeline load-stage'")
    return int(stage["id"]), ids


def _load_stage(rest):
    """Record what the driver runs next: the stage artifact's task list and its stage id.

    Idempotent — a re-load of the same file keeps every task's live state, so a resumed
    run does not restart its in-flight work. Nothing from an earlier stage is dropped or
    renumbered: each cut is a fresh file with fresh ids, and the merge record of what
    already shipped is what the close-time drain reads."""
    args = common.base_parser("pipeline load-stage").parse_args(rest)

    stage_doc = common.load_yaml(common.resolve_path(args.config, "stage", None))
    stage_id = _stage_id(stage_doc)
    tasks = _stage_tasks(stage_doc)
    if not tasks:
        common.die("the stage artifact declares no tasks — nothing to run")
    ids = [t["id"] for t in tasks]

    doc = _load_state(args)
    prior = doc.get("stage") or {}
    changed = prior.get("id") != stage_id or list(prior.get("tasks") or []) != ids

    states = doc.setdefault("task_states", {})
    for task in tasks:
        # `covers` is recorded per task because the stage file dies at its own merge:
        # by the PR's close-time drain the run state is the only record of what the
        # sprint's earlier stages built.
        covers = _covers_of(task)
        entry = states.setdefault(task["id"], {})
        if covers:
            entry["covers"] = covers
    doc["stage"] = {"id": stage_id, "tasks": ids}

    served = [str(s) for s in (doc.get("serves") or [])]
    served += [s for s in _serves_of(stage_doc) if s not in served]
    if served:
        doc["serves"] = served
    if changed:
        doc.setdefault("history", []).append({
            "ts": common.now(), "event": "stage_loaded", "stage": stage_id, "tasks": ids,
        })
    _save_state(args, doc)

    common.emit({"stage": stage_id, "tasks": ids, "count": len(ids)}, args.format)
    return 0


# ── The frontier query ───────────────────────────────────────────────────────


def _dispatch_entry(task_id, worktree_base, sprint_id):
    return {"task_id": task_id, "worktree": f"{worktree_base}/{sprint_id}-{task_id}"}


def _in_flight_entry(task_id, task_status, history):
    """A running task with the age of the dispatch that started it. `dispatch` only
    records the intent to start an agent — nothing proves the agent was ever spawned,
    so an unspawned task holds its slot forever, indistinguishable from a slow one.
    The elapsed clock is what makes that visible."""
    entry = {"task_id": task_id, "status": task_status, "agent": None,
             "dispatched_at": None, "since_s": None}
    for h in reversed(history):
        if h.get("event") == "dispatch" and h.get("task_id") == task_id:
            entry["agent"] = h.get("agent")
            entry["dispatched_at"] = h.get("ts")
            entry["since_s"] = _elapsed_s(h.get("ts"))
            break
    return entry


def _elapsed_s(ts):
    """Whole seconds from an ISO-Z timestamp to now, or None if it will not parse."""
    try:
        then = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None
    return int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds())


def _next(rest):
    """The loaded stage's dispatch frontier + its settlement signal.

    ``dispatch`` is the set of pending tasks to start now (capped to free slots); ``ready``
    is the overflow that dispatches as slots free. The whole stage goes at once — its tasks
    are independent by construction, so ``driver.max_parallel`` is the only thing
    serialising them.

    ``terminal.stage_done`` is true when nothing is still pending or in-flight; the driver
    then runs the batch merge. An approved, parked, escalated or blocked task does not hold
    the stage open and dooms nothing — the stage closes with what merged and the rest
    re-enters at the next cut. ``terminal.halt`` is set only when no stage is loaded."""
    args = common.base_parser("pipeline next").parse_args(rest)

    state = common.load_yaml(
        common.resolve_path(args.config, "pipeline_state", None), optional=True
    )
    cfg = common.config_doc(args.config)
    cap = int((cfg.get("driver") or {}).get("max_parallel") or 4)
    worktree_base = (cfg.get("parallel") or {}).get("worktree_base") \
        or ".wf/transient/worktrees"
    sprint_id = state.get("sprint_id") or "sprint"

    stage = state.get("stage") if isinstance(state.get("stage"), dict) else {}
    stage_ids = [t for t in (stage.get("tasks") or []) if t]
    if stage.get("id") is None or not stage_ids:
        common.emit(_halt_frontier("no stage loaded; run 'wf pipeline load-stage'"),
                    args.format)
        return 0

    authored = {t["id"]: t.get("status") for t in _stage_tasks(_stage_doc(args, cfg.get("paths")))}
    task_states = state.get("task_states") or {}

    def status(tid):
        return _effective_status(tid, authored.get(tid), task_states)

    history = state.get("history") or []
    in_flight = [_in_flight_entry(t, status(t), history)
                 for t in stage_ids if status(t) in _OCCUPIES_SLOT]
    pending = sorted(t for t in stage_ids if status(t) not in (
        _COMPLETED | _OCCUPIES_SLOT | _PARKED | _ESCALATED | _BLOCKED | _APPROVED))

    slots = max(0, cap - len(in_flight))
    dispatch = [_dispatch_entry(t, worktree_base, sprint_id) for t in pending[:slots]]
    ready = pending[slots:]

    common.emit({
        "stage": {"id": int(stage["id"]), "tasks": stage_ids},
        "dispatch": dispatch,
        "ready": ready,
        "in_flight": in_flight,
        "approved": [t for t in stage_ids if status(t) in _APPROVED],
        "repairing": [t for t in stage_ids if status(t) in _PARKED],
        "escalated": [t for t in stage_ids if status(t) in _ESCALATED],
        "blocked": [t for t in stage_ids if status(t) in _BLOCKED],
        "terminal": {"stage_done": not (dispatch or ready or in_flight), "halt": None},
    }, args.format)
    return 0


def _halt_frontier(reason):
    return {
        "stage": None,
        "dispatch": [], "ready": [], "in_flight": [],
        "approved": [], "repairing": [], "escalated": [], "blocked": [],
        "terminal": {"stage_done": False, "halt": {"reason": reason}},
    }


# ── Run-state reads ──────────────────────────────────────────────────────────


def _state_path(args) -> Path:
    return common.resolve_path(args.config, "pipeline_state", None)


def _load_state(args) -> dict:
    """Read pipeline_state.yaml — empty doc if it does not exist yet (a fresh sprint
    has no state file; the first mutation creates it)."""
    return common.load_yaml(_state_path(args), optional=True)


def _save_state(args, doc) -> None:
    common.write_yaml(_state_path(args), doc)


def _current_phase(rest):
    args = common.base_parser("pipeline current-phase").parse_args(rest)
    doc = _load_state(args)
    stage = doc.get("stage") if isinstance(doc.get("stage"), dict) else {}
    common.emit(
        {
            "phase": doc.get("current_phase", "idle"),
            "sprint_branch": doc.get("sprint_branch")
            or common.current_branch(common.project_root(args.config)),
            "stage": stage.get("id"),
        },
        args.format,
    )
    return 0


def _task_state(rest):
    p = common.base_parser("pipeline task-state")
    p.add_argument("task_id")
    args = p.parse_args(rest)
    doc = _load_state(args)
    ts = (doc.get("task_states", {}) or {}).get(args.task_id) or {}
    common.emit(
        {
            "task_id": args.task_id,
            "state": ts.get("status", "pending"),
            "attempt_counter": ts.get("attempt_counter", 0),
            "pass_index": ts.get("pass_index", 0),
            "branch": ts.get("branch", ""),
            "worktree_path": ts.get("worktree_path", ""),
            "build_commit": ts.get("build_commit"),
        },
        args.format,
    )
    return 0


def _unresolved_design_issues(rest):
    args = common.base_parser("pipeline unresolved-design-issues").parse_args(rest)
    doc = _load_state(args)
    raw = doc.get("design_issues", {}) or {}
    issues = []
    items = raw.items() if isinstance(raw, dict) else (
        (None, v) for v in raw if isinstance(v, dict))
    for k, v in items:
        if not isinstance(v, dict):
            continue
        status = v.get("status", "open")
        if status in ("open", "routing"):
            issues.append({
                "di_id": v.get("issue_id") or v.get("di_id") or k,
                "task_id": v.get("task_id") or k,
                "fix_kind": v.get("fix_kind", ""),
                "status": status,
            })
    common.emit({"count": len(issues), "issues": issues}, args.format)
    return 0


def _attempt_counter(rest):
    p = common.base_parser("pipeline attempt-counter")
    p.add_argument("task_id")
    args = p.parse_args(rest)
    ts = (_load_state(args).get("task_states", {}) or {}).get(args.task_id) or {}
    common.emit({"value": ts.get("attempt_counter", 0)}, args.format)
    return 0


def _history_tail(rest):
    p = common.base_parser("pipeline history-tail")
    p.add_argument("n", nargs="?", default="20")
    args = p.parse_args(rest)
    hist = _load_state(args).get("history", []) or []
    try:
        n = int(args.n)
    except (TypeError, ValueError):
        n = 20
    common.emit(hist[-n:] if n > 0 else [], args.format)
    return 0


# ── Run-state mutations ──────────────────────────────────────────────────────


def _transition(rest):
    """Advance the macro-phase pointer (current_phase) and append it to history. The
    phase is a resume/observability breadcrumb only — `next` never consults it — so this
    is an unguarded write: the controller is the sole writer and stamps the phase it is
    entering.

    ``--sprint-id`` stamps whose sprint this run state belongs to (the driver passes it
    when it opens one). It names the per-task worktrees, so without it they are named for
    a sprint the driver is not running. Omitting the flag leaves an already-recorded id
    alone."""
    p = common.base_parser("pipeline transition")
    p.add_argument("--to", dest="to_phase", required=True)
    p.add_argument("--reason")
    p.add_argument("--sprint-id", dest="sprint_id",
                   help="record the sprint this run state belongs to")
    args = p.parse_args(rest)

    doc = _load_state(args)
    if args.sprint_id:
        doc["sprint_id"] = args.sprint_id
    doc["current_phase"] = args.to_phase
    doc["last_transition"] = {"to": args.to_phase, "timestamp": common.now(), "reason": args.reason}
    entry = {"ts": common.now(), "event": "transition", "to_phase": args.to_phase}
    if args.reason:
        entry["reason"] = args.reason
    doc.setdefault("history", []).append(entry)
    _save_state(args, doc)
    return 0


# Agent role → task state. Build is `building`; every other dispatch is a review
# pass (build → review → security-review → …), all of which occupy a slot as
# `reviewing`.
_DISPATCH_STATE = {
    "wf-build": "building", "build": "building",
    "wf-retrospective": "retrospective",
}


def _dispatch(rest):
    p = common.base_parser("pipeline dispatch")
    p.add_argument("--agent", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--attempt", required=True)
    p.add_argument("--pass", dest="pass_index", type=int,
                   help="review-pass index this dispatch advances to (N-pass loop)")
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task, {})
    ts["status"] = _DISPATCH_STATE.get(args.agent, "reviewing")
    if args.pass_index is not None:
        ts["pass_index"] = args.pass_index
    entry = {"ts": common.now(), "event": "dispatch", "agent": args.agent,
             "task_id": args.task, "attempt": int(args.attempt)}
    if args.pass_index is not None:
        entry["pass_index"] = args.pass_index
    doc.setdefault("history", []).append(entry)
    _save_state(args, doc)
    conf = {"ok": True, "event": "dispatch", "task_id": args.task, "agent": args.agent,
            "status": ts["status"], "attempt": int(args.attempt)}
    if args.pass_index is not None:
        conf["pass_index"] = args.pass_index
    common.emit(conf, args.format)
    return 0


def _complete_task(rest):
    p = common.base_parser("pipeline complete-task")
    p.add_argument("task_id")
    p.add_argument("--commit", required=True)
    p.add_argument("--merge", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task_id, {})
    ts["status"] = "completed"
    ts["build_commit"] = args.commit
    ts["merge_commit"] = args.merge
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "task_completed", "task_id": args.task_id,
        "build_commit": args.commit, "merge_commit": args.merge,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_completed", "task_id": args.task_id,
                 "status": "completed"}, args.format)
    return 0


def _approve_task(rest):
    """Mark a task as having passed every review pass — built and approved, awaiting
    the batch merge at stage close. Distinct from completed (which records the merge)."""
    p = common.base_parser("pipeline approve-task")
    p.add_argument("task_id")
    p.add_argument("--commit", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task_id, {})
    ts["status"] = "approved"
    ts["build_commit"] = args.commit
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "task_approved", "task_id": args.task_id,
        "build_commit": args.commit,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_approved", "task_id": args.task_id,
                 "status": "approved"}, args.format)
    return 0


def _reject_task(rest):
    p = common.base_parser("pipeline reject-task")
    p.add_argument("task_id")
    # --feedback is an explicit path ARGUMENT: stored verbatim, never re-anchored.
    p.add_argument("--feedback", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task_id, {})
    ts["attempt_counter"] = int(ts.get("attempt_counter", 0)) + 1
    ts["status"] = "building"
    ts["pass_index"] = 0  # a rejection restarts the pass pipeline at build (fix mode)
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "task_rejected", "task_id": args.task_id,
        "feedback_path": args.feedback, "next_attempt": ts["attempt_counter"],
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_rejected", "task_id": args.task_id,
                 "status": "building", "attempt": ts["attempt_counter"]}, args.format)
    return 0


def _retry_task(rest):
    """Send a task back to build after a dispatch that left no artifact to route on.
    The attempt is spent — an agent that mis-steps every attempt is not converging — but
    nothing about the task is decided, so it returns to building rather than blocked.
    Distinct from reject-task, which carries a review's findings for the build to fix."""
    p = common.base_parser("pipeline retry-task")
    p.add_argument("task_id")
    p.add_argument("--reason", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task_id, {})
    ts["attempt_counter"] = int(ts.get("attempt_counter", 0)) + 1
    ts["status"] = "building"
    ts["pass_index"] = 0
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "task_retried", "task_id": args.task_id,
        "reason": args.reason, "next_attempt": ts["attempt_counter"],
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_retried", "task_id": args.task_id,
                 "status": "building", "attempt": ts["attempt_counter"]}, args.format)
    return 0


def _block_task(rest):
    """Give up on ONE task. Terminal for that task and nothing else: a stage's tasks are
    independent, so a block dooms no sibling — the stage closes with the rest and the
    blocked work re-enters at the next cut. The reason is kept because the driver reads it
    back into the design issue that carries the work forward."""
    p = common.base_parser("pipeline block-task")
    p.add_argument("task_id")
    p.add_argument("--reason", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    blocked = doc.setdefault("blocked_tasks", {})
    if isinstance(blocked, list):
        blocked.append({"task_id": args.task_id, "reason": args.reason})
    else:
        blocked[args.task_id] = {"reason": args.reason}
    doc.setdefault("task_states", {}).setdefault(args.task_id, {})["status"] = "blocked"
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "task_blocked", "task_id": args.task_id, "reason": args.reason,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_blocked", "task_id": args.task_id,
                 "status": "blocked"}, args.format)
    return 0


def _reclaim_stale(rest):
    """Cold-resume safety: reset orphan slots (building/reviewing/dispatching) left by
    an interrupted run back to pending so `next` re-surfaces them. The attempt counter
    is NOT bumped — an external disruption is not a task failure."""
    args = common.base_parser("pipeline reclaim-stale").parse_args(rest)
    doc = _load_state(args)
    reclaimed = []
    for tid, ts in (doc.get("task_states") or {}).items():
        if (ts or {}).get("status") in _OCCUPIES_SLOT:
            ts["status"] = "pending"
            reclaimed.append({"task_id": tid, "from_status": ts.get("status", "")})
    if reclaimed:
        hist = doc.setdefault("history", [])
        for r in reclaimed:
            hist.append({"ts": common.now(), "event": "reclaimed_stale_dispatch",
                         "task_id": r["task_id"]})
        _save_state(args, doc)
    common.emit({"reclaimed": reclaimed}, args.format)
    return 0


def _record_design_issue(rest):
    p = common.base_parser("pipeline record-design-issue")
    p.add_argument("di_id")
    # A stage-boundary DI is task-less — it names no task, so --task is optional. Omit it
    # and NO task is parked: there is nothing to park, and a phantom task_states entry is
    # exactly the synthetic-task trap a task-less DI exists to avoid.
    p.add_argument("--task", default=None)
    p.add_argument("--severity", required=True)
    # Build/review/stage-repair raise a BARE issue — no fix_kind. The design role
    # classifies it at the next cut and writes the kind back to the host artifact; this
    # run-state twin only tracks status.
    p.add_argument("--fix_kind", default=None)
    args = p.parse_args(rest)

    doc = _load_state(args)
    issues = doc.setdefault("design_issues", {})
    entry = {
        "issue_id": args.di_id, "task_id": args.task, "severity": args.severity,
        "fix_kind": args.fix_kind, "status": "open",
    }
    if isinstance(issues, list):
        issues.append(entry)
    else:
        issues[args.di_id] = entry
    if args.task:
        doc.setdefault("task_states", {}).setdefault(args.task, {})["status"] = "design_issue"
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "design_issue_recorded", "di_id": args.di_id,
        "task_id": args.task, "fix_kind": args.fix_kind, "severity": args.severity,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "design_issue_recorded", "di_id": args.di_id,
                 "task_id": args.task, "status": "open"}, args.format)
    return 0


def _resolve_design_issue(rest):
    """Mark a recorded design issue resolved in pipeline_state so the stage boundary
    stops re-routing it (the HOST design-issues artifact is the design role's record;
    this is the run-state's)."""
    p = common.base_parser("pipeline resolve-design-issue")
    p.add_argument("di_id")
    args = p.parse_args(rest)

    doc = _load_state(args)
    issues = doc.get("design_issues") or {}
    if isinstance(issues, dict):
        entry = issues.get(args.di_id)
        if not isinstance(entry, dict):
            entry = next((v for v in issues.values() if isinstance(v, dict)
                          and (v.get("issue_id") == args.di_id or v.get("di_id") == args.di_id)),
                         None)
    else:
        entry = next((v for v in issues if isinstance(v, dict)
                      and (v.get("issue_id") == args.di_id or v.get("di_id") == args.di_id)),
                     None)
    if not isinstance(entry, dict):
        common.die(f"unknown design issue: {args.di_id}")
    entry["status"] = "resolved"
    # Un-park the implicated task (design_issue → pending) so it can be placed again. A
    # task that already moved on (re-dispatched → building, blocked, …) is left alone.
    tid = entry.get("task_id")
    ts = (doc.get("task_states") or {}).get(tid) if tid else None
    if isinstance(ts, dict) and ts.get("status") == "design_issue":
        ts["status"] = "pending"
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "design_issue_resolved", "di_id": args.di_id,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "design_issue_resolved", "di_id": args.di_id,
                 "status": "resolved"}, args.format)
    return 0


# ── Stage close: timing, summary, the PR body and the history spill ──────────


def _stage_timing(doc):
    """(id, timing) for the loaded stage. Keyed by the repo-lifetime-monotonic stage id,
    so no later stage can ever inherit an earlier one's wall-clock origin."""
    stage_id, _ = _loaded_stage(doc)
    return stage_id, doc.setdefault("stage_summaries", {}).setdefault(
        stage_id, {}).setdefault("timing", {})


def _stage_start(rest):
    """Record the loaded stage's start time (idempotent — a resumed stage keeps its
    original wall-clock origin)."""
    args = common.base_parser("pipeline stage-start").parse_args(rest)
    doc = _load_state(args)
    stage_id, timing = _stage_timing(doc)
    if not timing.get("started_at"):
        timing["started_at"] = common.now()
        _save_state(args, doc)
    common.emit({"stage": stage_id, "started_at": timing["started_at"]}, args.format)
    return 0


def _stage_end(rest):
    """Record the loaded stage's completion time + duration_seconds against its start."""
    args = common.base_parser("pipeline stage-end").parse_args(rest)
    doc = _load_state(args)
    stage_id, timing = _stage_timing(doc)
    end = common.now()
    timing["completed_at"] = end
    started = timing.get("started_at")
    if started:
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            delta = datetime.datetime.strptime(end, fmt) - datetime.datetime.strptime(started, fmt)
            timing["duration_seconds"] = int(delta.total_seconds())
        except ValueError:
            pass
    _save_state(args, doc)
    common.emit({"stage": stage_id, "timing": timing}, args.format)
    return 0


def _stage_summary(rest):
    """Write a compact summary of the loaded stage, deriving the task lists from the live
    task_states. The context-hygiene anchor: at stage close the driver writes this and
    stops re-reading the stage's per-task detail."""
    args = common.base_parser("pipeline stage-summary").parse_args(rest)
    doc = _load_state(args)
    stage_id, stage_ids = _loaded_stage(doc)
    task_states = doc.get("task_states") or {}

    def st(tid):
        return (task_states.get(tid) or {}).get("status", "pending")

    completed = [t for t in stage_ids if st(t) in _COMPLETED]
    summary = {
        "tasks": list(stage_ids),
        "completed": completed,
        "approved": [t for t in stage_ids if st(t) in _APPROVED],
        "escalated": [t for t in stage_ids if st(t) in _ESCALATED],
        "design_issue": [t for t in stage_ids if st(t) in _PARKED],
        "blocked": [t for t in stage_ids if st(t) in _BLOCKED],
        "merged": [
            {"task_id": t, "merge_commit": (task_states.get(t) or {}).get("merge_commit")}
            for t in completed if (task_states.get(t) or {}).get("merge_commit")
        ],
    }
    doc.setdefault("stage_summaries", {}).setdefault(stage_id, {}).update(summary)
    _save_state(args, doc)
    common.emit({"stage": stage_id, **summary}, args.format)
    return 0


def _pr_body_block(stage_doc, stage_id):
    """One stage's contribution to the PR body: what it serves, the end-to-end fact it
    crossed, and the decisions taken below the escalation gate."""
    block = [f"## Stage {stage_id}", ""]
    served = _serves_of(stage_doc)
    if served:
        block += [f"**Serves:** {', '.join(served)}", ""]
    checkpoint = str(stage_doc.get("checkpoint") or "").strip()
    if checkpoint:
        block += [f"**Checkpoint:** {checkpoint}", ""]
    decisions = [str(d).strip() for d in (stage_doc.get("decisions") or []) if str(d).strip()]
    if decisions:
        block += ["**Decisions:**"] + [f"- {d}" for d in decisions] + [""]
    return block


def _append_pr_body(rest):
    """Append the stage's serves / checkpoint / decisions to paths.pr_body — called at
    stage merge, so the accumulator holds every stage the PR batches and the ship step
    folds it in whole. Idempotent per stage: a block already carried appends nothing."""
    args = common.base_parser("pipeline append-pr-body").parse_args(rest)

    stage_doc = common.load_yaml(common.resolve_path(args.config, "stage", None))
    stage_id = _stage_id(stage_doc)
    path = common.resolve_path(args.config, "pr_body", None)
    existing = path.read_text() if path.exists() else ""

    result = {"stage": stage_id, "path": str(path)}
    if re.search(rf"^## Stage {stage_id}\s*$", existing, re.MULTILINE):
        result["appended"] = False
        common.emit(result, args.format)
        return 0

    block = "\n".join(_pr_body_block(stage_doc, stage_id)).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block)
    result["appended"] = True
    common.emit(result, args.format)
    return 0


def _archive_history(rest):
    """Spill the oldest history entries into paths.pipeline_history, keeping at most
    --cap entries live. Both files together preserve the append-only audit trail."""
    p = common.base_parser("pipeline archive-history")
    p.add_argument("--cap", required=True, type=int)
    args = p.parse_args(rest)

    history_path = common.resolve_path(args.config, "pipeline_history", None)
    doc = _load_state(args)
    hist = doc.get("history", []) or []
    if len(hist) <= args.cap:
        return 0
    spill_count = len(hist) - args.cap
    spill, keep = hist[:spill_count], hist[spill_count:]

    history_path.parent.mkdir(parents=True, exist_ok=True)
    prior = common.load_yaml(history_path, optional=True)
    prior_entries = prior.get("history", []) if isinstance(prior, dict) else []
    common.write_yaml(history_path, {"history": prior_entries + spill})

    doc["history"] = keep
    doc.setdefault("history", []).append({
        "ts": common.now(), "event": "history_archived", "spilled_count": spill_count,
        "archive_path": str(history_path),
    })
    _save_state(args, doc)
    return 0


COMMANDS = {
    # the stage and its frontier
    ("pipeline", "load-stage"): _load_stage,
    ("pipeline", "next"): _next,
    # reads
    ("pipeline", "current-phase"): _current_phase,
    ("pipeline", "task-state"): _task_state,
    ("pipeline", "unresolved-design-issues"): _unresolved_design_issues,
    ("pipeline", "attempt-counter"): _attempt_counter,
    ("pipeline", "history-tail"): _history_tail,
    # run-state mutations
    ("pipeline", "transition"): _transition,
    ("pipeline", "dispatch"): _dispatch,
    ("pipeline", "complete-task"): _complete_task,
    ("pipeline", "approve-task"): _approve_task,
    ("pipeline", "reject-task"): _reject_task,
    ("pipeline", "retry-task"): _retry_task,
    ("pipeline", "block-task"): _block_task,
    ("pipeline", "reclaim-stale"): _reclaim_stale,
    ("pipeline", "record-design-issue"): _record_design_issue,
    ("pipeline", "resolve-design-issue"): _resolve_design_issue,
    # stage close
    ("pipeline", "stage-start"): _stage_start,
    ("pipeline", "stage-end"): _stage_end,
    ("pipeline", "stage-summary"): _stage_summary,
    ("pipeline", "append-pr-body"): _append_pr_body,
    ("pipeline", "archive-history"): _archive_history,
}
COMMANDS.update(drain.COMMANDS)
