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
from pathlib import Path

import archive
import common

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


def _next(rest):
    """Return the current stage's dispatch frontier + settlement signals.

    ``dispatch`` is the set of pending current-stage tasks to start now (capped to
    free slots); ``ready`` is the overflow that will dispatch as slots free.
    ``terminal.stage_done`` is true when nothing in the stage is still pending or
    in-flight (the controller then runs end_of_stage); ``terminal.sprint_done`` adds
    "and this is the last stage". ``terminal.halt`` is set only when something is
    structurally wrong (stages not computed, or an out-of-range current)."""
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

    in_flight = [t for t in stage_ids if status(t) in _OCCUPIES_SLOT]
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
            "sprint_branch": doc.get("sprint_branch") or None,
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


# Agent role → task state. Build is `building`; the design-issue fixers get their
# own states; every other dispatch is a review pass (build → review → security-review
# → …), all of which occupy a slot as `reviewing`.
_DISPATCH_STATE = {
    "wf-build": "building", "build": "building",
    "wf-swa": "fix_swa", "wf-sa": "fix_sa",
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
    p.add_argument("--fix_kind", required=True)
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


def _complete_sprint(rest):
    """Close the sprint and reset the pipeline for the next one. Run during ship, before
    the push, so its archive snapshots commit into the PR. When paths.archive is set,
    snapshot the sprint's working set into
    paths.archive/<sprint_id>/ as it drains — the sprint, the design-slice, and the
    host design-issues file are moved out (drained), the design-backlog and final run
    state are copied (reconcile drains the backlog on its own). The archive is a
    write-only maintainer sink; no role reads
    it. Then reset pipeline_state to a bare ``idle`` so the next run starts clean instead
    of overlaying the shipped sprint's all-completed task states. When paths.archive is
    unset, the sprint slot is simply cleared (no archive).

    Git is intentionally NOT touched — a pure file/state mutation."""
    args = common.base_parser("pipeline complete-sprint").parse_args(rest)

    doc = _load_state(args)
    sprint_path = common.resolve_path(args.config, "sprint", None)
    sprint_doc = common.load_yaml(sprint_path, optional=True)
    sprint_id = sprint_doc.get("sprint_id") or doc.get("sprint_id") or "sprint"

    paths = common.config_doc(args.config).get("paths") or {}
    archived = {}
    if paths.get("archive"):
        root = common.resolve_path(args.config, "archive", None)
        # sprint + slice + design-issues drain out of the working set; backlog +
        # run-state are snapshots.
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
        if paths.get("design_backlog"):
            bp = common.resolve_path(args.config, "design_backlog", None)
            if bp.exists():
                archived["backlog"] = str(archive.snapshot(root, sprint_id, bp, move=False))
        if _state_path(args).exists():
            archived["pipeline_state"] = str(archive.snapshot(root, sprint_id, _state_path(args), move=False))
    else:
        # no archive configured — clear the active working-set slots
        if sprint_path.exists():
            sprint_path.unlink()
        if paths.get("design_issues"):
            dip = common.resolve_path(args.config, "design_issues", None)
            if dip.exists():
                dip.unlink()

    _save_state(args, {"current_phase": "idle"})
    common.emit({
        "sprint_id": sprint_id,
        "archived": archived,
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
