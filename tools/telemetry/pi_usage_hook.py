#!/usr/bin/env python3
"""pi_usage_hook.py — pi telemetry enrichment (the pi analog of the claude Stop hook).

The driver launches each role as a headless `pi -p`, which persists its session
(hooks for the launched role + any in-process sub-sessions) to ``--session-dir``.
This script reads those pi session files and appends ``kind: usage`` rows to the
telemetry sink in exactly the shape the claude Stop/SubagentStop hook writes, so
``wf telemetry roles`` computes the same per-role context report for a pi run that
it does for a claude one. Stdlib only; runs on the target's system python3.

A row is emitted per session file: the top-level session of a dispatch is a ``Stop``
row, and a child session whose header carries ``parentSession`` is a ``SubagentStop``
row. Given ``--session-id`` (the dispatch's uuid, read off its log) the hook emits
only that dispatch's own sessions — the file whose header ``id`` is the uuid, plus any
child that points back at it — so re-running one dispatch never re-emits a neighbour's
rows. Called by the driver after each pi dispatch.

A usage hook must never break the run: ANY failure (bad payload, missing session,
unreadable config) writes nothing and exits 0.
"""
import argparse
import glob
import json
import os
import sys
from datetime import datetime, timezone

# Never litter a target repo's committed .wf/tools with __pycache__.
sys.dont_write_bytecode = True

from record_session import resolve_sink, _parse_iso  # sibling module: shared sink anchoring

CONFIG = os.path.join(".wf", "config.yaml")  # the bootstrap anchor — the only hard-coded path


def _cfg_value(config_path, section, key):
    """Read `<section>: / <key>: <value>` from the flat config — quoted scalar
    values, trailing comments stripped (mirrors scaffold.sh's cfg_path awk)."""
    in_section = False
    with open(config_path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line[:1].isspace():
                in_section = stripped.split(":", 1)[0].strip() == section
                continue
            if in_section and stripped.startswith(key + ":"):
                value = stripped[len(key) + 1:].split("#", 1)[0].strip()
                return value.strip('"').strip("'")
    return None


def _as_iso(ts):
    """Coerce a session/message timestamp to an ISO-8601 string with a zone."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
    return ts


def scan_session(path):
    """Derive a ``kind: usage`` row's numeric fields from one pi session JSONL file.

    Returns ``(header, tokens, tool_calls, requests, context_max, started_at,
    ended_at)`` — ``tokens`` as a dict of {input, output, cache_read, cache_creation}
    summed over the deduped assistant requests (one API message is often repeated
    across the stream's ``message_end`` and ``turn_end`` lines, so requests are keyed
    by the response id). Returns ``None`` when the session carries no assistant usage.
    """
    header = None
    first = last = None          # (ISO string) over every entry timestamp
    usage_by_id, anon = {}, []   # responseId -> usage ; usages with no response id
    tool_ids, anon_tools = set(), 0

    with open(path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue

            if entry.get("type") == "session":
                header = entry
            ts = entry.get("timestamp")
            if isinstance(ts, str):
                iso = _as_iso(ts)
                if first is None or iso < first:
                    first = iso
                if last is None or iso > last:
                    last = iso

            if entry.get("type") != "message":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue

            # Tool calls live on the assistant's content blocks; a tool result message
            # repeats the toolCallId, so self-deduping on the assistant blocks is enough.
            if message.get("role") == "assistant":
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "toolCall":
                        block_id = block.get("id")
                        if block_id:
                            tool_ids.add(block_id)
                        else:
                            anon_tools += 1

            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            rid = message.get("responseId")
            if rid:
                usage_by_id[rid] = usage
            else:
                anon.append(usage)

    if header is None:
        return None
    usages = list(usage_by_id.values()) + anon
    if not usages:
        return None

    def val(u, k):
        v = u.get(k) or 0
        return v if isinstance(v, (int, float)) else 0

    tokens = {
        "input": sum(val(u, "input") for u in usages),
        "output": sum(val(u, "output") for u in usages),
        "cache_read": sum(val(u, "cacheRead") for u in usages),
        "cache_creation": sum(val(u, "cacheWrite") for u in usages),
    }
    context_max = max(
        val(u, "input") + val(u, "cacheRead") + val(u, "cacheWrite")
        for u in usages
    ) if usages else 0
    return (header, tokens, len(tool_ids) + anon_tools, len(usages), context_max,
            first, last or first)


def _emit_row(sink, header, tokens, tool_calls, requests, context_max,
              started_at, ended_at, hook_event):
    record = {
        "kind": "usage",
        "session_id": header.get("id"),
        "hook_event": hook_event,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started_at": _as_iso(started_at),
        "ended_at": _as_iso(ended_at),
        "tokens": tokens,
        "tool_calls": tool_calls,
        "requests": requests,
        "context_max": context_max,
    }
    parent = os.path.dirname(sink)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _run(args):
    sink = args.sink
    if not sink:
        config = args.config or os.path.join(os.getcwd(), CONFIG)
        sink = _cfg_value(config, "paths", "telemetry")
    if not sink:
        return 0

    files = []
    if args.session:
        files = [args.session]
    elif args.session_dir:
        files = sorted(glob.glob(os.path.join(args.session_dir, "*.jsonl")))

    # Scan every file once; a file with no assistant usage is simply not a session.
    scans = {}
    for path in files:
        if not os.path.isfile(path):
            continue
        try:
            scanned = scan_session(path)
        except OSError:
            continue
        if scanned is not None:
            scans[path] = scanned

    top_level_id = args.session_id
    if top_level_id:
        # This dispatch's own session is the file whose header carries the uuid from its
        # log; its children are the files whose parentSession points back at that file —
        # never a neighbour's leftover, and never a child of some other session.
        top_paths = {p for p, (h, *_rest) in scans.items()
                     if h.get("id") == top_level_id}
        top_file = next((p for p in sorted(top_paths)), None)
        # Match a child's parentSession however it references the top file — absolute,
        # relative, or by basename — so a child of THIS dispatch is claimed and a
        # child of a neighbour's session (pointing at a file we don't own) is not.
        top_names = set()
        if top_file:
            top_names.add(os.path.abspath(top_file))
            top_names.add(os.path.normpath(top_file))
            top_names.add(os.path.basename(top_file))
            top_names.add(os.path.basename(os.path.normpath(top_file)).rsplit(".jsonl", 1)[0])

        def _scope(path, h):
            if h.get("id") == top_level_id:
                return "Stop"
            parent = h.get("parentSession")
            if parent and any(str(n) in str(parent) for n in top_names):
                return "SubagentStop"
            return None
    else:
        def _scope(path, h):
            return "SubagentStop" if h.get("parentSession") else "Stop"

    for path, (header, tokens, tool_calls, requests, context_max,
               start_iso, end_iso) in scans.items():
        event = _scope(path, header)
        if event is None:
            continue
        _emit_row(sink, header, tokens, tool_calls, requests, context_max,
                  start_iso, end_iso, event)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pi_usage_hook.py")
    p.add_argument("--session", help="one pi session JSONL file to emit")
    p.add_argument("--session-dir", help="scan every *.jsonl under this dir")
    p.add_argument("--session-id", help="only emit THIS dispatch's sessions (the uuid from its log)")
    p.add_argument("--sink", help="explicit telemetry sink; else read paths.telemetry")
    p.add_argument("--config", help="path to .wf/config.yaml (default: cwd's)")
    args = p.parse_args(argv)
    try:
        return _run(args)
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
