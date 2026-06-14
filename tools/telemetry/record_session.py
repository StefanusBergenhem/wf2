#!/usr/bin/env python3
"""record_session.py — append one session record to the telemetry sink (JSONL).

Telemetry is observability, not correctness: one JSON line per wf session. The
caller (a skill, per wf-basics) resolves the sink from config `paths.telemetry`
and passes it as --sink. Stdlib only; runs on the target's system python3.

Usage:
  record_session.py --agent <name> --started-at <iso> --ended-at <iso> \
                    --outcome <completed|halted|escalated> [--notes <text>] \
                    --sink <path>
"""
import argparse
import json
import os
import sys
from datetime import datetime


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
    p.add_argument("--notes", default="")
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
        "notes": args.notes,
    }

    parent = os.path.dirname(args.sink)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(args.sink)
    return 0


if __name__ == "__main__":
    sys.exit(main())
