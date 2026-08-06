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
import state as driver_state

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

    def parked_frontier(self):
        """The frontier while T1 sits parked on a design issue: the sub-layer has work
        left, but the only task in it is `repairing` and nothing is dispatchable."""
        return {"increment": 1, "stage": {"index": 1, "total": 1, "tasks": ["T1"]},
                "dispatch": [], "ready": [], "in_flight": [], "approved": [],
                "repairing": ["T1"], "escalated": [], "blocked": [],
                "terminal": {"stage_done": True, "increment_done": False,
                             "sprint_done": False, "halt": None}}

    def parked_until_the_twin_is_resolved(self):
        """`pipeline next`, answering the way the real pipeline does: T1 stays parked
        until the run state's twin of the design issue is resolved, and only then is it
        dispatchable, approved and merged. Resolving the HOST entry alone changes
        nothing here — the scheduler reads the run state."""
        live = iter(self.full_cli().responses[("pipeline", "next")])

        def answer(cli, args):
            if not any(c[:2] == ["pipeline", "resolve-design-issue"] for c in cli.calls):
                return self.parked_frontier()
            return next(live, self.full_cli().responses[("pipeline", "next")][-1])
        return answer

    def rt(self, cli=None, git=None, agents=None, state=None):
        return fakes.runtime(self.cfg, cli=cli or self.full_cli(),
                             git=git or fakes.FakeGit(), agents=agents, state=state,
                             telemetry=__import__("events").Telemetry(self.cfg))

    def rows(self):
        path = self.cfg.path("telemetry")
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] \
            if path.exists() else []

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

    def test_a_restart_reclaims_the_slots_the_dead_run_was_holding(self):
        """The hygiene lives inside sprint_start, which a mid-sprint resume never passes
        through — so an interruption in the increment loop (a session limit, a ^C) came
        back with its tasks still holding slots nobody would ever release."""
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        self.write_slice()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "closeout"
        rt.state.save()
        loop.run_loop(rt, once=True)
        verbs = rt.cli.verbs()
        self.assertIn("pipeline reclaim-stale", verbs)
        self.assertIn("orchestrate sweep-transients", verbs)

    def test_a_restart_onto_a_branch_git_lacks_halts_before_any_dispatch(self):
        agents = self.agents_that_design()
        git = fakes.FakeGit(absent=["sprint/s1"])
        rt = self.rt(agents=agents, git=git)
        self.write_slice()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "increment_loop"
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 1)
        self.assertEqual(rt.state.stop_reason, "sprint_branch_missing")
        self.assertEqual(agents.roles(), [])

    def test_a_red_slice_gate_on_resume_halts_carrying_its_findings(self):
        """The halt named the slice and nothing else, so the operator had to re-run the
        gate by hand to learn what was wrong — and in the case that produced this test
        the slice was not at fault at all (the ADR scan was walking the live worktrees).
        A halt that cannot be acted on is the loop going dark at the one moment it
        mattered."""
        agents = self.agents_that_design()
        cli = self.full_cli()
        cli.responses[("slice", "check")] = (1, {
            "verdict": "fail",
            "errors": [{"code": "A5", "msg": "slice: ADR-023 is defined in more than "
                                             "one ADR set"}]})
        rt = self.rt(cli=cli, agents=agents)
        self.write_slice()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "increment_loop"
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 1)
        self.assertEqual(rt.state.stop_reason, "slice_check_red")
        detail = [r for r in self.rows() if r["event"] == "halt"][-1]["detail"]
        self.assertIn("A5", detail)
        self.assertIn("more than one ADR set", detail)

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

    def test_an_escalation_from_repair_resumes_into_the_increment_loop(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_slice())      # originate
        agents.on("wf-tl", fakes.raise_design_issue(self.cfg))

        def escalate(*a):
            self.cfg.path("decision_prep").write_text(
                "# Decision prep\nmode: repair\n\n## Ruling\n<!-- pending -->\n")
        agents.on("wf-designer", escalate)                            # repair → halt
        rt = self.rt(agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(rt.state.stop_reason, "escalation")
        self.assertEqual(rt.state.phase, "awaiting_ruling")
        self.assertEqual(rt.state.resume_phase, "increment_loop")
        # the brief is the human's inbox — nothing may consume it before the ruling
        self.assertTrue(self.cfg.path("decision_prep").exists())

        # the human rules; the restart resumes rather than re-designing
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\nmode: repair\n\n## Ruling\nDI-9: amend the contract.\n")
        agents2 = fakes.FakeAgents(self.cfg)
        resolve = fakes.resolve_issues(self.cfg)

        def consume(*a):
            self.cfg.path("decision_prep").unlink()
            resolve(*a)
        agents2.on("wf-designer", consume)
        rt2 = self.rt(agents=agents2, state=driver_state.load(self.cfg))
        self.assertEqual(loop.run_loop(rt2, once=True), 0)
        self.assertEqual(agents2.launches[0]["role"], "wf-designer")
        self.assertEqual(agents2.launches[0]["mode"], "resume")
        self.assertEqual(agents2.launches[0]["params"]["Mode"], "resume")
        self.assertNotIn("originate", [x["mode"] for x in agents2.launches])
        self.assertIn("wf-tl", agents2.roles())      # back in the increment loop

    def test_a_task_scoped_escalation_resumes_and_the_parked_task_merges(self):
        # The build raises a design issue: the run-state twin parks T1, the repair run
        # escalates, the loop pauses. The resume run closes only the HOST entry — unless
        # the driver closes the twin too, T1 stays parked, the sub-layer never settles,
        # and the sprint burns its iteration budget.
        wt = self.root / ".wf/transient/worktrees/s1-T1/.wf/transient"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "design-issues.yaml").write_text(
            'issues:\n  - id: "DI-1"\n    task_id: "T1"\n    severity: high\n'
            '    status: open\n    summary: "the contract contradicts the increment"\n')

        agents = self.agents_that_design()

        def escalate(*a):
            self.cfg.path("decision_prep").write_text(
                "# Decision prep\nmode: repair\ndi_id: DI-1\n\n"
                "## Ruling\n<!-- pending -->\n")
        agents.on("wf-designer", escalate)              # the repair run escalates
        cli = self.full_cli()
        cli.responses[("orchestrate", "inspect-build-return")] = {
            "task_id": "T1", "verdict": "design_issue", "di_id": "DI-1"}
        cli.responses[("pipeline", "next")] = [
            self.full_cli().responses[("pipeline", "next")][0], self.parked_frontier()]
        rt = self.rt(cli=cli, agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(rt.state.stop_reason, "escalation")
        self.assertEqual(rt.state.resume_phase, "increment_loop")

        # the human rules; the restart resumes and the run must reach the merge
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\nmode: repair\ndi_id: DI-1\n\n"
            "## Ruling\nDI-1: amend the contract.\n")
        agents2 = fakes.FakeAgents(self.cfg)
        resolve = fakes.resolve_issues(self.cfg)

        def consume(*a):
            self.cfg.path("decision_prep").unlink()
            resolve(*a)                                 # only the HOST entry
        agents2.on("wf-designer", consume)
        cli2 = self.full_cli()
        cli2.responses[("pipeline", "next")] = self.parked_until_the_twin_is_resolved()
        cli2.responses[("pipeline", "unresolved-design-issues")] = {
            "count": 1, "issues": [{"di_id": "DI-1", "task_id": "T1", "status": "open"}]}
        git = fakes.FakeGit()
        rt2 = self.rt(cli=cli2, git=git, agents=agents2,
                      state=driver_state.load(self.cfg))
        self.assertEqual(loop.run_loop(rt2, once=True), 0)
        self.assertIn(["pipeline", "resolve-design-issue", "DI-1"], cli2.calls)
        self.assertEqual(git.merges, ["task/s1-T1"])

    # ── the remote ───────────────────────────────────────────────────────────

    def test_the_base_is_fetched_before_the_stack_is_derived(self):
        git = fakes.FakeGit()
        rt = self.rt(git=git, agents=self.agents_that_design())
        loop.run_loop(rt, once=True)
        self.assertEqual(git.fetched, ["main"])

    def test_an_unreachable_origin_warns_and_the_loop_carries_on(self):
        git = fakes.FakeGit(fetch_ok=False)
        agents = self.agents_that_design()
        rt = self.rt(git=git, agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertIn("wf-designer", agents.roles())
        warn = [r for r in self.rows() if r["event"] == "warn"]
        self.assertEqual(warn[0]["reason"], "fetch_failed")

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

    def test_dry_run_leaves_no_state_file_behind(self):
        # A dry run that persists state poisons the next REAL run: it resumes into a
        # phase whose sprint branch was never actually cut.
        import events
        cfg = driver_config.load(str(support.write_config(self.root)))
        rt = fakes.runtime(cfg, telemetry=events.Telemetry(cfg, dry_run=True))
        rt.dry_run = True
        rt.agents.dry_run = True
        rt.state.dry_run = True
        loop.run_loop(rt, once=True)
        self.assertFalse(cfg.state_file.exists())


if __name__ == "__main__":
    unittest.main()
