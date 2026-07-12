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


def _stats(vals):
    return {"avg": round(sum(vals) / len(vals)), "max": max(vals)} if vals else {"avg": 0, "max": 0}


def _roles(rest):
    """Per-role context-footprint report, derived on demand from the telemetry rows —
    nothing is stored. Each `SubagentStop` usage row is one wf-role subagent's own
    transcript; it is window-joined to the skill row (`agent` + window) it overlaps
    most. `footprint = input + cache_creation` ≈ the unique context that run loaded
    (within one subagent run the context only grows, so this ≈ its peak). The main
    loop (`wf-orchestrate`) is not a subagent — its `Stop` rows are cumulative
    snapshots of the whole session transcript, so its final total per session is
    reported separately under `main_loop`."""
    p = common.base_parser("telemetry roles")
    p.add_argument("--sink", default=None,
                   help="explicit telemetry path; overrides paths.telemetry")
    args = p.parse_args(rest)
    sink = Path(args.sink) if args.sink else common.resolve_path(args.config, "telemetry", None)

    skill, sub, stop = [], [], []
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
            elif "agent" in r:
                skill.append(r)

    # Subagent candidates: skill rows for non-orchestrate roles that overlap some usage
    # row (the hook-era rows). Each carries a parsed window.
    cands = [dict(s, _a=_parse(s.get("started_at")), _b=_parse(s.get("ended_at")))
             for s in skill if s.get("agent") != "wf-orchestrate"]

    used, joined = set(), []
    for u in sub:
        ua, ub = _parse(u.get("started_at")), _parse(u.get("ended_at"))
        scored = sorted(((_iou(ua, ub, c["_a"], c["_b"]), i) for i, c in enumerate(cands)),
                        reverse=True)
        pick = next((i for sc, i in scored if i not in used and sc > 0), None)
        if pick is None:
            continue
        used.add(pick)
        t = u.get("tokens") or {}
        joined.append((cands[pick]["agent"], {
            "footprint": (t.get("input") or 0) + (t.get("cache_creation") or 0),
            "cache_read": t.get("cache_read") or 0,
            "output": t.get("output") or 0,
            "tool_calls": u.get("tool_calls") or 0,
            "duration_s": int((ub - ua).total_seconds()) if ua and ub else 0,
        }))

    by_role = {}
    for role, m in joined:
        by_role.setdefault(role, []).append(m)
    roles = []
    for role, runs in by_role.items():
        roles.append({
            "role": role,
            "runs": len(runs),
            "footprint_avg": _stats([r["footprint"] for r in runs])["avg"],
            "footprint_max": _stats([r["footprint"] for r in runs])["max"],
            "output_avg": _stats([r["output"] for r in runs])["avg"],
            "output_max": _stats([r["output"] for r in runs])["max"],
            "cache_read_avg": _stats([r["cache_read"] for r in runs])["avg"],
            "tool_calls_avg": _stats([r["tool_calls"] for r in runs])["avg"],
            "tool_calls_max": _stats([r["tool_calls"] for r in runs])["max"],
            "duration_s_avg": _stats([r["duration_s"] for r in runs])["avg"],
        })
    roles.sort(key=lambda r: r["footprint_max"], reverse=True)  # most concerning first

    # Main loop: per session_id, the largest cumulative Stop snapshot is the final total.
    main = {}
    for u in stop:
        t = u.get("tokens") or {}
        total = sum(t.get(k) or 0 for k in ("input", "output", "cache_read", "cache_creation"))
        sid = u.get("session_id")
        if sid not in main or total > main[sid][0]:
            main[sid] = (total, {
                "session": sid,
                "footprint": (t.get("input") or 0) + (t.get("cache_creation") or 0),
                "cache_read": t.get("cache_read") or 0,
                "output": t.get("output") or 0,
                "tool_calls": u.get("tool_calls") or 0,
            })
    main_loop = [v[1] for v in sorted(main.values(), key=lambda x: x[0], reverse=True)]

    common.emit({
        "roles": roles,
        "main_loop": main_loop,
        "matched": len(joined),
        "usage_rows": len(sub) + len(stop),
    }, args.format)
    return 0


COMMANDS = {
    ("telemetry", "record-session"): _record_session,
    ("telemetry", "roles"): _roles,
}
