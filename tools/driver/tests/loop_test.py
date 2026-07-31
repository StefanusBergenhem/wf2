#!/usr/bin/env python3
"""Tests for the continuous loop — sprint after sprint until a stop rule fires,
resuming from the state file, and the --once / --dry-run bring-up modes.
Run: python3 tools/driver/tests/loop_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import json
import unittest

import support  # noqa: F401

import config as driver_config
import fakes
import loop

CAPS = 'version: 1\ncapabilities:\n  - id: "CAP-001"\n    statement: "s"\n'
SLICE_OK = {"verdict": "pass", "serves": [], "increments": [{"n": 1, "title": "one"}],
            "errors": []}


class LoopTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-discover", "wf-build", "wf-review", "wf-adequacy",
                     "wf-retrospective", "wf-stage-repair"):
            (self.root / f".claude/agents/{role}.md").write_text("x\n")
        for role in ("wf-designer", "wf-tl"):
            (self.root / f".claude/skills/{role}").mkdir(parents=True)
            (self.root / f".claude/skills/{role}/SKILL.md").write_text("x\n")
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)
        self.cfg.path("capabilities").write_text(CAPS)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")

    def full_cli(self):
        return fakes.FakeCli({
            ("slice", "check"): SLICE_OK,
            ("pipeline", "increments"): [
                (2, {}), {"increments": [{"increment": 1, "tasks": 1, "done": False}]}],
            ("sprint", "materialize"): {"verdict": "pass"},
            ("sprint", "check"): {"verdict": "pass"},
            ("pipeline", "compute-stages"): {"stages": [["T1"]], "total": 1},
            ("pipeline", "next"): [
                {"increment": 1, "stage": {"index": 1, "total": 1, "tasks": ["T1"]},
                 "dispatch": [{"task_id": "T1",
                               "worktree": ".wf/transient/worktrees/s1-T1"}],
                 "ready": [], "in_flight": [], "approved": [], "repairing": [],
                 "escalated": [], "blocked": [],
                 "terminal": {"stage_done": False, "increment_done": False,
                              "sprint_done": False, "halt": None}},
                {"increment": 1, "stage": {"index": 1, "total": 1, "tasks": ["T1"]},
                 "dispatch": [], "ready": [], "in_flight": [], "approved": ["T1"],
                 "repairing": [], "escalated": [], "blocked": [],
                 "terminal": {"stage_done": True, "increment_done": True,
                              "sprint_done": True, "halt": None}},
            ],
            ("pipeline", "task-state"): {"attempt_counter": 0, "build_commit": "b1"},
            ("orchestrate", "inspect-build-return"): {
                "task_id": "T1", "verdict": "ready_for_review",
                "build_commit_sha": "b1"},
            ("orchestrate", "inspect-review-return"): {"task_id": "T1",
                                                       "verdict": "approved"},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })

    def rt(self, cli=None, git=None, agents=None):
        return fakes.runtime(self.cfg, cli=cli or self.full_cli(),
                             git=git or fakes.FakeGit(), agents=agents,
                             telemetry=__import__("events").Telemetry(self.cfg))

    def write_slice(self, *a):
        self.cfg.path("design_slice").write_text(
            "# Design-slice — one\n**Serves:** CAP-001\n\n## Increments\n\n"
            "### Increment 1 — one\nCheckpoint: it works.\n")

    def agents_that_design(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_slice())
        return agents

    # ── one sprint ───────────────────────────────────────────────────────────

    def test_once_runs_a_single_sprint_end_to_end(self):
        agents = self.agents_that_design()
        git = fakes.FakeGit()
        rt = self.rt(git=git, agents=agents)
        rc = loop.run_loop(rt, once=True)
        self.assertEqual(rc, 0)
        self.assertEqual(agents.roles(),
                         ["wf-discover", "wf-designer", "wf-tl", "wf-build",
                          "wf-review", "wf-retrospective", "wf-adequacy"])
        self.assertEqual(git.pushed, ["sprint/s1"])
        self.assertEqual(rt.state.phase, "sprint_start")
        self.assertIsNone(rt.state.sprint_id)

    def test_the_loop_keeps_going_until_a_stop_rule_fires(self):
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        rc = loop.run_loop(rt, max_sprints=2)
        self.assertEqual(rc, 0)
        self.assertEqual(agents.roles().count("wf-designer"), 2)

    # ── stop rules ───────────────────────────────────────────────────────────

    def test_a_stop_file_ends_the_loop_before_a_new_sprint(self):
        self.cfg.stop_file.write_text("")
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        self.assertEqual(loop.run_loop(rt), 0)
        self.assertEqual(agents.roles(), [])
        self.assertEqual(rt.state.stop_reason, "manual_stop")
        rows = [json.loads(x) for x in
                self.cfg.path("telemetry").read_text().splitlines()]
        self.assertEqual(rows[-1]["event"], "stop")
        self.assertEqual(rows[-1]["reason"], "manual_stop")
        self.assertEqual(rows[-1]["kind"], "driver_event")

    def test_an_empty_work_set_exits_cleanly(self):
        self.cfg.path("capabilities").write_text("version: 1\ncapabilities: []\n")
        rt = self.rt()
        self.assertEqual(loop.run_loop(rt), 0)
        self.assertEqual(rt.state.stop_reason, "work_exhaustion")

    def test_a_stop_pending_from_an_increment_boundary_ends_the_loop_after_ship(self):
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        rt.state.stop_pending = None

        def stop_at_boundary(*a):
            self.cfg.stop_file.write_text("")
        agents.on("wf-tl", stop_at_boundary)
        rc = loop.run_loop(rt, max_sprints=5)
        self.assertEqual(rc, 0)
        self.assertEqual(agents.roles().count("wf-designer"), 1)
        self.assertEqual(rt.state.stop_reason, "manual_stop")

    def test_a_halt_returns_non_zero_and_keeps_the_position(self):
        git = fakes.FakeGit(clean=False)
        rt = self.rt(git=git)
        self.assertEqual(loop.run_loop(rt, once=True), 1)
        self.assertEqual(rt.state.stop_reason, "dirty_tree")

    # ── resume ───────────────────────────────────────────────────────────────

    def test_a_restart_resumes_the_phase_the_state_file_names(self):
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        self.write_slice()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "closeout"
        rt.state.save()
        loop.run_loop(rt, once=True)
        self.assertNotIn("wf-designer", agents.roles())
        self.assertIn("wf-retrospective", agents.roles())

    def test_awaiting_ruling_without_a_ruling_stays_paused(self):
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\n\n## Ruling\n<!-- empty -->\n")
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "awaiting_ruling"
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(rt.state.stop_reason, "escalation")
        self.assertEqual(agents.roles(), [])

    def test_a_recorded_ruling_resumes_the_design_role(self):
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(agents=agents)
        self.write_slice()
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\nmode: originate\n\n## Ruling\nD-1: take option B.\n")

        def consume(*a):
            self.cfg.path("decision_prep").unlink()
        agents.on("wf-designer", consume)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "awaiting_ruling"
        rt.state.save()
        loop.run_loop(rt, once=True)
        self.assertEqual(agents.launches[0]["role"], "wf-designer")
        self.assertEqual(agents.launches[0]["mode"], "resume")
        self.assertIn("wf-tl", agents.roles())

    # ── dry run ──────────────────────────────────────────────────────────────

    def test_dry_run_prints_the_planned_dispatches_and_changes_nothing(self):
        import events
        cfg = driver_config.load(str(support.write_config(self.root)))
        rt = fakes.runtime(cfg, telemetry=events.Telemetry(cfg, dry_run=True))
        rt.dry_run = True
        rt.agents.dry_run = True
        rc = loop.run_loop(rt, once=True)
        self.assertEqual(rc, 0)
        self.assertEqual([x["role"] for x in rt.agents.launches],
                         ["wf-discover", "wf-designer"])
        self.assertFalse(self.cfg.path("design_slice").exists())


if __name__ == "__main__":
    unittest.main()
