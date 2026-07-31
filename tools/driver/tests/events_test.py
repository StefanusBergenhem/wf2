#!/usr/bin/env python3
"""Tests for the driver's telemetry rows — dispatch/routing/stop events appended to
paths.telemetry as JSONL, tagged so they never mix with session or usage rows.
Run: python3 tools/driver/tests/events_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import json
import unittest

import support  # noqa: F401

import config as driver_config
import events as driver_events


class TelemetryTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        self.tele = driver_events.Telemetry(self.cfg)

    def rows(self):
        path = self.cfg.path("telemetry")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_event_row_carries_the_driver_kind_and_a_timestamp(self):
        self.tele.event("dispatch", role="wf-build", task_id="T1", exit_code=0,
                        duration_s=12)
        (row,) = self.rows()
        self.assertEqual(row["kind"], "driver_event")
        self.assertEqual(row["event"], "dispatch")
        self.assertEqual(row["role"], "wf-build")
        self.assertEqual(row["task_id"], "T1")
        self.assertEqual(row["exit_code"], 0)
        self.assertEqual(row["duration_s"], 12)
        self.assertTrue(row["ts"].endswith("Z"))

    def test_rows_append_and_the_sink_dir_is_created(self):
        self.tele.event("sprint_start", sprint_id="s1")
        self.tele.event("stop", reason="manual_stop")
        self.assertEqual([r["event"] for r in self.rows()], ["sprint_start", "stop"])

    def test_none_valued_fields_are_dropped(self):
        self.tele.event("phase", role=None, mode=None, increment=2)
        (row,) = self.rows()
        self.assertNotIn("role", row)
        self.assertEqual(row["increment"], 2)

    def test_dry_run_writes_nothing(self):
        tele = driver_events.Telemetry(self.cfg, dry_run=True)
        tele.event("dispatch", role="wf-designer")
        self.assertEqual(self.rows(), [])

    def test_a_broken_sink_never_raises(self):
        # telemetry is observability, not correctness: a sink that cannot be written
        # (here: a directory sitting where the file belongs) must not stop the loop.
        path = self.cfg.path("telemetry")
        path.mkdir(parents=True)
        self.tele.event("dispatch", role="wf-build")


if __name__ == "__main__":
    unittest.main()
