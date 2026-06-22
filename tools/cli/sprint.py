"""wf sprint — read-side helpers over the sprint task DAG (sprint.yaml).

The orchestrator extracts ONE task's contract into its worktree before dispatching
build, so the build agent reads a focused contract (current_task) rather than the
whole sprint. ``sprint task <id>`` is that extractor.
"""
from __future__ import annotations

from pathlib import Path

import common


def _task(rest):
    """Emit (or --write) the contract for a single task from sprint.yaml. --write is an
    explicit path ARGUMENT used as-given (the orchestrator passes the worktree-resolved
    current_task path), never re-anchored on the host config."""
    p = common.base_parser("sprint task")
    p.add_argument("task_id")
    p.add_argument("--write", help="write the contract to this path instead of stdout")
    args = p.parse_args(rest)

    sprint = common.load_yaml(common.resolve_path(args.config, "sprint", None))
    tasks = sprint.get("tasks") or []
    entry = next(
        (t for t in tasks if isinstance(t, dict) and t.get("id") == args.task_id), None
    )
    if entry is None:
        common.die(f"task {args.task_id} not found in sprint")

    if args.write:
        import yaml as _yaml

        out_path = Path(args.write)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            _yaml.safe_dump(entry, sort_keys=False, default_flow_style=False, allow_unicode=True)
        )
        common.emit({"task_id": args.task_id, "written": str(out_path)}, args.format)
    else:
        common.emit(entry, args.format)
    return 0


COMMANDS = {
    ("sprint", "task"): _task,
}
