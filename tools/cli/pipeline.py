"""wf pipeline — the orchestration decision brain.

The orchestrator (the thin skill OR the standalone Python driver) holds no
scheduling logic of its own: it asks this module what to do next. The model is
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

import common

# Effective task status → bucket. Live state (pipeline_state.task_states[id].status)
# wins; otherwise the sprint-authored status; otherwise "pending".
_COMPLETED = {"completed", "done"}          # done = sprint.yaml's authored terminal-OK
_OCCUPIES_SLOT = {"building", "reviewing", "dispatching"}  # active; counts against the cap
_PARKED = {"design_issue"}                  # hit a design issue; resolved at end_of_stage
_ESCALATED = {"escalated"}                  # terminal failure — dependents are doomed
_BLOCKED = {"blocked"}                      # a dependency is doomed; can never run


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
        "mode": "build",
        "component": task.get("component"),
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
    pending = [t for t in stage_ids if status(t) not in (
        _COMPLETED | _OCCUPIES_SLOT | _PARKED | _ESCALATED | _BLOCKED)]
    pending.sort()

    slots = max(0, cap - len(in_flight))
    dispatch = [
        _dispatch_entry(tasks_by_id[t], worktree_base, sprint_id) for t in pending[:slots]
    ]
    ready = pending[slots:]

    # The stage is settled (leave running_stage) once nothing is pending or running.
    # Parked design-issue / escalated / blocked tasks do NOT hold the stage open —
    # end_of_stage resolves design issues and propagates blocks before merging.
    stage_done = not (dispatch or ready or in_flight)
    sprint_done = stage_done and cur >= total

    out = {
        "stage": {"index": cur, "total": total, "tasks": stage_ids},
        "dispatch": dispatch,
        "ready": ready,
        "in_flight": in_flight,
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
            "scope_amendment_count": ts.get("scope_amendment_count", 0),
            "pass_index": ts.get("pass_index", 0),
            "branch": ts.get("branch", ""),
            "worktree_path": ts.get("worktree_path", ""),
            "build_commit": ts.get("build_commit"),
        },
        args.format,
    )
    return 0


COMMANDS = {
    ("pipeline", "compute-stages"): _compute_stages,
    ("pipeline", "next"): _next,
    ("pipeline", "current-phase"): _current_phase,
    ("pipeline", "task-state"): _task_state,
}
