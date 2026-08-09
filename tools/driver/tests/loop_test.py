#!/usr/bin/env python3
"""Tests for the continuous loop — cut, build, close, cut again until the PR boundary
falls, then sprint after sprint until a stop rule fires; resuming from the state file,
and the --once / --dry-run bring-up modes.
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
STAGE_OK = {"stage": 7, "verdict": "pass", "serves": ["CAP-001"], "errors": []}


def frontier(*, dispatch=(), approved=(), stage_done=False, tasks=("S7-T1",)):
    return {
        "stage": {"id": 7, "tasks": list(tasks)},
        "dispatch": [{"task_id": t, "worktree": f".wf/transient/worktrees/s1-{t}"}
                     for t in dispatch],
        "ready": [], "in_flight": [], "approved": list(approved), "repairing": [],
        "escalated": [], "blocked": [],
        "terminal": {"stage_done": stage_done, "halt": None},
    }


class LoopTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-discover", "wf-build", "wf-review", "wf-adequacy",
                     "wf-retrospective", "wf-stage-repair"):
            (self.root / f".claude/agents/{role}.md").write_text("x\n")
        (self.root / ".claude/skills/wf-designer").mkdir(parents=True)
        (self.root / ".claude/skills/wf-designer/SKILL.md").write_text("x\n")
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)
        self.cfg.path("capabilities").write_text(CAPS)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")

    # ── the scripted pipeline ────────────────────────────────────────────────

    def frontier_answers(self):
        """`pipeline next`, answering the way the real pipeline does: the loaded stage's
        task is pending until it is dispatched and approved, and gone once it merged.
        Keyed off the calls since the last `load-stage`, so it answers every stage of
        every sprint rather than a one-shot script."""
        def answer(cli, args):
            since = []
            for call in cli.calls:
                if call[:2] == ["pipeline", "load-stage"]:
                    since = []
                since.append(call)
            if any(c[:2] == ["pipeline", "complete-task"] for c in since):
                return frontier(stage_done=True)
            if any(c[:2] == ["pipeline", "approve-task"] for c in since):
                return frontier(approved=["S7-T1"], stage_done=True)
            return frontier(dispatch=["S7-T1"])
        return answer

    def full_cli(self, overrides=None):
        responses = {
            ("stage", "check"): STAGE_OK,
            ("pipeline", "load-stage"): {"stage": 7, "tasks": ["S7-T1"], "count": 1},
            ("pipeline", "next"): self.frontier_answers(),
            ("pipeline", "task-state"): {"attempt_counter": 0, "build_commit": "b1"},
            ("pipeline", "capability-complete"): {"complete": []},
            ("orchestrate", "inspect-build-return"): {
                "task_id": "S7-T1", "verdict": "ready_for_review",
                "build_commit_sha": "b1"},
            ("orchestrate", "inspect-review-return"): {"task_id": "S7-T1",
                                                       "verdict": "approved"},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1",
                                              "drain": {"served": ["CAP-001"]}},
        }
        responses.update(overrides or {})
        return fakes.FakeCli(responses)

    def rt(self, cli=None, git=None, agents=None, state=None):
        return fakes.runtime(self.cfg, cli=cli or self.full_cli(),
                             git=git or fakes.FakeGit(), agents=agents, state=state,
                             telemetry=__import__("events").Telemetry(self.cfg))

    def rows(self):
        path = self.cfg.path("telemetry")
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] \
            if path.exists() else []

    def write_stage(self, *, e2e=True, **kw):
        tasks = [support.task("S7-T1", system_tests=["SYS-TC-1"] if e2e else None)]
        return support.write_stage(self.cfg, tasks=tasks, **kw)

    def agents_that_design(self, e2e=True):
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage(e2e=e2e))
        return agents

    # ── one sprint ───────────────────────────────────────────────────────────

    def test_once_runs_a_single_sprint_end_to_end(self):
        agents = self.agents_that_design()
        git = fakes.FakeGit()
        rt = self.rt(git=git, agents=agents)
        rc = loop.run_loop(rt, once=True)
        self.assertEqual(rc, 0)
        self.assertEqual(agents.roles(),
                         ["wf-discover", "wf-designer", "wf-build", "wf-review",
                          "wf-retrospective"])
        self.assertEqual(git.pushed, ["sprint/s1"])
        self.assertEqual(rt.state.phase, "sprint_start")
        self.assertIsNone(rt.state.sprint_id)

    def test_the_pr_ships_when_a_stage_lands_a_system_test(self):
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        loop.run_loop(rt, once=True)
        self.assertEqual(agents.roles().count("wf-designer"), 1)

    def test_stages_keep_cutting_until_one_lands_a_system_test(self):
        """The PR batches the preceding non-checkpoint stages into it — the designing →
        stage_run → designing cycle is the loop, not a one-shot."""
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage(e2e=False))
        agents.on("wf-designer", lambda *a: self.write_stage(e2e=False))
        agents.on("wf-designer", lambda *a: self.write_stage(e2e=True))
        rt = self.rt(agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(agents.roles().count("wf-designer"), 3)
        self.assertEqual(agents.roles().count("wf-build"), 3)
        self.assertEqual(agents.roles().count("wf-retrospective"), 1)

    def test_the_stage_cap_ships_a_sprint_that_never_lands_one(self):
        cfg = driver_config.load(str(support.write_config(
            self.root, max_stages_per_sprint=2)))
        self.cfg = cfg
        agents = self.agents_that_design(e2e=False)
        rt = self.rt(agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(agents.roles().count("wf-designer"), 2)
        self.assertIn("wf-retrospective", agents.roles())

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
        rows = self.rows()
        self.assertEqual(rows[-1]["event"], "stop")
        self.assertEqual(rows[-1]["reason"], "manual_stop")
        self.assertEqual(rows[-1]["kind"], "driver_event")

    def test_an_empty_work_set_exits_cleanly(self):
        self.cfg.path("capabilities").write_text("version: 1\ncapabilities: []\n")
        rt = self.rt()
        self.assertEqual(loop.run_loop(rt), 0)
        self.assertEqual(rt.state.stop_reason, "work_exhaustion")

    def test_a_cut_that_produces_nothing_ships_what_merged_then_exits(self):
        """Merged, reviewed, green work must not strand on a branch nobody is looking
        at — the PR goes out first, and the loop exits after it. CAP-001 is still open
        here, so the stop names the cut, not a drained backlog."""
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage(e2e=False))
        agents.on("wf-designer", lambda *a: None)        # nothing cut at the next one
        git = fakes.FakeGit()
        rt = self.rt(agents=agents, git=git)
        self.assertEqual(loop.run_loop(rt, max_sprints=5), 0)
        self.assertEqual(rt.state.stop_reason, "no_stage_cut")
        self.assertIn("wf-retrospective", agents.roles())
        self.assertEqual(git.pushed, ["sprint/s1"])

    def test_a_stop_pending_from_a_stage_close_ends_the_loop_after_ship(self):
        agents = self.agents_that_design(e2e=False)
        rt = self.rt(agents=agents)

        def stop_at_the_close(*a):
            self.cfg.stop_file.write_text("")
        agents.on("wf-build", stop_at_the_close)
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
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "closeout"
        rt.state.save()
        loop.run_loop(rt, once=True)
        self.assertNotIn("wf-designer", agents.roles())
        self.assertIn("wf-retrospective", agents.roles())

    def test_a_restart_reclaims_the_slots_the_dead_run_was_holding(self):
        """The hygiene lives inside sprint_start, which a mid-sprint resume never passes
        through — so an interruption inside a stage came back with its tasks still
        holding slots nobody would ever release."""
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
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
        self.write_stage()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "stage_run"
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 1)
        self.assertEqual(rt.state.stop_reason, "sprint_branch_missing")
        self.assertEqual(agents.roles(), [])

    def test_a_restart_after_the_close_archived_the_stage_cuts_the_next_one(self):
        """The close archives and deletes the artifact, so a run interrupted between
        that and the phase write resumes into stage_run with nothing to load. Its work
        is merged; what is missing is the next cut, not a halt."""
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "stage_run"
        rt.state.stages_shipped = 1
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(agents.roles()[0], "wf-designer")
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

    def test_a_run_awaiting_a_ruling_that_was_already_consumed_carries_on(self):
        """The wedge a real run hit: the ruling round consumed the brief and stopped with
        the phase still at awaiting_ruling. Every restart from there found no brief to
        resume, dispatched nothing, and exited 0 — with the whole work-set still open."""
        agents = self.agents_that_design()
        rt = self.rt(agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.suspend("awaiting_ruling")
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(agents.roles().count("wf-designer"), 1)
        self.assertIn("wf-build", agents.roles())
        self.assertEqual(rt.state.stop_reason, "sprint_limit")

    def test_an_escalated_cut_pauses_and_the_ruling_resumes_it(self):
        agents = fakes.FakeAgents(self.cfg)

        def escalate(*a):
            self.cfg.path("decision_prep").write_text(
                "# Decision prep\nresume_at: Phase 5\n\n## Ruling\n<!-- pending -->\n")
        agents.on("wf-designer", escalate)
        rt = self.rt(agents=agents)
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertEqual(rt.state.stop_reason, "escalation")
        self.assertEqual(rt.state.phase, "awaiting_ruling")
        self.assertEqual(rt.state.resume_phase, "designing")
        # the brief is the human's inbox — nothing may consume it before the ruling
        self.assertTrue(self.cfg.path("decision_prep").exists())

        # the human rules; the restart resumes the cut rather than starting a new one
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\nresume_at: Phase 5\n\n## Ruling\nD-1: take option B.\n")
        agents2 = fakes.FakeAgents(self.cfg)

        def consume(*a):
            self.cfg.path("decision_prep").unlink()
            self.write_stage()
        agents2.on("wf-designer", consume)
        rt2 = self.rt(agents=agents2, state=driver_state.load(self.cfg))
        self.assertEqual(loop.run_loop(rt2, once=True), 0)
        self.assertEqual(agents2.launches[0]["role"], "wf-designer")
        self.assertEqual(agents2.launches[0]["mode"], "resume")
        self.assertEqual(agents2.launches[0]["params"]["Mode"], "resume")
        self.assertIn("wf-build", agents2.roles())      # the cut carried on to the build

    def test_a_resume_closes_the_run_state_twin_of_the_briefs_own_issue(self):
        """The design role writes only the host file; the twin is what parks the task
        the issue names, so a twin left open keeps that task out of every frontier."""
        self.cfg.path("decision_prep").write_text(
            "# Decision prep\nresume_at: Phase 5\ndi_id: DI-1\n\n"
            "## Ruling\nDI-1: amend the contract.\n")
        agents = fakes.FakeAgents(self.cfg)

        def consume(*a):
            self.cfg.path("decision_prep").unlink()
            self.write_stage()
        agents.on("wf-designer", consume)
        cli = self.full_cli()
        rt = self.rt(cli=cli, agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.phase = "awaiting_ruling"
        rt.state.save()
        self.assertEqual(loop.run_loop(rt, once=True), 0)
        self.assertIn(["pipeline", "resolve-design-issue", "DI-1"], cli.calls)

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
        self.assertFalse(self.cfg.path("stage").exists())

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
