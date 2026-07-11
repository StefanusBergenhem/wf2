#!/usr/bin/env python3
"""record_session.py — append one session record to the telemetry sink (JSONL).

Telemetry is observability, not correctness: one JSON line per wf session. The
caller (a skill, per wf-basics) resolves the sink from config `paths.telemetry`
and passes it as --sink. Stdlib only; runs on the target's system python3.

The session record carries a structured `feedback` block (the
continuous-improvement questions, see wf-basics) that the retrospective later
distils into durable lessons. All feedback fields are optional — empty (and
`friction_kind: none`) is the expected value for a clean session. `friction_kind`
is a closed enum classifying the `wf_friction` prose so the retrospective can
cluster mechanically; `gotcha` is a repo setup/env/convention trap destined for
an AGENTS.md proposal, not the learnings streams.

Usage:
  record_session.py --agent <name> --started-at <iso> --ended-at <iso> \
                    --outcome <completed|halted|escalated> \
                    [--wf-friction <text>] [--friction-kind <enum>] \
                    [--repo-observation <text>] [--gotcha <text>] \
                    --sink <path>
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime


def resolve_sink(sink, cwd=None):
    """Anchor a relative sink to the main checkout root — the parent of
    ``git rev-parse --git-common-dir`` — so an append from inside a per-task git
    worktree lands in the main repo's sink instead of dying with the worktree.
    Outside a git repo the sink stays relative to ``cwd`` (the process cwd when
    omitted). Shared with claude_usage_hook.py, which anchors from the hook
    payload's cwd rather than its own."""
    if os.path.isabs(sink):
        return sink
    fallback = sink if cwd is None else os.path.join(cwd, sink)
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=True, cwd=cwd,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return fallback
    if not out:
        return fallback
    common_dir = out if os.path.isabs(out) else os.path.join(cwd or os.getcwd(), out)
    return os.path.join(os.path.dirname(common_dir), sink)


def _parse_iso(s):
    """Parse an ISO-8601 stamp (accepting a trailing Z); None if unparseable."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv=None):
    p = argparse.ArgumentParser(prog="record_session.py")
    p.add_argument("--agent", required=True)
    p.add_argument("--started-at", required=True, dest="started_at")
    p.add_argument("--ended-at", required=True, dest="ended_at")
    p.add_argument("--outcome", required=True,
                   choices=["completed", "halted", "escalated"])
    p.add_argument("--wf-friction", dest="wf_friction", default="",
                   help="concrete wf-instruction friction, or empty (-> wf_learnings)")
    p.add_argument("--friction-kind", dest="friction_kind", default="none",
                   choices=["contract_defect", "skill_gap", "tooling_bug",
                            "env_setup", "none"],
                   help="closed classification of --wf-friction (default: none)")
    p.add_argument("--repo-observation", dest="repo_observation", default="",
                   help="concrete codebase observation, or empty (-> learnings)")
    p.add_argument("--gotcha", default="",
                   help="repo env/setup/convention trap, or empty (-> AGENTS.md proposal)")
    p.add_argument("--sink", required=True)
    args = p.parse_args(argv)

    start, end = _parse_iso(args.started_at), _parse_iso(args.ended_at)
    duration = int((end - start).total_seconds()) if start and end else None

    record = {
        "agent": args.agent,
        "started_at": args.started_at,
        "ended_at": args.ended_at,
        "duration_seconds": duration,
        "outcome": args.outcome,
        "feedback": {
            "wf_friction": args.wf_friction,
            "friction_kind": args.friction_kind,
            "repo_observation": args.repo_observation,
            "gotcha": args.gotcha,
        },
    }

    sink = resolve_sink(args.sink)
    parent = os.path.dirname(sink)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(sink)
    return 0


if __name__ == "__main__":
    sys.exit(main())
