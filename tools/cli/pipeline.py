"""wf pipeline — the orchestration decision brain.

The orchestrator (the thin skill) holds no scheduling logic of its own: it asks
this module what to do next. The model is
**staged** — tasks are layered into dependency stages, run one stage at a time
with a barrier between, and within a stage the independent tasks run in parallel
under a concurrency cap:

  - ``compute-stages`` topologically layers ``sprint.yaml`` into ordered stages
    (stage N's tasks depend only on stages 1..N-1) and stores the plan. Run once
    in *preparing*; idempotent on resume.
  - ``next`` returns the *current* stage's dispatch frontier (the pending tasks it
    can start now, capped to free slots) plus whether the stage is settled and
    whether the whole sprint is done. The controller drives off ``terminal``.

Everything mechanical lives here so the controller never schedules by hand. State
*mutations* (transition / dispatch / complete-task / …) and *advancing* the stage
are separate verbs (see the run-state section); return *routing* lives in the
inspect/dispatch helpers.
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

import archive
import common

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reconcile"))
from reconcile import DEFAULT_TEST_GLOBS, harvest  # noqa: E402

# Effective task status → bucket. Live state (pipeline_state.task_states[id].status)
# wins; otherwise the sprint-authored status; otherwise "pending".
_COMPLETED = {"completed", "done"}          # done = sprint.yaml's authored terminal-OK
_OCCUPIES_SLOT = {"building", "reviewing", "dispatching"}  # active; counts against the cap
_PARKED = {"design_issue"}                  # hit a design issue; resolved at end_of_stage
_ESCALATED = {"escalated"}                  # terminal failure — dependents are doomed
_BLOCKED = {"blocked"}                      # a dependency is doomed; can never run
_APPROVED = {"approved"}                    # passed all review passes; awaiting the end_of_stage batch merge


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _effective_status(task_id, sprint_status, task_states):
    """Live state wins; fall back to the sprint-authored status; default pending."""
    live = (task_states.get(task_id) or {}).get("status")
    return live or sprint_status or "pending"


def _detect_cycle(deps_of):
    """Return the ids forming a dependency cycle, or [] if acyclic. ``deps_of`` maps
    each task id to its in-graph dependency ids (unknown deps already filtered out)."""
    WHITE, GREY, BLACK = 0, 1, 2
    color = {tid: WHITE for tid in deps_of}
    path: list = []

    def visit(tid):
        color[tid] = GREY
        path.append(tid)
        for dep in deps_of.get(tid, []):
            if dep not in color:
                continue
            if color[dep] == GREY:
                return path[path.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found:
                    return found
        path.pop()
        color[tid] = BLACK
        return None

    for tid in deps_of:
        if color[tid] == WHITE:
            found = visit(tid)
            if found:
                return found
    return []


def _sprint_graph(sprint):
    """Return (ordered_ids, deps_of) for the sprint. deps_of is filtered to in-graph
    edges only (a depends_on naming an unknown task is dropped, not an error)."""
    tasks = [t for t in (sprint.get("tasks") or []) if isinstance(t, dict) and t.get("id")]
    ids = [t["id"] for t in tasks]
    id_set = set(ids)
    deps_of = {
        t["id"]: [d for d in (t.get("depends_on") or []) if d in id_set] for t in tasks
    }
    return tasks, ids, deps_of


def _layer_stages(ids, deps_of, completed, excluded):
    """Topologically layer ids into ordered stages. A task lands in the lowest stage
    where every dependency is either already ``completed`` or placed in an earlier
    stage. ``completed`` tasks occupy no stage (their dependents are freed); tasks in
    ``excluded`` (blocked) and anything transitively depending on them are not placed
    — returned as ``unplaceable``.

    Returns (stages, unplaceable): stages is a list of sorted id-lists."""
    remaining = [t for t in ids if t not in completed and t not in excluded]
    satisfied = set(completed)
    stages: list[list[str]] = []
    while remaining:
        layer = [t for t in remaining if all(d in satisfied for d in deps_of[t])]
        if not layer:
            break  # leftover tasks depend on excluded/blocked work — unplaceable
        layer.sort()
        stages.append(layer)
        satisfied.update(layer)
        remaining = [t for t in remaining if t not in satisfied]
    return stages, remaining


# ── Stage computation (preparing) ───────────────────────────────────────────


def _compute_stages(rest):
    """Layer sprint.yaml into ordered dependency stages and store the plan in
    pipeline_state (stages.definitions / current / total). Idempotent: if a plan
    already exists it is preserved (preserving an in-flight ``current``) unless
    --force. HALTs on a dependency cycle — a cyclic graph is unschedulable."""
    p = common.base_parser("pipeline compute-stages")
    p.add_argument("--force", action="store_true",
                   help="recompute even if a stage plan already exists")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    doc = _load_state(args)

    existing = doc.get("stages") or {}
    if existing.get("definitions") and not args.force:
        common.emit({
            "stages": existing.get("definitions"),
            "total": existing.get("total"),
            "current": existing.get("current", 1),
            "recomputed": False,
        }, args.format)
        return 0

    tasks, ids, deps_of = _sprint_graph(sprint)
    cycle = _detect_cycle(deps_of)
    if cycle:
        common.die(f"dependency cycle: {' -> '.join(cycle)}")

    task_states = doc.get("task_states") or {}
    status_of = {t["id"]: _effective_status(t["id"], t.get("status"), task_states) for t in tasks}
    completed = {tid for tid in ids if status_of[tid] in _COMPLETED}
    excluded = {tid for tid in ids if status_of[tid] in _BLOCKED}

    stages, unplaceable = _layer_stages(ids, deps_of, completed, excluded)

    doc["stages"] = {"definitions": stages, "current": 1, "total": len(stages)}
    # Tasks that can never run (depend on a blocked/excluded task) are recorded as
    # blocked so the status view and end_of_stage propagation agree with the plan.
    if unplaceable:
        blocked = doc.setdefault("blocked_tasks", {})
        for tid in unplaceable:
            reason = next((d for d in deps_of[tid] if d in excluded), "blocked")
            if isinstance(blocked, list):
                blocked.append({"task_id": tid, "blocked_by": reason})
            else:
                blocked.setdefault(tid, {"blocked_by": reason})
    doc.setdefault("history", []).append({
        "ts": _now(), "event": "stages_computed",
        "total": len(stages), "blocked": sorted(unplaceable),
    })
    _save_state(args, doc)

    common.emit({
        "stages": stages,
        "total": len(stages),
        "current": 1,
        "blocked": sorted(unplaceable),
        "recomputed": True,
    }, args.format)
    return 0


# ── The frontier query (running_stage) ───────────────────────────────────────


def _dispatch_entry(task, worktree_base, sprint_id):
    tid = task["id"]
    return {
        "task_id": tid,
        "worktree": f"{worktree_base}/{sprint_id}-{tid}",
    }


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
    """Return the current stage's dispatch frontier + settlement signals.

    ``dispatch`` is the set of pending current-stage tasks to start now (capped to
    free slots); ``ready`` is the overflow that will dispatch as slots free.
    ``terminal.stage_done`` is true when nothing in the stage is still pending or
    in-flight (the controller then runs end_of_stage); ``terminal.sprint_done`` adds
    "and this is the last stage". ``terminal.halt`` is set only when something is
    structurally wrong (stages not computed, or an out-of-range current).
    ``in_flight`` entries carry the dispatch that started them and its age, so a task
    whose agent was never actually spawned stops looking like a slow one."""
    args = common.base_parser("pipeline next").parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    state = common.load_yaml(
        common.resolve_path(args.config, "pipeline_state", None), optional=True
    )
    cfg = common.config_doc(args.config)
    parallel = cfg.get("parallel") or {}
    cap = int(parallel.get("max_concurrent_tasks") or 4)
    worktree_base = parallel.get("worktree_base") or ".wf/transient/worktrees"
    sprint_id = state.get("sprint_id") or sprint.get("sprint_id") or "sprint"

    tasks_by_id = {
        t["id"]: t
        for t in (sprint.get("tasks") or [])
        if isinstance(t, dict) and t.get("id")
    }
    task_states = state.get("task_states") or {}
    stages = state.get("stages") or {}
    defs = stages.get("definitions")
    cur = int(stages.get("current", 1) or 1)
    total = int(stages.get("total", len(defs) if defs else 0) or 0)

    if not defs:
        common.emit(_halt_frontier("stages not computed; run 'wf pipeline compute-stages'"),
                    args.format)
        return 0
    if cur < 1 or cur > total:
        common.emit(_halt_frontier(f"current stage {cur} out of range 1..{total}"),
                    args.format)
        return 0

    stage_ids = list(defs[cur - 1])

    def status(tid):
        sprint_status = (tasks_by_id.get(tid) or {}).get("status")
        return _effective_status(tid, sprint_status, task_states)

    history = state.get("history") or []
    in_flight = [_in_flight_entry(t, status(t), history)
                 for t in stage_ids if status(t) in _OCCUPIES_SLOT]
    repairing = [t for t in stage_ids if status(t) in _PARKED]
    escalated = [t for t in stage_ids if status(t) in _ESCALATED]
    blocked = [t for t in stage_ids if status(t) in _BLOCKED]
    approved = [t for t in stage_ids if status(t) in _APPROVED]
    pending = [t for t in stage_ids if status(t) not in (
        _COMPLETED | _OCCUPIES_SLOT | _PARKED | _ESCALATED | _BLOCKED | _APPROVED)]
    pending.sort()

    slots = max(0, cap - len(in_flight))
    dispatch = [
        _dispatch_entry(tasks_by_id[t], worktree_base, sprint_id) for t in pending[:slots]
    ]
    ready = pending[slots:]

    # The stage is settled (leave running_stage) once nothing is pending or running.
    # approved tasks (awaiting the batch merge), parked design-issue, escalated and
    # blocked tasks do NOT hold the stage open — end_of_stage merges the approved set,
    # resolves design issues, and propagates blocks before advancing.
    stage_done = not (dispatch or ready or in_flight)
    sprint_done = stage_done and cur >= total

    out = {
        "stage": {"index": cur, "total": total, "tasks": stage_ids},
        "dispatch": dispatch,
        "ready": ready,
        "in_flight": in_flight,
        "approved": approved,
        "repairing": repairing,
        "escalated": escalated,
        "blocked": blocked,
        "terminal": {"stage_done": stage_done, "sprint_done": sprint_done, "halt": None},
    }
    common.emit(out, args.format)
    return 0


def _halt_frontier(reason):
    return {
        "stage": None,
        "dispatch": [], "ready": [], "in_flight": [],
        "repairing": [], "escalated": [], "blocked": [],
        "terminal": {"stage_done": False, "sprint_done": False, "halt": {"reason": reason}},
    }


# ── Run-state reads ──────────────────────────────────────────────────────────


def _state_path(args) -> Path:
    return common.resolve_path(args.config, "pipeline_state", None)


def _load_state(args) -> dict:
    """Read pipeline_state.yaml — empty doc if it does not exist yet (a fresh sprint
    has no state file; the first mutation creates it)."""
    return common.load_yaml(_state_path(args), optional=True)


def _write_yaml(path: Path, doc) -> None:
    """Atomic YAML write: render to a sibling temp file, then os-replace it in.
    Concurrent readers (and parallel-worktree appenders) never see a partial
    write."""
    import yaml as _yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    tmp.replace(path)


def _save_state(args, doc) -> None:
    _write_yaml(_state_path(args), doc)


def _current_phase(rest):
    args = common.base_parser("pipeline current-phase").parse_args(rest)
    doc = _load_state(args)
    stages = doc.get("stages") or {}
    common.emit(
        {
            "phase": doc.get("current_phase", "idle"),
            "sprint_branch": doc.get("sprint_branch")
            or common.current_branch(common.project_root(args.config)),
            "stage": stages.get("current") if stages.get("definitions") else None,
            "total_stages": stages.get("total") if stages.get("definitions") else None,
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


def _blocked_tasks(rest):
    args = common.base_parser("pipeline blocked-tasks").parse_args(rest)
    doc = _load_state(args)
    raw = doc.get("blocked_tasks", {}) or {}
    tasks = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            entry = {"task_id": k}
            if isinstance(v, dict):
                entry.update(v)
            tasks.append(entry)
    elif isinstance(raw, list):
        tasks = [t for t in raw if isinstance(t, dict)]
    common.emit({"count": len(tasks), "tasks": tasks}, args.format)
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
    entering."""
    p = common.base_parser("pipeline transition")
    p.add_argument("--to", dest="to_phase", required=True)
    p.add_argument("--reason")
    args = p.parse_args(rest)

    doc = _load_state(args)
    doc["current_phase"] = args.to_phase
    doc["last_transition"] = {"to": args.to_phase, "timestamp": _now(), "reason": args.reason}
    entry = {"ts": _now(), "event": "transition", "to_phase": args.to_phase}
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
    entry = {"ts": _now(), "event": "dispatch", "agent": args.agent,
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
        "ts": _now(), "event": "task_completed", "task_id": args.task_id,
        "build_commit": args.commit, "merge_commit": args.merge,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_completed", "task_id": args.task_id,
                 "status": "completed"}, args.format)
    return 0


def _approve_task(rest):
    """Mark a task as having passed every review pass — built and approved, awaiting
    the end_of_stage batch merge. Distinct from completed (which records the merge)."""
    p = common.base_parser("pipeline approve-task")
    p.add_argument("task_id")
    p.add_argument("--commit", required=True)
    args = p.parse_args(rest)

    doc = _load_state(args)
    ts = doc.setdefault("task_states", {}).setdefault(args.task_id, {})
    ts["status"] = "approved"
    ts["build_commit"] = args.commit
    doc.setdefault("history", []).append({
        "ts": _now(), "event": "task_approved", "task_id": args.task_id,
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
        "ts": _now(), "event": "task_rejected", "task_id": args.task_id,
        "feedback_path": args.feedback, "next_attempt": ts["attempt_counter"],
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "task_rejected", "task_id": args.task_id,
                 "status": "building", "attempt": ts["attempt_counter"]}, args.format)
    return 0


def _block_task(rest):
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
        "ts": _now(), "event": "task_blocked", "task_id": args.task_id, "reason": args.reason,
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
    stale = _OCCUPIES_SLOT
    reclaimed = []
    for tid, ts in (doc.get("task_states") or {}).items():
        if (ts or {}).get("status") in stale:
            ts["status"] = "pending"
            reclaimed.append({"task_id": tid, "from_status": ts.get("status", "")})
    if reclaimed:
        hist = doc.setdefault("history", [])
        for r in reclaimed:
            hist.append({"ts": _now(), "event": "reclaimed_stale_dispatch",
                         "task_id": r["task_id"]})
        _save_state(args, doc)
    common.emit({"reclaimed": reclaimed}, args.format)
    return 0


def _record_design_issue(rest):
    p = common.base_parser("pipeline record-design-issue")
    p.add_argument("di_id")
    # A stage-boundary DI is task-less — it names no sprint task, so --task is optional.
    # Omit it and NO task is parked: there is nothing to park, and a phantom task_states
    # entry is exactly the synthetic-task trap a task-less DI exists to avoid.
    p.add_argument("--task", default=None)
    p.add_argument("--severity", required=True)
    # Build/review/stage-repair raise a BARE issue — no fix_kind. wf-spec-fix classifies it
    # and writes the kind back to the host artifact; this run-state twin only tracks status.
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
        "ts": _now(), "event": "design_issue_recorded", "di_id": args.di_id,
        "task_id": args.task, "fix_kind": args.fix_kind, "severity": args.severity,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "design_issue_recorded", "di_id": args.di_id,
                 "task_id": args.task, "status": "open"}, args.format)
    return 0


def _resolve_design_issue(rest):
    """Mark a recorded design issue resolved in pipeline_state so the stage boundary
    stops re-routing it (the HOST design-issues artifact is the fix agent's record;
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
    # Un-park the implicated task (design_issue → pending) so the scheduler can place
    # it again — e.g. behind a component_defect follow-up task after a re-layer. A task
    # that already moved on (re-dispatched → building, blocked, …) is left alone.
    tid = entry.get("task_id")
    ts = (doc.get("task_states") or {}).get(tid) if tid else None
    if isinstance(ts, dict) and ts.get("status") == "design_issue":
        ts["status"] = "pending"
    doc.setdefault("history", []).append({
        "ts": _now(), "event": "design_issue_resolved", "di_id": args.di_id,
    })
    _save_state(args, doc)
    common.emit({"ok": True, "event": "design_issue_resolved", "di_id": args.di_id,
                 "status": "resolved"}, args.format)
    return 0


# ── Staged-model mutations ───────────────────────────────────────────────────


def _advance_stage(rest):
    """Move to the next stage (current += 1). No-op (advanced: false) when already on
    the last stage — that is sprint_done, the controller's signal to end_of_sprint."""
    args = common.base_parser("pipeline advance-stage").parse_args(rest)
    doc = _load_state(args)
    stages = doc.get("stages") or {}
    cur = int(stages.get("current", 1) or 1)
    total = int(stages.get("total", 0) or 0)
    if cur >= total:
        common.emit({"advanced": False, "current": cur, "total": total}, args.format)
        return 0
    stages["current"] = cur + 1
    doc["stages"] = stages
    doc.setdefault("history", []).append({
        "ts": _now(), "event": "stage_advanced", "from_stage": cur, "to_stage": cur + 1,
    })
    _save_state(args, doc)
    common.emit({"advanced": True, "current": cur + 1, "total": total}, args.format)
    return 0


def _propagate_blocks(rest):
    """Mark every task transitively depending on an escalated/blocked task as blocked.
    The staged-model escalation propagation: an escalated dependency dooms its dependents
    in any later stage, so they are never dispatched. Mechanical — recomputes the doomed
    set over the sprint DAG and records the newly-doomed as blocked."""
    args = common.base_parser("pipeline propagate-blocks").parse_args(rest)
    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    doc = _load_state(args)
    tasks, ids, deps_of = _sprint_graph(sprint)
    task_states = doc.get("task_states") or {}
    status_of = {t["id"]: _effective_status(t["id"], t.get("status"), task_states) for t in tasks}

    doomed = {tid for tid in ids if status_of[tid] in (_ESCALATED | _BLOCKED)}
    raw_blocked = doc.get("blocked_tasks") or {}
    if isinstance(raw_blocked, dict):
        doomed |= set(raw_blocked)
    elif isinstance(raw_blocked, list):
        doomed |= {b.get("task_id") for b in raw_blocked if isinstance(b, dict)}

    changed = True
    while changed:
        changed = False
        for tid in ids:
            if tid in doomed or status_of[tid] in _COMPLETED:
                continue
            if any(d in doomed for d in deps_of[tid]):
                doomed.add(tid)
                changed = True

    newly = []
    blocked = doc.setdefault("blocked_tasks", {})
    for tid in ids:
        if tid in doomed and status_of[tid] not in (_ESCALATED | _BLOCKED | _COMPLETED):
            reason = next((d for d in deps_of[tid] if d in doomed), "blocked")
            if isinstance(blocked, list):
                blocked.append({"task_id": tid, "blocked_by": reason})
            else:
                blocked.setdefault(tid, {"blocked_by": reason})
            doc.setdefault("task_states", {}).setdefault(tid, {})["status"] = "blocked"
            newly.append(tid)
    if newly:
        doc.setdefault("history", []).append({
            "ts": _now(), "event": "blocks_propagated", "blocked": sorted(newly),
        })
        _save_state(args, doc)
    common.emit({"blocked": sorted(newly)}, args.format)
    return 0


def _stage_start(rest):
    """Record stage start time (idempotent — preserves an existing started_at so a
    resumed stage keeps its original wall-clock origin)."""
    p = common.base_parser("pipeline stage-start")
    p.add_argument("--stage", required=True, type=int)
    args = p.parse_args(rest)
    doc = _load_state(args)
    timing = doc.setdefault("stage_summaries", {}).setdefault(args.stage, {}).setdefault("timing", {})
    if not timing.get("started_at"):
        timing["started_at"] = _now()
        _save_state(args, doc)
    common.emit({"stage": args.stage, "started_at": timing["started_at"]}, args.format)
    return 0


def _stage_end(rest):
    """Record stage completion time + duration_seconds against the recorded start."""
    p = common.base_parser("pipeline stage-end")
    p.add_argument("--stage", required=True, type=int)
    args = p.parse_args(rest)
    doc = _load_state(args)
    timing = doc.setdefault("stage_summaries", {}).setdefault(args.stage, {}).setdefault("timing", {})
    end = _now()
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
    common.emit({"stage": args.stage, "timing": timing}, args.format)
    return 0


def _stage_summary(rest):
    """Write a compact summary of a stage by deriving completed/escalated/design_issue
    task lists from the live task_states for that stage's tasks. The context-hygiene
    anchor: at stage close the controller writes this and stops re-reading the stage's
    per-task detail."""
    p = common.base_parser("pipeline stage-summary")
    p.add_argument("--stage", required=True, type=int)
    args = p.parse_args(rest)
    doc = _load_state(args)
    defs = (doc.get("stages") or {}).get("definitions") or []
    if args.stage < 1 or args.stage > len(defs):
        common.die(f"stage {args.stage} out of range 1..{len(defs)}")
    stage_ids = defs[args.stage - 1]
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
    ss = doc.setdefault("stage_summaries", {}).setdefault(args.stage, {})
    ss.update(summary)
    _save_state(args, doc)
    common.emit({"stage": args.stage, **summary}, args.format)
    return 0


# ── Sprint lifecycle ─────────────────────────────────────────────────────────


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
    _write_yaml(history_path, {"history": prior_entries + spill})

    doc["history"] = keep
    doc.setdefault("history", []).append({
        "ts": _now(), "event": "history_archived", "spilled_count": spill_count,
        "archive_path": str(history_path),
    })
    _save_state(args, doc)
    return 0


# ── The close-time drain ─────────────────────────────────────────────────────
#
# The merge record (which tasks completed, what they cover/serve) is the drain signal
# for the working state: complete-sprint trims shipped ids from the design backlog,
# drains learnings whose last serving design just emptied, sweeps the slice's
# superseded SYS-TC tags, and appends a drain report for wf-sa's proof gate
# (capabilities never drain here — that takes the adequacy judgment).

_BACKLOG_BULLET_RE = re.compile(r"^- \*\*((?:REQ|SYS-TC)-\d+)\*\*")
_BACKLOG_LABEL_RE = re.compile(r"^\*\*(Component requirements|System test cases|Supersedes):\*\*")
_DRIVER_ID_RE = re.compile(r"\b(?:CAP|L)-\d+\b")
_ID_LANES = ("Component requirements", "System test cases")


def _completed_tasks(sprint_doc, task_states):
    tasks = [t for t in (sprint_doc.get("tasks") or []) if isinstance(t, dict) and t.get("id")]
    return [t for t in tasks
            if _effective_status(t["id"], t.get("status"), task_states) in _COMPLETED]


def _shipped_ids(completed):
    """(req+systc id set, {systc id: {description, covers}}) from the merged tasks."""
    ids, scenarios = set(), {}
    for t in completed:
        cov = t.get("covers")
        ids.update([cov] if isinstance(cov, str) else (cov or []))
        for st in t.get("system_tests") or []:
            if isinstance(st, dict) and st.get("id"):
                ids.add(st["id"])
                scenarios[st["id"]] = {"description": st.get("description", ""),
                                       "covers": list(st.get("covers") or [])}
    return ids, scenarios


def _split_backlog(text):
    """(preamble_lines, blocks) — a block starts at a `## ` heading and carries its
    header line, body lines, and the driver ids of its `— serves` header."""
    preamble, blocks, cur = [], [], None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = {"header": line, "lines": [], "serves": _DRIVER_ID_RE.findall(line)}
            blocks.append(cur)
        elif cur is None:
            preamble.append(line)
        else:
            cur["lines"].append(line)
    return preamble, blocks


def _trim_block(block, shipped):
    """Remove shipped id bullets (bullet line + indented continuations) from the block's
    requirement/system-test lanes. Returns (kept_lines, trimmed_ids, remaining_ids) —
    Supersedes bullets are neither trimmed nor counted as remaining work."""
    kept, trimmed, remaining = [], [], []
    label, skipping = None, False
    for line in block["lines"]:
        m = _BACKLOG_LABEL_RE.match(line)
        if m:
            label, skipping = m.group(1), False
            kept.append(line)
            continue
        b = _BACKLOG_BULLET_RE.match(line)
        if b and label in _ID_LANES:
            if b.group(1) in shipped:
                trimmed.append(b.group(1))
                skipping = True
                continue
            remaining.append(b.group(1))
            skipping = False
        elif skipping and (line.startswith((" ", "\t"))):
            continue  # a trimmed bullet's wrapped continuation goes with it
        else:
            skipping = False
        kept.append(line)
    return kept, trimmed, remaining


def _block_title(block):
    return block["header"].lstrip("# ").split("— serves")[0].strip()


def _trim_backlog(path, shipped):
    """Trim shipped ids; drop emptied blocks. Returns the drain facts."""
    preamble, blocks = _split_backlog(path.read_text())
    out_lines = list(preamble)
    emptied, partial, emptied_serves = [], [], []
    for block in blocks:
        kept, trimmed, remaining = _trim_block(block, shipped)
        if trimmed and not remaining:
            emptied.append(_block_title(block))
            emptied_serves.extend(block["serves"])
            continue  # the whole block drains
        if trimmed:
            partial.append({"design": _block_title(block),
                            "shipped": sorted(trimmed), "remaining": sorted(remaining)})
        out_lines.append(block["header"])
        out_lines.extend(kept)
    path.write_text("\n".join(out_lines) + "\n")
    surviving = "\n".join(out_lines)
    return emptied, partial, emptied_serves, surviving


def _drain_learnings(path, drained_ids):
    doc = common.load_yaml(path)
    entries = doc.get("learnings") or []
    doc["learnings"] = [e for e in entries
                        if not (isinstance(e, dict) and e.get("id") in drained_ids)]
    _write_yaml(path, doc)


def _superseded_sys_tc_survivors(slice_text, args):
    """The slice's superseded SYS-TC ids still tagged in the test tree — the build was
    required to remove them; a survivor is a finding for the next slice."""
    m = re.search(r"^##\s+Supersedes\s*$(.*?)(?=^##\s|\Z)", slice_text,
                  re.MULTILINE | re.DOTALL)
    ids = re.findall(r"^- \*\*(SYS-TC-\d+)\*\*", m.group(1), re.MULTILINE) if m else []
    if not ids:
        return []
    cfg_tests = (common.config_doc(args.config).get("paths") or {}).get("tests")
    roots = [cfg_tests] if isinstance(cfg_tests, str) else list(cfg_tests or [])
    root_dir = common.project_root(args.config)
    dirs = [d for d in (root_dir / r for r in roots) if d.is_dir()]
    if not dirs:
        return []
    harvested = harvest(dirs, DEFAULT_TEST_GLOBS)
    return [{"id": rid, "files": sorted(set(harvested[rid]["files"]))}
            for rid in ids if rid in harvested]


def _drain_working_set(args, doc, sprint_doc, paths):
    """Run the close-time drain; returns the drain summary (empty when no merge
    record exists — nothing shipped, nothing to drain)."""
    if not sprint_doc:
        return {}
    completed = _completed_tasks(sprint_doc, doc.get("task_states") or {})
    shipped, scenarios = _shipped_ids(completed)

    survivors = []
    if paths.get("design_slice"):
        sp = common.resolve_path(args.config, "design_slice", None)
        if sp.exists():
            survivors = _superseded_sys_tc_survivors(sp.read_text(), args)

    emptied, partial, emptied_serves, surviving = [], [], [], ""
    if paths.get("design_backlog"):
        bp = common.resolve_path(args.config, "design_backlog", None)
        if bp.exists() and shipped:
            emptied, partial, emptied_serves, surviving = _trim_backlog(bp, shipped)

    def unserved(prefix):
        seen, out = set(), []
        for did in emptied_serves:
            if did.startswith(prefix) and did not in seen:
                seen.add(did)
                if not re.search(rf"\b{re.escape(did)}\b", surviving):
                    out.append(did)
        return out

    drained_learnings = unserved("L-")
    if drained_learnings and paths.get("learnings"):
        lp = common.resolve_path(args.config, "learnings", None)
        if lp.exists():
            _drain_learnings(lp, set(drained_learnings))
        else:
            drained_learnings = []

    candidates = [{"capability": cid,
                   "shipped_scenarios": [{"id": sid, **info}
                                         for sid, info in sorted(scenarios.items())
                                         if cid in info["covers"]]}
                  for cid in unserved("CAP-")]

    return {
        "emptied_designs": emptied,
        "partially_shipped": partial,
        "proof_gate_candidates": candidates,
        "learnings_drained": drained_learnings,
        "superseded_survivors": survivors,
    }


def _append_drain_report(args, paths, sprint_id, drain):
    if not paths.get("drain_report") or not any(drain.values()):
        return None
    rp = common.resolve_path(args.config, "drain_report", None)
    doc = common.load_yaml(rp, optional=True)
    doc.setdefault("reports", []).append({"sprint_id": sprint_id, "closed_at": _now(), **drain})
    _write_yaml(rp, doc)
    return str(rp)


def _complete_sprint(rest):
    """Close the sprint and reset the pipeline for the next one. Run during ship, before
    the push, so its archive snapshots and working-set trims commit into the PR.

    The drain: from the merge record (completed tasks' covers/system_tests/serves),
    trim shipped ids from paths.design_backlog (dropping emptied design blocks), drain
    learnings whose last serving design emptied, sweep the slice's superseded SYS-TC
    ids for surviving tags, and append the drain report (paths.drain_report) that hands
    wf-sa its capability proof-gate candidates. Capabilities are NOT drained here —
    only the adequacy judgment drains a capability.

    When paths.archive is set, snapshot the working set into paths.archive/<sprint_id>/:
    backlog + learnings + final run state are copied (backlog/learnings pre-trim), and
    the sprint, design-slice, design-issues, and spec-decisions files are moved out
    (drained). The archive is a write-only maintainer sink; no role reads it. Then reset
    pipeline_state to a bare ``idle``. When paths.archive is unset the working-set slots
    are simply cleared — the drain itself runs either way.

    Git is intentionally NOT touched — a pure file/state mutation."""
    args = common.base_parser("pipeline complete-sprint").parse_args(rest)

    doc = _load_state(args)
    sprint_path = common.resolve_path(args.config, "sprint", None)
    sprint_doc = common.load_yaml(sprint_path, optional=True)
    sprint_id = sprint_doc.get("sprint_id") or doc.get("sprint_id") or "sprint"

    paths = common.config_doc(args.config).get("paths") or {}
    archived = {}
    root = common.resolve_path(args.config, "archive", None) if paths.get("archive") else None

    # Pre-trim snapshots: the archive keeps the backlog/learnings as the sprint left them.
    if root:
        for key in ("design_backlog", "learnings"):
            if paths.get(key):
                p = common.resolve_path(args.config, key, None)
                if p.exists():
                    archived[key] = str(archive.snapshot(root, sprint_id, p, move=False))

    drain = _drain_working_set(args, doc, sprint_doc, paths)
    report_path = _append_drain_report(args, paths, sprint_id, drain)

    if root:
        # sprint + slice + design-issues + spec-decisions drain out of the working set;
        # the final run state is a snapshot.
        if sprint_path.exists():
            archived["sprint"] = str(archive.snapshot(root, sprint_id, sprint_path, move=True))
        if paths.get("design_slice"):
            sp = common.resolve_path(args.config, "design_slice", None)
            if sp.exists():
                archived["slice"] = str(archive.snapshot(root, sprint_id, sp, move=True))
        # The host design-issues file is per-sprint working state (the fix agents' prose
        # record). Its run-state twin (pipeline_state.design_issues) resets with the state
        # below; drain the file too, or resolved DIs accumulate across sprints.
        if paths.get("design_issues"):
            dip = common.resolve_path(args.config, "design_issues", None)
            if dip.exists():
                archived["design_issues"] = str(archive.snapshot(root, sprint_id, dip, move=True))
        # The spec-fix decision report is per-sprint working state — folded into the PR body
        # at ship, then drained here so it does not carry into the next sprint's report.
        if paths.get("spec_decisions"):
            sdp = common.resolve_path(args.config, "spec_decisions", None)
            if sdp.exists():
                archived["spec_decisions"] = str(archive.snapshot(root, sprint_id, sdp, move=True))
        if _state_path(args).exists():
            archived["pipeline_state"] = str(archive.snapshot(root, sprint_id, _state_path(args), move=False))
    else:
        # no archive configured — clear the active working-set slots
        if sprint_path.exists():
            sprint_path.unlink()
        for key in ("design_issues", "spec_decisions"):
            if paths.get(key):
                p = common.resolve_path(args.config, key, None)
                if p.exists():
                    p.unlink()

    _save_state(args, {"current_phase": "idle"})
    common.emit({
        "sprint_id": sprint_id,
        "archived": archived,
        "drain": drain,
        "drain_report": report_path,
        "pipeline_state_reset": str(_state_path(args)),
    }, args.format)
    return 0


COMMANDS = {
    # stage computation + frontier
    ("pipeline", "compute-stages"): _compute_stages,
    ("pipeline", "next"): _next,
    # reads
    ("pipeline", "current-phase"): _current_phase,
    ("pipeline", "task-state"): _task_state,
    ("pipeline", "unresolved-design-issues"): _unresolved_design_issues,
    ("pipeline", "blocked-tasks"): _blocked_tasks,
    ("pipeline", "attempt-counter"): _attempt_counter,
    ("pipeline", "history-tail"): _history_tail,
    # run-state mutations
    ("pipeline", "transition"): _transition,
    ("pipeline", "dispatch"): _dispatch,
    ("pipeline", "complete-task"): _complete_task,
    ("pipeline", "approve-task"): _approve_task,
    ("pipeline", "reject-task"): _reject_task,
    ("pipeline", "block-task"): _block_task,
    ("pipeline", "reclaim-stale"): _reclaim_stale,
    ("pipeline", "record-design-issue"): _record_design_issue,
    ("pipeline", "resolve-design-issue"): _resolve_design_issue,
    # staged-model mutations
    ("pipeline", "advance-stage"): _advance_stage,
    ("pipeline", "propagate-blocks"): _propagate_blocks,
    ("pipeline", "stage-start"): _stage_start,
    ("pipeline", "stage-end"): _stage_end,
    ("pipeline", "stage-summary"): _stage_summary,
    # sprint lifecycle
    ("pipeline", "archive-history"): _archive_history,
    ("pipeline", "complete-sprint"): _complete_sprint,
}
