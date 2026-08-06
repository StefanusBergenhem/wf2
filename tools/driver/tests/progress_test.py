#!/usr/bin/env python3
"""Tests for the progress reporter — the loop's window onto itself.

Run: python3 tools/driver/tests/progress_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import io
import os
import signal
import threading
import time
import unittest

import support  # noqa: F401

import progress


class FakeClock:
    """A monotonic clock the test advances by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def reporter(**kw):
    out = io.StringIO()
    kw.setdefault("heartbeat_s", None)
    return progress.Reporter(out=out, **kw), out


class FormatTest(unittest.TestCase):
    def test_a_duration_reads_in_the_largest_unit_that_fits(self):
        self.assertEqual(progress.duration(9), "9s")
        self.assertEqual(progress.duration(75), "1m15s")
        self.assertEqual(progress.duration(3900), "1h05m")

    def test_the_elapsed_prefix_is_a_fixed_width_clock(self):
        self.assertEqual(progress.clock_str(0), "0:00:00")
        self.assertEqual(progress.clock_str(3725), "1:02:05")


class LineTest(unittest.TestCase):
    def test_every_line_carries_the_prefix_and_the_elapsed_clock(self):
        clock = FakeClock()
        rep, out = reporter(clock=clock)
        clock.advance(127)
        rep.line("sprint s1 on sprint/s1")
        self.assertEqual(out.getvalue(), "[wf-driver 0:02:07] sprint s1 on sprint/s1\n")

    def test_a_symbol_and_indent_place_the_line_in_the_tree(self):
        rep, out = reporter()
        rep.line("wf-build", symbol=progress.RUN, indent=2)
        self.assertIn(f"    {progress.RUN} wf-build", out.getvalue())

    def test_detail_lines_are_verbose_only(self):
        rep, out = reporter()
        rep.detail("wf pipeline next -> rc 0")
        self.assertEqual(out.getvalue(), "")

        loud, loud_out = reporter(verbose=True)
        loud.detail("wf pipeline next -> rc 0")
        self.assertIn("wf pipeline next -> rc 0", loud_out.getvalue())


class StepTest(unittest.TestCase):
    def test_a_step_prints_an_open_and_a_close_line_with_its_duration(self):
        clock = FakeClock()
        rep, out = reporter(clock=clock)
        with rep.step("wf-designer (originate)"):
            clock.advance(185)
        lines = out.getvalue().splitlines()
        self.assertIn(f"{progress.RUN} wf-designer (originate)", lines[0])
        self.assertIn(f"{progress.OK} wf-designer (originate)", lines[1])
        self.assertIn("3m05s", lines[1])

    def test_a_step_can_carry_its_own_outcome_note(self):
        rep, out = reporter()
        with rep.step("wf-build") as step:
            step.note = "rc=1"
            step.ok = False
        closing = out.getvalue().splitlines()[-1]
        self.assertIn(progress.BAD, closing)
        self.assertIn("rc=1", closing)

    def test_a_raising_step_closes_as_failed_and_re_raises(self):
        rep, out = reporter()
        with self.assertRaises(ValueError):
            with rep.step("wf-tl"):
                raise ValueError("boom")
        closing = out.getvalue().splitlines()[-1]
        self.assertIn(progress.BAD, closing)
        self.assertIn("ValueError", closing)

    def test_an_open_step_is_in_flight_until_it_closes(self):
        clock = FakeClock()
        rep, _ = reporter(clock=clock)
        with rep.step("wf-build task T1"):
            clock.advance(90)
            inflight = rep.inflight()
        self.assertEqual([(label, int(secs)) for label, secs in inflight],
                         [("wf-build task T1", 90)])
        self.assertEqual(rep.inflight(), [])

    def test_parallel_steps_are_all_in_flight(self):
        rep, _ = reporter()
        started, release = threading.Barrier(3), threading.Event()

        def work(name):
            with rep.step(name):
                started.wait(5)
                release.wait(5)

        threads = [threading.Thread(target=work, args=(f"task T{i}",)) for i in (1, 2)]
        for t in threads:
            t.start()
        started.wait(5)
        labels = sorted(label for label, _ in rep.inflight())
        release.set()
        for t in threads:
            t.join(5)
        self.assertEqual(labels, ["task T1", "task T2"])
        self.assertEqual(rep.inflight(), [])


class HeartbeatTest(unittest.TestCase):
    def test_a_long_step_reports_that_it_is_still_running(self):
        out = io.StringIO()
        rep = progress.Reporter(out=out, heartbeat_s=0.05)
        with rep.step("wf-build", budget_s=7200):
            deadline = time.monotonic() + 5
            while "still running" not in out.getvalue() and time.monotonic() < deadline:
                time.sleep(0.01)
        beat = [ln for ln in out.getvalue().splitlines() if "still running" in ln]
        self.assertTrue(beat, f"no heartbeat line in:\n{out.getvalue()}")
        self.assertIn("wf-build", beat[0])
        self.assertIn("2h00m", beat[0])  # the budget it will be killed at

    def test_a_closed_step_stops_beating(self):
        out = io.StringIO()
        rep = progress.Reporter(out=out, heartbeat_s=0.05)
        with rep.step("wf-build"):
            time.sleep(0.12)
        quiet = out.getvalue()
        time.sleep(0.2)
        self.assertEqual(out.getvalue(), quiet)


class InterruptTest(unittest.TestCase):
    def test_the_in_flight_report_names_what_was_running_and_for_how_long(self):
        clock = FakeClock()
        rep, out = reporter(clock=clock)
        with rep.step("wf-build task T1"):
            clock.advance(420)
            rep.report_inflight("interrupted")
        text = out.getvalue()
        self.assertIn("interrupted", text)
        self.assertIn("wf-build task T1", text)
        self.assertIn("7m00s", text)

    def test_the_in_flight_report_says_so_when_nothing_was_running(self):
        rep, out = reporter()
        rep.report_inflight("interrupted")
        self.assertIn("nothing was in flight", out.getvalue())

    def test_a_sigint_reports_from_inside_the_open_step_then_interrupts(self):
        """The whole point of the handler: by the time an except-block sees the
        KeyboardInterrupt the step has already closed, so the snapshot must be taken
        while the signal is being handled."""
        rep, out = reporter()
        previous = signal.getsignal(signal.SIGINT)
        self.addCleanup(signal.signal, signal.SIGINT, previous)
        progress.install_interrupt_report(rep)
        with self.assertRaises(KeyboardInterrupt):
            with rep.step("wf-build task T1"):
                os.kill(os.getpid(), signal.SIGINT)
                time.sleep(0.5)  # give the signal a bytecode boundary to land on
        self.assertIn("interrupted — in flight:", out.getvalue())
        self.assertIn("wf-build task T1", out.getvalue())
        self.assertEqual(signal.getsignal(signal.SIGINT), previous,
                         "a second ^C must be the plain one")


if __name__ == "__main__":
    unittest.main()
