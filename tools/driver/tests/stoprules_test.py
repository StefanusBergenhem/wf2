#!/usr/bin/env python3
"""Tests for the stop rules — escalation, work exhaustion, manual stop, stack depth,
and a request-changes review.
Run: python3 tools/driver/tests/stoprules_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import gitops
import stoprules


CAPS = """\
# CAPABILITIES — comments must survive every edit.
version: 1
capabilities:
  - id: "CAP-001"
    statement: "Users can patch a zone."
    status: planned
  - id: "CAP-002"
    statement: "Users can bulk-patch zones."
    status: parked
"""

LEARNINGS = """\
version: 1
learnings:
  - id: "L-001"
    statement: "Preflight is slow."
"""


class WorkTest(support.TempProject):
    def setUp(self):
        super().setUp()
        support.init_repo(self.root)
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        support.commit_wf(self.root)
        self.git = gitops.Git(self.cfg)

    def write_work(self, caps=CAPS, learnings=LEARNINGS):
        self.cfg.path("capabilities").write_text(caps)
        self.cfg.path("learnings").write_text(learnings)

    def test_open_work_skips_parked_entries(self):
        self.write_work()
        work = stoprules.open_work(self.cfg)
        self.assertEqual(work["capabilities"], ["CAP-001"])
        self.assertEqual(work["learnings"], ["L-001"])

    def test_open_work_skips_proposed_entries(self):
        """A `proposed` capability was minted by the residue exit from a defect residual;
        its words are the reviewer's, not a product owner's. The loop cannot design
        against it until a PO session owns the wording."""
        self.write_work(caps=CAPS + '  - id: "CAP-003"\n'
                                    '    statement: "minted from a residual"\n'
                                    '    status: proposed\n')
        self.assertEqual(stoprules.open_work(self.cfg)["capabilities"], ["CAP-001"])

    def test_only_proposed_left_is_work_exhaustion(self):
        """The right stop, not a spin: it says the loop is out of work a human has agreed
        to, which is exactly what a PO session is for."""
        self.write_work(caps=CAPS.replace("status: planned", "status: proposed"),
                        learnings="version: 1\nlearnings: []\n")
        stop = stoprules.pre_sprint(self.cfg, self.git)
        self.assertEqual(stop.reason, "work_exhaustion")

    def test_all_parked_and_no_learnings_is_work_exhaustion(self):
        self.write_work(caps=CAPS.replace("status: planned", "status: parked"),
                        learnings="version: 1\nlearnings: []\n")
        stop = stoprules.pre_sprint(self.cfg, self.git)
        self.assertEqual(stop.reason, "work_exhaustion")

    def test_open_work_means_no_stop(self):
        self.write_work()
        self.assertIsNone(stoprules.pre_sprint(self.cfg, self.git))

    def test_stop_file_stops_the_loop(self):
        self.write_work()
        self.cfg.stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.stop_file.write_text("")
        self.assertEqual(stoprules.pre_sprint(self.cfg, self.git).reason, "manual_stop")
        self.assertEqual(stoprules.at_boundary(self.cfg, self.git).reason, "manual_stop")

    def test_decision_prep_pauses_for_a_ruling(self):
        self.write_work()
        self.cfg.path("decision_prep").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("decision_prep").write_text("# Decision prep\n")
        self.assertEqual(stoprules.pre_sprint(self.cfg, self.git).reason, "escalation")

    def test_stack_depth_cap_pauses_until_merges(self):
        self.write_work()
        for n in (1, 2, 3):
            base = "main" if n == 1 else f"sprint/s{n - 1}"
            self.git.start_branch(f"sprint/s{n}", base)
            (self.root / f"s{n}.txt").write_text("x")
            support.git(self.root, "add", f"s{n}.txt")
            support.git(self.root, "commit", "-q", "-m", f"s{n}")
        stop = stoprules.pre_sprint(self.cfg, self.git)
        self.assertEqual(stop.reason, "stack_depth")
        self.assertIn("3", stop.detail)

    def test_request_changes_review_is_a_stop_signal(self):
        self.write_work()
        self.git.start_branch("sprint/s1", "main")
        (self.root / "s1.txt").write_text("x")
        support.git(self.root, "add", "s1.txt")
        support.git(self.root, "commit", "-q", "-m", "s1")
        cfg = driver_config.load(str(support.write_config(
            self.root, review_state_cmd="echo CHANGES_REQUESTED {branch}")))
        stop = stoprules.at_boundary(cfg, gitops.Git(cfg))
        self.assertEqual(stop.reason, "review_changes_requested")
        self.assertIn("sprint/s1", stop.detail)

    def test_an_approved_review_is_not_a_stop(self):
        self.write_work()
        self.git.start_branch("sprint/s1", "main")
        (self.root / "s1.txt").write_text("x")
        support.git(self.root, "add", "s1.txt")
        support.git(self.root, "commit", "-q", "-m", "s1")
        cfg = driver_config.load(str(support.write_config(
            self.root, review_state_cmd="echo APPROVED")))
        self.assertIsNone(stoprules.at_boundary(cfg, gitops.Git(cfg)))

    def test_an_unset_review_command_is_simply_skipped(self):
        self.write_work()
        self.assertIsNone(stoprules.review_state(self.cfg, self.git))


if __name__ == "__main__":
    unittest.main()
