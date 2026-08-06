#!/usr/bin/env python3
"""Tests for the wf-CLI wrapper — the driver's only source of verdicts.
Run: python3 tools/driver/tests/cliverbs_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import io
import unittest

import support  # noqa: F401

import cliverbs
import config as driver_config
import progress


class CliverbsTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        self.cli = cliverbs.Cli(self.cfg)

    def test_read_parses_json_and_passes_the_config(self):
        (self.root / ".wf/transient").mkdir(parents=True, exist_ok=True)
        (self.root / ".wf/transient/pipeline-state.yaml").write_text(
            "current_phase: designing\n")
        res = self.cli.read("pipeline", "current-phase")
        self.assertEqual(res.rc, 0)
        self.assertEqual(res.data["phase"], "designing")

    def test_a_failing_verb_returns_its_exit_code_without_raising(self):
        res = self.cli.read("pipeline", "increments")  # no sprint file on disk
        self.assertNotEqual(res.rc, 0)
        self.assertEqual(res.data, {})

    def test_mutate_runs_the_verb(self):
        self.cli.mutate("pipeline", "transition", "--to", "designing")
        state = (self.root / ".wf/transient/pipeline-state.yaml").read_text()
        self.assertIn("current_phase: designing", state)

    def test_dry_run_skips_mutations_but_still_reads(self):
        cli = cliverbs.Cli(self.cfg, dry_run=True)
        res = cli.mutate("pipeline", "transition", "--to", "designing")
        self.assertEqual(res.rc, 0)
        self.assertFalse((self.root / ".wf/transient/pipeline-state.yaml").exists())
        self.assertEqual(len(cli.planned), 1)

    def test_command_line_is_reported_for_diagnostics(self):
        res = self.cli.read("pipeline", "current-phase")
        self.assertIn("pipeline", res.argv)
        self.assertIn("--format", res.argv)

    def test_a_verbose_reporter_prints_every_verb_and_its_exit_code(self):
        out = io.StringIO()
        cli = cliverbs.Cli(self.cfg, report=progress.Reporter(
            out=out, heartbeat_s=None, verbose=True))
        cli.read("pipeline", "current-phase")
        self.assertIn("wf pipeline current-phase", out.getvalue())
        self.assertIn("rc=", out.getvalue())

    def test_verbs_are_silent_without_verbose(self):
        out = io.StringIO()
        cli = cliverbs.Cli(self.cfg, report=progress.Reporter(out=out,
                                                             heartbeat_s=None))
        cli.read("pipeline", "current-phase")
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
