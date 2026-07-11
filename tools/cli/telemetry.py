"""wf telemetry — the session-record verb.

Resolves the sink from config (``paths.telemetry``, via common) and delegates the
record-writing to ``tools/telemetry/record_session.py`` — the same script skills
invoke directly; this verb exists so the drivers can record through the one CLI.
"""
from __future__ import annotations

import importlib.util
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


COMMANDS = {
    ("telemetry", "record-session"): _record_session,
}
