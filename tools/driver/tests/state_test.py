#!/usr/bin/env python3
"""Tests for the driver's on-disk state — the file a restarted driver reconstructs
its position from. Run: python3 tools/driver/tests/state_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import state as driver_state


class StateTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))

    def test_absent_file_reads_as_a_fresh_sprint_start(self):
        st = driver_state.load(self.cfg)
        self.assertEqual(st.phase, "sprint_start")
        self.assertIsNone(st.sprint_id)
        self.assertIsNone(st.stop_reason)

    def test_round_trip_through_disk(self):
        st = driver_state.load(self.cfg)
        st.phase = "stage_run"
        st.sprint_id = "s7"
        st.sprint_branch = "sprint/s7"
        st.stage = 12
        st.stages_shipped = 2
        st.save()
        again = driver_state.load(self.cfg)
        self.assertEqual(again.phase, "stage_run")
        self.assertEqual(again.sprint_id, "s7")
        self.assertEqual(again.sprint_branch, "sprint/s7")
        self.assertEqual(again.stage, 12)
        self.assertEqual(again.stages_shipped, 2)

    def test_save_writes_the_configured_path_and_stamps_it(self):
        st = driver_state.load(self.cfg)
        st.phase = "closeout"
        st.save()
        self.assertTrue(self.cfg.state_file.exists())
        self.assertIn("updated_at", self.cfg.state_file.read_text())

    def test_identical_save_does_not_rewrite_the_file(self):
        st = driver_state.load(self.cfg)
        st.phase = "designing"
        st.save()
        before = self.cfg.state_file.stat().st_mtime_ns
        raw = self.cfg.state_file.read_text()
        st.save()
        self.assertEqual(raw, self.cfg.state_file.read_text())
        self.assertEqual(before, self.cfg.state_file.stat().st_mtime_ns)

    def test_start_sprint_resets_the_per_sprint_fields(self):
        # stages_shipped counts what the PR in flight carries; carrying it into a new
        # sprint would ship the next PR early, on stages that are already merged.
        st = driver_state.load(self.cfg)
        st.phase = "closeout"
        st.stage = 12
        st.stages_shipped = 4
        st.stop_reason = "manual_stop"
        st.start_sprint("s9", "sprint/s9")
        self.assertEqual(st.phase, "sprint_start")
        self.assertEqual(st.sprint_id, "s9")
        self.assertIsNone(st.stage)
        self.assertEqual(st.stages_shipped, 0)
        self.assertIsNone(st.stop_reason)

    def test_an_unknown_phase_is_refused(self):
        st = driver_state.load(self.cfg)
        with self.assertRaises(ValueError):
            st.enter("increment_loop")

    def test_malformed_state_file_does_not_crash_the_load(self):
        self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.state_file.write_text("not: [a, valid\n")
        st = driver_state.load(self.cfg)
        self.assertEqual(st.phase, "sprint_start")


if __name__ == "__main__":
    unittest.main()
