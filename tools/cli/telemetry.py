"""wf telemetry — the session-record verb.

Resolves the sink from config (``paths.telemetry``, via common) and delegates the
record-writing to ``tools/telemetry/record_session.py`` — the same script skills
invoke directly; this verb exists so the drivers can record through the one CLI.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import common


def _recorder():
    """Load record_session.py by file path — tools/cli/../telemetry/ holds in both
    the wf2 source tree and the installed .wf/tools tree."""
    path = Path(__file__).resolve().parent.parent / "telemetry" / "record_session.py"
    spec = importlib.util.spec_from_file_location("wf_record_session", path)
    if spec is None or spec.loader is None:
        common.die(f"record_session.py not found at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _record_session(rest):
    p = common.base_parser("telemetry record-session")
    p.add_argument("--agent", required=True)
    p.add_argument("--started-at", required=True, dest="started_at")
    p.add_argument("--ended-at", required=True, dest="ended_at")
    p.add_argument("--outcome", required=True)
    p.add_argument("--wf-friction", dest="wf_friction", default="")
    p.add_argument("--friction-kind", dest="friction_kind", default="none")
    p.add_argument("--repo-observation", dest="repo_observation", default="")
    p.add_argument("--gotcha", default="")
    p.add_argument("--sink", default=None,
                   help="explicit sink path; overrides paths.telemetry")
    args = p.parse_args(rest)

    sink = args.sink or str(common.resolve_path(args.config, "telemetry", None))
    return _recorder().main([
        "--agent", args.agent,
        "--started-at", args.started_at,
        "--ended-at", args.ended_at,
        "--outcome", args.outcome,
        "--wf-friction", args.wf_friction,
        "--friction-kind", args.friction_kind,
        "--repo-observation", args.repo_observation,
        "--gotcha", args.gotcha,
        "--sink", sink,
    ])


def _parse(ts):
    """Parse an ISO-8601 stamp (trailing Z or offset) to a datetime, or None."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iou(ua, ub, sa, sb):
    """Intersection-over-union of two [start, end] windows, in [0, 1]. IoU (not raw
    overlap) so a wide window that merely contains a narrow one does not out-score the
    narrow window's own same-sized match."""
    if not (ua and ub and sa and sb):
        return -1.0
    inter = max(0.0, (min(ub, sb) - max(ua, sa)).total_seconds())
    union = (max(ub, sb) - min(ua, sa)).total_seconds()
    return inter / union if union > 0 else 0.0


def _driver_role(row):
    """The role a `kind: driver_event` row names — the driver writes the role name under
    both `agent` and `role`. A row naming neither is a phase event (sprint_start, stop,
    ship), not a dispatch, and joins nothing."""
    return row.get("agent") or row.get("role")


def _contains(cand, ua, ub):
    """True when the candidate's window brackets the whole usage window."""
    return bool(cand["_a"] and cand["_b"] and cand["_a"] <= ua and cand["_b"] >= ub)


def _exact_pick(ua, ub, cands, used, only=None):
    """The index of the tightest candidate window CONTAINING [ua, ub] — the exact join.
    The driver brackets every dispatch it launches, so a transcript that falls inside a
    dispatch belongs to that role; tightest wins when parallel dispatches overlap.
    ``only`` restricts the search to a subset of candidate indices."""
    if not (ua and ub):
        return None
    fits = [(c["_b"] - c["_a"], i) for i, c in enumerate(cands)
            if i not in used and (only is None or i in only) and _contains(c, ua, ub)]
    return min(fits)[1] if fits else None


def _stats(vals):
    return {"avg": round(sum(vals) / len(vals)), "max": max(vals)} if vals else {"avg": 0, "max": 0}


def _roles(rest):
    """Per-role context-footprint report, derived on demand from the telemetry rows —
    nothing is stored. Each `SubagentStop` usage row is one wf-role subagent's own
    transcript, joined to the row that names the role that ran it: a driver dispatch row
    (`kind: driver_event`) or a session record. A dispatch brackets the run, so a
    transcript falling inside one joins it exactly; only a window nothing contains falls
    back to the best time-overlap match. Two load metrics, deliberately separate:
    `context_max` is the largest single-request context — the honest "how much did this
    role hold at once" peak — while `footprint = input + cache_creation` sums every
    cache write, so it inflates whenever a slow turn expires the prompt-cache TTL and
    the context is re-written (churn, not load). Diagnose "loaded too much" on
    context_max, "cache churned" on a footprint far above it. Pre-upgrade rows carry
    no context_max and read as 0.
    Cost comes off the dispatch row, where the driver puts what the harness reported
    when the launch closed — reported only over the runs that carry one (`costed_runs`),
    since a refused dispatch wrote no result line and totalling it as 0 would read as a
    run that was free.
    A main-loop session is not a subagent — its `Stop` rows are cumulative snapshots
    of the whole session transcript, so its final total per session is reported
    separately under `main_loop`."""
    p = common.base_parser("telemetry roles")
    p.add_argument("--sink", default=None,
                   help="explicit telemetry path; overrides paths.telemetry")
    args = p.parse_args(rest)
    sink = Path(args.sink) if args.sink else common.resolve_path(args.config, "telemetry", None)

    skill, driver, sub, stop = [], [], [], []
    if sink.exists():
        for line in sink.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("kind") == "usage":
                (sub if r.get("hook_event") == "SubagentStop" else stop).append(r)
            elif r.get("kind") == "driver_event":
                if _driver_role(r):
                    driver.append(r)
            elif "agent" in r:
                skill.append(r)

    # Join candidates: every row naming the role that ran a subagent — the driver's
    # dispatch rows and the roles' own session records. Each carries a parsed window.
    def _candidate(row, role):
        return dict(row, agent=role,
                    _a=_parse(row.get("started_at")), _b=_parse(row.get("ended_at")))

    # A main-loop session row spans the whole run, so it would contain (and swallow)
    # every subagent's window; it is reported under main_loop from its `Stop` rows.
    cands = [_candidate(s, s.get("agent")) for s in skill
             if s.get("agent") != "wf-orchestrate"]
    dispatched = set(range(len(cands), len(cands) + len(driver)))
    cands += [_candidate(d, _driver_role(d)) for d in driver]

    def _metrics(u, ua, ub, cand=None):
        t = u.get("tokens") or {}
        return {
            "footprint": (t.get("input") or 0) + (t.get("cache_creation") or 0),
            "context_max": u.get("context_max") or 0,
            "requests": u.get("requests") or 0,
            "cache_read": t.get("cache_read") or 0,
            "output": t.get("output") or 0,
            "tool_calls": u.get("tool_calls") or 0,
            "duration_s": int((ub - ua).total_seconds()) if ua and ub else 0,
            # What the harness charged for this dispatch, off the row that launched it.
            # None, not 0: a refused launch wrote no result line, and a zero there would
            # total up as a run that was free.
            "cost_usd": (cand or {}).get("cost_usd"),
            "task": (cand or {}).get("task"),
        }

    used, joined = set(), []
    for u in sub:
        ua, ub = _parse(u.get("started_at")), _parse(u.get("ended_at"))
        pick = _exact_pick(ua, ub, cands, used)
        if pick is None:
            scored = sorted(((_iou(ua, ub, c["_a"], c["_b"]), i)
                             for i, c in enumerate(cands)), reverse=True)
            pick = next((i for sc, i in scored if i not in used and sc > 0), None)
        if pick is None:
            continue
        used.add(pick)
        joined.append((cands[pick]["agent"], _metrics(u, ua, ub, cands[pick])))

    # A role the driver dispatches runs as its OWN top-level session, so its hook fires
    # `Stop`, not `SubagentStop`. A Stop row a dispatch window brackets is that role's
    # transcript. Containment only, and only against dispatch windows: the main loop's
    # own Stop rows are cumulative snapshots spanning the whole session, so no dispatch
    # can contain one, and an overlap fallback would let one claim a role it merely ran.
    unclaimed = []
    for u in stop:
        ua, ub = _parse(u.get("started_at")), _parse(u.get("ended_at"))
        pick = _exact_pick(ua, ub, cands, used, only=dispatched)
        if pick is None:
            unclaimed.append(u)
            continue
        used.add(pick)
        joined.append((cands[pick]["agent"], _metrics(u, ua, ub, cands[pick])))

    by_role = {}
    for role, m in joined:
        by_role.setdefault(role, []).append(m)
    roles = []
    for role, runs in by_role.items():
        # Cost is reported only over the runs that actually carry one. A dispatch the
        # harness refused wrote no result line, so summing it as 0 would understate the
        # role and read as a run that was free — `costed_runs` says how much of `runs`
        # the total covers.
        costs = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
        roles.append({
            "role": role,
            "runs": len(runs),
            "cost_usd": round(sum(costs), 2),
            "cost_usd_max": round(max(costs), 2) if costs else 0,
            "costed_runs": len(costs),
            "context_max_avg": _stats([r["context_max"] for r in runs])["avg"],
            "context_max_max": _stats([r["context_max"] for r in runs])["max"],
            "footprint_avg": _stats([r["footprint"] for r in runs])["avg"],
            "footprint_max": _stats([r["footprint"] for r in runs])["max"],
            "requests_avg": _stats([r["requests"] for r in runs])["avg"],
            "output_avg": _stats([r["output"] for r in runs])["avg"],
            "output_max": _stats([r["output"] for r in runs])["max"],
            "cache_read_avg": _stats([r["cache_read"] for r in runs])["avg"],
            "tool_calls_avg": _stats([r["tool_calls"] for r in runs])["avg"],
            "tool_calls_max": _stats([r["tool_calls"] for r in runs])["max"],
            "duration_s_avg": _stats([r["duration_s"] for r in runs])["avg"],
        })
    # most concerning first: the honest peak leads; footprint breaks the tie for
    # pre-upgrade rows that carry no context_max
    roles.sort(key=lambda r: (r["context_max_max"], r["footprint_max"]), reverse=True)

    # Main loop: per session_id, the largest cumulative Stop snapshot is the final total.
    main = {}
    for u in unclaimed:
        t = u.get("tokens") or {}
        total = sum(t.get(k) or 0 for k in ("input", "output", "cache_read", "cache_creation"))
        sid = u.get("session_id")
        if sid not in main or total > main[sid][0]:
            main[sid] = (total, {
                "session": sid,
                "footprint": (t.get("input") or 0) + (t.get("cache_creation") or 0),
                "context_max": u.get("context_max") or 0,
                "requests": u.get("requests") or 0,
                "cache_read": t.get("cache_read") or 0,
                "output": t.get("output") or 0,
                "tool_calls": u.get("tool_calls") or 0,
            })
    main_loop = [v[1] for v in sorted(main.values(), key=lambda x: x[0], reverse=True)]

    # Per task, so stage width can be judged on what a task cost to build rather than on
    # how many tasks the stage held. Context comes from the transcripts that joined; the
    # attempt count and cost come from the dispatch rows, which exist either way — a
    # dispatch whose transcript never joined still ran and still cost.
    tasks = {}

    def _task_row(name):
        return tasks.setdefault(name, {"task": name, "roles": [], "dispatches": 0,
                                       "context_max": 0, "cost_usd": 0.0})

    for _role, m in joined:
        if m.get("task"):
            row = _task_row(m["task"])
            row["context_max"] = max(row["context_max"], m["context_max"])
    for d in driver:
        if not d.get("task"):
            continue
        row = _task_row(d["task"])
        row["dispatches"] += 1
        if isinstance(d.get("cost_usd"), (int, float)):
            row["cost_usd"] += d["cost_usd"]
        role = _driver_role(d)
        if role and role not in row["roles"]:
            row["roles"].append(role)
    for row in tasks.values():
        row["cost_usd"] = round(row["cost_usd"], 2)
    # Hottest first: the task that came closest to running out of window leads.
    task_rows = sorted(tasks.values(),
                       key=lambda r: (r["context_max"], r["cost_usd"]), reverse=True)

    # Every dispatch the harness costed, not only the ones a usage row joined — a role
    # whose transcript never matched still spent money, and the run total must say so.
    run_cost = sum(d["cost_usd"] for d in driver
                   if isinstance(d.get("cost_usd"), (int, float)))
    common.emit({
        "roles": roles,
        "tasks": task_rows,
        "main_loop": main_loop,
        "matched": len(joined),
        "usage_rows": len(sub) + len(stop),
        "cost_usd": round(run_cost, 2),
        "costed_dispatches": sum(1 for d in driver if d.get("cost_usd") is not None),
    }, args.format)
    return 0


COMMANDS = {
    ("telemetry", "record-session"): _record_session,
    ("telemetry", "roles"): _roles,
}
