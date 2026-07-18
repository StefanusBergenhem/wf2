#!/usr/bin/env python3
"""claude_usage_hook.py — Claude Code Stop/SubagentStop telemetry enrichment.

Reads the hook payload JSON on stdin, parses the session transcript (JSONL),
and appends ONE `kind: usage` record — token sums and tool-call count — to the
telemetry sink resolved from `.wf/config.yaml` `paths.telemetry`. Registered at
install time (claude target only); joined to the skill-written session rows
offline by session_id / time window. Stdlib only; runs on the target's system
python3.

A telemetry hook must never break the session: ANY failure (bad payload,
missing/malformed transcript, unreadable config) writes nothing, prints
nothing, and exits 0.
"""
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


def _scan_transcript(path):
    """Sum assistant usage tokens (deduped by message id — one API message is
    often split across transcript lines with its usage repeated) and count
    tool_use blocks (deduped by block id). Malformed lines are skipped.
    Returns (tokens, tool_calls, started_at, ended_at), or None when the
    transcript carries no assistant usage at all."""
    usage_by_msg, anon_usages = {}, []
    tool_ids, anon_tools = set(), 0
    first = last = None  # (datetime, raw string) over every timestamped line

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

            ts = entry.get("timestamp")
            if isinstance(ts, str):
                dt = _parse_iso(ts)
                if dt is not None:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if first is None or dt < first[0]:
                        first = (dt, ts)
                    if last is None or dt > last[0]:
                        last = (dt, ts)

            message = entry.get("message")
            if entry.get("type") != "assistant" or not isinstance(message, dict):
                continue

            usage = message.get("usage")
            if isinstance(usage, dict):
                msg_id = message.get("id")
                if msg_id:
                    usage_by_msg[msg_id] = usage
                else:
                    anon_usages.append(usage)

            content = message.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        block_id = block.get("id")
                        if block_id:
                            tool_ids.add(block_id)
                        else:
                            anon_tools += 1

    usages = list(usage_by_msg.values()) + anon_usages
    if not usages:
        return None

    def val(u, field):
        v = u.get(field) or 0
        return v if isinstance(v, int) else 0

    def total(field):
        return sum(val(u, field) for u in usages)

    tokens = {
        "input": total("input_tokens"),
        "output": total("output_tokens"),
        "cache_read": total("cache_read_input_tokens"),
        "cache_creation": total("cache_creation_input_tokens"),
    }
    # context_max: the largest single-request context (input + cache_read +
    # cache_creation) — the honest per-request peak. Summed cache_creation
    # double-counts whenever the prompt cache expires mid-session and the whole
    # context is re-written, so it measures churn, not load; this doesn't.
    context_max = max(
        val(u, "input_tokens") + val(u, "cache_read_input_tokens")
        + val(u, "cache_creation_input_tokens")
        for u in usages
    )
    stats = {"requests": len(usages), "context_max": context_max}
    return tokens, len(tool_ids) + anon_tools, first and first[1], last and last[1], stats


def _run():
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        return
    cwd = payload.get("cwd") or os.getcwd()

    config = os.path.join(cwd, CONFIG)
    if _cfg_value(config, "telemetry", "enabled") == "false":
        return
    sink_path = _cfg_value(config, "paths", "telemetry")
    if not sink_path:
        return

    transcript = payload.get("agent_transcript_path") or payload.get("transcript_path")
    if not transcript or not os.path.isfile(transcript):
        return
    scanned = _scan_transcript(transcript)
    if scanned is None:
        return
    tokens, tool_calls, started_at, ended_at, stats = scanned

    record = {
        "kind": "usage",
        "session_id": payload.get("session_id"),
        "hook_event": payload.get("hook_event_name"),
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "started_at": started_at,
        "ended_at": ended_at,
        "tokens": tokens,
        "tool_calls": tool_calls,
        "requests": stats["requests"],
        "context_max": stats["context_max"],
    }

    sink = resolve_sink(sink_path, cwd=cwd)
    parent = os.path.dirname(sink)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(sink, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def main():
    try:
        _run()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
