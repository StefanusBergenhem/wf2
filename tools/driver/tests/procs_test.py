#!/usr/bin/env python3
"""Tests for bounded subprocess execution — the timeout contract, and what a timeout
actually kills.

Run: python3 tools/driver/tests/procs_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import time
import unittest

import support  # noqa: F401

import procs


class RunTest(support.TempProject):
    def test_a_command_that_finishes_reports_its_output_and_span(self):
        done = procs.run(["echo", "hello"], timeout=10)
        self.assertEqual(done.rc, 0)
        self.assertEqual(done.stdout.strip(), "hello")
        self.assertFalse(done.timed_out)
        self.assertLessEqual(done.started_at, done.ended_at)

    def test_a_failing_command_returns_its_code_without_raising(self):
        done = procs.run("exit 3", timeout=10, shell=True)
        self.assertEqual(done.rc, 3)

    def test_a_command_that_cannot_start_returns_127(self):
        done = procs.run(["/nonexistent/binary"], timeout=10)
        self.assertEqual(done.rc, 127)

    def test_stdout_path_streams_both_streams_to_the_file(self):
        log = self.root / "run.log"
        done = procs.run("echo out; echo err >&2", timeout=10, shell=True,
                         stdout_path=log)
        self.assertEqual(done.rc, 0)
        self.assertIn("out", log.read_text())
        self.assertIn("err", log.read_text())

    def test_a_timeout_reports_124_and_the_timed_out_flag(self):
        done = procs.run("sleep 30", timeout=1, shell=True)
        self.assertEqual(done.rc, 124)
        self.assertTrue(done.timed_out)

    def test_a_timeout_kills_the_GRANDchild_too(self):
        """A role is launched through a shell, so the agent is a grandchild. Killing only
        the direct child leaves the agent alive — still writing into the repo the driver
        has already given up on and moved past."""
        marker = self.root / "the-orphan-was-here"
        # the grandchild outlives the bound, then writes; nothing must ever appear
        done = procs.run(f"bash -c 'sleep 1.5; echo x > {marker}' & wait",
                         timeout=0.5, shell=True)
        self.assertTrue(done.timed_out)
        time.sleep(2.5)
        self.assertFalse(marker.exists(),
                         "a killed dispatch left a process still writing to the repo")


if __name__ == "__main__":
    unittest.main()
