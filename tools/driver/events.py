"""Driver telemetry — one JSONL row per dispatch, routing decision and stop.

Rows land in the same sink as the session and usage rows and carry
``kind: driver_event``, so the retrospective and ``wf telemetry roles`` can join
on role and time without guessing which row is which. Observability, never
correctness: a sink that cannot be written is swallowed.

The row shape is fixed, because the telemetry reader joins on it:
``{kind, agent, role, event, mode, sprint, increment, task, rc, duration_s,
started_at, ended_at, ts}``. ``agent`` mirrors ``role`` — the reader classifies
rows by ``agent``, the same field the session and usage rows carry — and every row
carries an ISO-8601 UTC span, zero-length for an instantaneous event, so a join by
time never has to guess a window.
"""
from __future__ import annotations

import datetime
import json


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Telemetry:
    KIND = "driver_event"

    def __init__(self, cfg, dry_run: bool = False):
        self.path = cfg.path("telemetry")
        self.dry_run = dry_run

    def event(self, event: str, **fields) -> None:
        if self.dry_run:
            return
        stamp = _now()
        row = {"kind": self.KIND, "ts": stamp, "event": event}
        row.update({k: v for k, v in fields.items() if v is not None})
        if row.get("role"):
            row.setdefault("agent", row["role"])
        row.setdefault("started_at", stamp)
        row.setdefault("ended_at", stamp)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
        except OSError:
            return
