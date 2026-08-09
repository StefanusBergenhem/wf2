#!/usr/bin/env python3
"""Tests for the per-sprint phase machine: sprint_start → designing → stage_run
→ closeout, its halts, its escalation pause, and the completion gate that fires per
capability at every stage close.
Run: python3 tools/driver/tests/phases_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import fakes
import phases
import runtime as driver_runtime
import state as driver_state

STAGE_OK = {"stage": 7, "verdict": "pass", "serves": ["CAP-001", "L-002"],
            "tasks": 2, "errors": [], "warnings": []}

CAPS = ('version: 1\ncapabilities:\n  - id: "CAP-001"\n'
        '    statement: "Users can patch a zone."\n'
        '    value: "One less manual step."\n'
        '    system_tests:\n'
        '      - id: SYS-TC-1\n'
        '        title: "a zone is patched end to end"\n')


class PhaseTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        (self.root / ".claude/skills/wf-designer").mkdir(parents=True)
        (self.root / ".claude/skills/wf-designer/SKILL.md").write_text("x\n")
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-discover", "wf-adequacy", "wf-retrospective"):
            (self.root / f".claude/agents/{role}.md").write_text("x\n")
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)

    def rt(self, **kw):
        return fakes.runtime(self.cfg, **kw)

    def write_stage(self, **kw):
        return support.write_stage(self.cfg, **kw)

    # ── sprint_start ─────────────────────────────────────────────────────────

    def test_sprint_start_branches_off_the_stack_tip_and_refreshes_discover(self):
        git = fakes.FakeGit(stack=["sprint/s1"], sprint_id="s2")
        rt = self.rt(git=git)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt)
        self.assertEqual(git.branches, ["sprint/s2"])
        self.assertEqual(rt.state.sprint_id, "s2")
        self.assertEqual(rt.state.sprint_branch, "sprint/s2")
        self.assertIn("wf-discover", rt.agents.roles())
        self.assertEqual(rt.state.phase, "designing")

    def test_sprint_start_resets_the_pr_body_accumulator(self):
        """It is appended to at every stage merge, so a sprint that started on the last
        one's leftovers would open a PR describing work that is already merged."""
        self.cfg.path("pr_body").write_text("## Stage 6\n\n**Serves:** CAP-999\n")
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        rt = self.rt(git=fakes.FakeGit(sprint_id="s2"))
        phases.sprint_start(rt)
        self.assertFalse(self.cfg.path("pr_body").exists())

    def test_sprint_start_records_the_sprint_id_in_the_run_state(self):
        git = fakes.FakeGit(stack=["sprint/s1"], sprint_id="s2")
        cli = fakes.FakeCli()
        rt = self.rt(git=git, cli=cli)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt)
        transition = next(c for c in cli.calls if c[:2] == ["pipeline", "transition"])
        self.assertIn("--sprint-id", transition)
        self.assertEqual(transition[transition.index("--sprint-id") + 1], "s2")

    def test_sprint_start_halts_on_a_dirty_tree(self):
        rt = self.rt(git=fakes.FakeGit(clean=False))
        with self.assertRaises(driver_runtime.Halt):
            phases.sprint_start(rt)

    def test_a_tree_dirtied_only_by_the_telemetry_sink_is_committed_not_halted(self):
        # roles and Stop hooks append rows to the committed sink after the sprint's
        # last commit; that must not block the next sprint
        git = fakes.FakeGit(dirty=[self.cfg.rel("telemetry")], sprint_id="s2")
        rt = self.rt(git=git)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt)
        self.assertEqual(git.commits[0][1], [self.cfg.rel("telemetry")])
        self.assertIn("telemetry", git.commits[0][0])
        self.assertIn("s2", git.commits[0][0])
        self.assertEqual(git.branches, ["sprint/s2"])

    def test_the_telemetry_carry_over_commit_lands_on_the_new_sprint_branch(self):
        # HEAD is still on the PREVIOUS sprint's branch when the next sprint starts.
        # Committing the carried rows before cutting the branch puts them on a branch
        # that is already pushed and merging: it stops registering as merged, the stack
        # never drains, and the next sprint's PR targets a deleted base.
        git = fakes.FakeGit(stack=[], sprint_id="s2")
        git.branches.append("sprint/s1")            # where ship left HEAD
        rt = self.rt(git=git)
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt)
        self.assertEqual(git.commits[0][2], "sprint/s2")

    def test_any_other_dirt_alongside_the_telemetry_sink_still_halts(self):
        git = fakes.FakeGit(dirty=[self.cfg.rel("telemetry"), "backend/zones.go"])
        rt = self.rt(git=git)
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.sprint_start(rt)
        self.assertEqual(caught.exception.reason, "dirty_tree")
        self.assertIn("backend/zones.go", caught.exception.detail)
        self.assertEqual(git.commits, [])
        # and no branch was cut: a stray sprint/sN burns the ordinal the next run mints
        self.assertEqual(git.branches, [])

    def test_sprint_start_resumes_the_existing_sprint_branch(self):
        git = fakes.FakeGit(sprint_id="s5")
        rt = self.rt(git=git)
        rt.state.sprint_id = "s4"
        rt.state.sprint_branch = "sprint/s4"
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt, resume=True)
        self.assertEqual(git.branches, ["sprint/s4"])
        self.assertEqual(rt.state.sprint_id, "s4")

    def test_sprint_start_halts_when_discover_leaves_no_brief(self):
        rt = self.rt()
        with self.assertRaises(driver_runtime.Halt):
            phases.sprint_start(rt)

    # ── designing ────────────────────────────────────────────────────────────

    def test_designing_gates_on_stage_check_and_enters_the_stage_run(self):
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertEqual(agents.launches[0]["role"], "wf-designer")
        self.assertEqual(rt.state.phase, "stage_run")
        self.assertEqual(rt.state.stage, 7)

    def test_the_design_role_is_dispatched_with_no_mode(self):
        """One role, one sitting, one artifact: there is no originate/repair split left
        to select, and a mode a role does not read is context cost."""
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertIsNone(agents.launches[0]["mode"])
        self.assertNotIn("Mode", agents.launches[0]["params"])

    def test_the_gate_materializes_the_scenario_text_before_it_checks(self):
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        verbs = cli.verbs()
        self.assertEqual(verbs[verbs.index("stage materialize"):][:2],
                         ["stage materialize", "stage check"])

    def test_a_red_materialize_is_a_gate_finding_too(self):
        """Its errors — a system_tests id no capability carries — are exactly the kind
        of defect the check itself never sees, and would ship an unfillable tag."""
        cli = fakes.FakeCli({
            ("stage", "materialize"): [
                (1, {"verdict": "fail",
                     "errors": ["S7-T1: system_tests names SYS-TC-9, which no "
                                "capability carries a scenario for"]}),
                {"verdict": "pass"}],
            ("stage", "check"): STAGE_OK,
        })
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        agents.on("wf-designer", fakes.resolve_issues(self.cfg, task="S7-T1"))
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertEqual(agents.roles(), ["wf-designer", "wf-designer"])
        issue = self.cfg.path("design_issues").read_text()
        self.assertIn("SYS-TC-9", issue)

    def test_a_decision_prep_pauses_the_run_for_a_ruling(self):
        agents = fakes.FakeAgents(self.cfg)

        def escalate(*a):
            self.cfg.path("decision_prep").write_text("# Decision prep\n")
        agents.on("wf-designer", escalate)
        rt = self.rt(agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "escalation")
        self.assertEqual(rt.state.phase, "awaiting_ruling")

    def test_no_stage_written_with_an_empty_work_set_is_work_exhaustion(self):
        self.cfg.path("capabilities").write_text("version: 1\ncapabilities: []\n")
        rt = self.rt()
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "work_exhaustion")

    def test_no_stage_written_with_work_still_open_is_not_work_exhaustion(self):
        """"Nothing is in scope" is a claim about the WORK-SET, so it is read from the
        work-set. The empty disk on its own says only that this cut produced nothing —
        the run that exposed this stopped saying work exhaustion with 18 capabilities and
        89 learnings open and unparked, which reads as a drained backlog."""
        self.cfg.path("capabilities").write_text(CAPS)
        rt = self.rt()
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "no_stage_cut")
        self.assertIn("1 capability", caught.exception.detail)
        self.assertIn("0 learning", caught.exception.detail)

    def test_a_no_stage_verdict_with_nothing_dispatched_halts(self):
        """A verdict about the work needs a role to have produced it. Reaching the
        no-stage ending with nothing launched is how the loop wedged: it concluded,
        saved, and exited 0 without the design role ever running."""
        rt = self.rt()
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.stage_gate(rt)
        self.assertEqual(caught.exception.reason, "no_design_dispatch")

    def test_authoring_a_scenario_set_is_not_work_exhaustion(self):
        """Taking up a capability with no scenario set is a job of its own, and the role
        ends that dispatch having written no stage — the same empty disk work exhaustion
        leaves. The work-set's scenario count is what separates them; without it the loop
        halts with work still in scope and a human has to restart it."""
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK,
                             ("workset", "check"): [{"scenarios": 0}, {"scenarios": 5},
                                                    {"scenarios": 5}]})
        agents = fakes.FakeAgents(self.cfg)
        calls = []

        def author_then_cut(*a):
            calls.append(1)
            if len(calls) > 1:            # the second dispatch cuts the stage
                self.write_stage()
        agents.on("wf-designer", author_then_cut)
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertEqual(len(calls), 2)
        self.assertEqual(rt.state.phase, "stage_run")

    def test_a_role_that_only_ever_authors_is_bounded(self):
        """A scenario count that keeps growing while no stage appears must not spin the
        loop forever — it exhausts its rounds and falls through to the normal verdict."""
        cli = fakes.FakeCli({("workset", "check"):
                             lambda c, a: {"scenarios": len(c.calls)}})
        rt = self.rt(cli=cli)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "work_exhaustion")

    def test_work_exhaustion_ships_the_stages_already_merged_first(self):
        """Merged, reviewed, green work left on a branch nobody is looking at is worse
        than a stop: the PR goes out, then the loop exits."""
        self.cfg.path("capabilities").write_text("version: 1\ncapabilities: []\n")
        rt = self.rt()
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.stages_shipped = 2
        phases.designing(rt)
        self.assertEqual(rt.state.phase, "closeout")
        self.assertEqual(rt.state.stop_pending, "work_exhaustion")

    # ── a resumed cut ────────────────────────────────────────────────────────

    def ruled_brief(self, di_id="DI-3"):
        self.cfg.path("decision_prep").write_text(
            f"# Decision prep\ndi_id: {di_id}\n\n## Ruling\nTake option B.\n")

    def awaiting_ruling(self, **kw):
        rt = self.rt(**kw)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.suspend("awaiting_ruling")
        return rt

    def test_a_resumed_cut_that_authors_a_scenario_set_cuts_on_the_next_round(self):
        """The ruling round is a design dispatch like any other, and spending it on the
        capability's scenario set leaves the same empty disk work exhaustion does. The
        resume path had none of the rounds the first cut gets, so the run that exposed
        this stopped on a set it had just grown from 41 scenarios to 44."""
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK,
                             ("workset", "check"): [{"scenarios": 41},
                                                    {"scenarios": 44}]})
        agents = fakes.FakeAgents(self.cfg)
        calls = []

        def consume_then_cut(*a):
            calls.append(1)
            if len(calls) == 1:
                self.cfg.path("decision_prep").unlink()   # the ruling is consumed
            else:
                self.write_stage()
        agents.on("wf-designer", consume_then_cut)
        self.ruled_brief()
        rt = self.awaiting_ruling(cli=cli, agents=agents)
        phases.resume_ruling(rt)
        self.assertEqual([x["mode"] for x in agents.launches], ["resume", None])
        self.assertEqual(rt.state.phase, "stage_run")

    def test_a_consumed_ruling_leaves_the_phase_ready_to_cut(self):
        """The wedge: the ruling round consumed the brief and the run stopped with the
        phase still at awaiting_ruling. Every restart then found no brief to resume,
        dispatched nothing, and exited 0 on the spot — forever."""
        self.cfg.path("capabilities").write_text("version: 1\ncapabilities: []\n")
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.cfg.path("decision_prep").unlink())
        self.ruled_brief()
        rt = self.awaiting_ruling(agents=agents)
        with self.assertRaises(driver_runtime.Pause):
            phases.resume_ruling(rt)
        self.assertEqual(rt.state.phase, "designing")
        self.assertEqual(driver_state.load(self.cfg).phase, "designing")

    def test_awaiting_a_ruling_that_is_already_consumed_dispatches_the_cut(self):
        """A run parked at awaiting_ruling whose brief is gone is waiting on nothing —
        resuming it must cut, not conclude."""
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.awaiting_ruling(cli=cli, agents=agents)
        phases.resume_ruling(rt)
        self.assertEqual([x["mode"] for x in agents.launches], [None])
        self.assertEqual(rt.state.phase, "stage_run")

    def test_a_resume_that_escalates_again_parks_the_run_unchanged(self):
        agents = fakes.FakeAgents(self.cfg)

        def escalate_again(*a):
            self.cfg.path("decision_prep").write_text(   # the ruling landed a new one
                "# Decision prep\ndi_id: DI-4\n\n## Ruling\n<!-- pending -->\n")
        agents.on("wf-designer", escalate_again)
        self.ruled_brief()
        rt = self.awaiting_ruling(agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.resume_ruling(rt)
        self.assertEqual(caught.exception.reason, "escalation")
        self.assertEqual(rt.state.phase, "awaiting_ruling")

    # ── a launch the harness refused ─────────────────────────────────────────

    def test_a_refused_design_launch_is_not_read_as_an_empty_work_set(self):
        """No stage on disk means "nothing is in scope" only if the role ran and decided
        so. A session limit leaves the same empty disk for the opposite reason."""
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-designer")
        rt = self.rt(agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertIn("session limit", caught.exception.detail)
        self.assertIn("wf-designer", caught.exception.detail)

    def test_a_refused_design_launch_does_not_ship_a_half_built_pr_either(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-designer")
        rt = self.rt(agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.stages_shipped = 2
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")

    def test_a_bad_exit_that_still_wrote_the_stage_carries_on(self):
        """The exit code alone proves nothing — the real run that exposed this had a
        designer exit 1 having already written a cut that passed its gate."""
        cli = fakes.FakeCli({("stage", "check"): STAGE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.exit_codes["wf-designer"] = 1
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        self.assertEqual(phases.designing(rt), STAGE_OK)
        self.assertEqual(rt.state.phase, "stage_run")

    def test_a_refused_discover_launch_is_not_read_as_a_missing_brief(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-discover")
        rt = self.rt(agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.sprint_start(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")

    def test_a_refused_closeout_launch_stops_the_close_and_is_not_banked(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-retrospective")
        rt = self.rt(agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertNotIn("wf-retrospective", rt.state.closeout_done)

    # ── resuming into a position git does not have ───────────────────────────

    def test_a_resume_onto_a_branch_git_lacks_halts_before_anything_runs(self):
        """The dems failure: a dry run's state file resumed a real run into `designing`,
        so no branch was ever cut and a whole sprint was built on main."""
        git = fakes.FakeGit(absent=["sprint/s1"])
        rt = self.rt(git=git)
        rt.state.start_sprint("s1", "sprint/s1")
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.verify_position(rt)
        self.assertEqual(caught.exception.reason, "sprint_branch_missing")
        self.assertIn("sprint/s1", caught.exception.detail)
        self.assertIn("git checkout -b", caught.exception.detail)
        self.assertIn(str(self.cfg.state_file), caught.exception.detail)

    def test_a_resume_with_head_elsewhere_checks_the_sprint_branch_out(self):
        git = fakes.FakeGit()
        rt = self.rt(git=git)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.verify_position(rt)
        self.assertEqual(git.current_branch(), "sprint/s1")

    def test_verify_position_is_a_no_op_before_a_sprint_exists(self):
        git = fakes.FakeGit(absent=["sprint/s1"])
        rt = self.rt(git=git)
        phases.verify_position(rt)          # no sprint_branch recorded yet
        self.assertEqual(git.branches, [])

    def test_the_branch_is_cut_before_the_position_is_recorded(self):
        """Recording first leaves a window where the state file names a branch git never
        got — which is exactly the position a resume then trusts."""
        git = fakes.FakeGit(stack=["sprint/s1"], sprint_id="s2")
        order = []
        git.start_branch = lambda name, base: order.append("git")
        rt = self.rt(git=git)
        rt.state.save = lambda: order.append("state")
        self.cfg.path("discover_brief").parent.mkdir(parents=True, exist_ok=True)
        self.cfg.path("discover_brief").write_text("brief\n")
        phases.sprint_start(rt)
        self.assertEqual(order[:2], ["git", "state"])

    def test_a_red_stage_check_goes_back_to_the_design_role_then_proceeds(self):
        cli = fakes.FakeCli({("stage", "check"): [
            (1, {"verdict": "fail", "stage": None,
                 "errors": [{"code": "A6", "msg": "no flow"}]}),
            STAGE_OK,
        ]})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        agents.on("wf-designer", fakes.resolve_issues(self.cfg, task="S7-T1"))
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertEqual(agents.roles(), ["wf-designer", "wf-designer"])
        self.assertIn("pipeline record-design-issue", cli.verbs())
        # the findings ride on the issue: the re-cut has to know WHICH check went red
        self.assertIn("A6: no flow", self.cfg.path("design_issues").read_text())
        self.assertEqual(rt.state.phase, "stage_run")

    def test_a_stage_that_never_passes_its_gate_halts_naming_the_findings(self):
        cli = fakes.FakeCli({("stage", "check"): (1, {
            "verdict": "fail",
            "errors": [{"code": "A12", "msg": "allocates 'internal/http', which "
                                              "neither the repo nor the map carries"}]})})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "stage_gate_red")
        self.assertIn("A12", caught.exception.detail)
        self.assertEqual(agents.roles().count("wf-designer"),
                         phases.STAGE_GATE_ATTEMPTS + 1)

    def test_a_resolved_issues_run_state_twin_is_closed_after_every_cut(self):
        """The design role writes only the host file, and the twin is what parks the
        task the issue names — a twin left open keeps that task out of every frontier."""
        fakes.write_design_issue(self.cfg, di_id="DI-4", task_id="S6-T2",
                                 status="resolved", task="S7-T1")
        cli = fakes.FakeCli({
            ("stage", "check"): STAGE_OK,
            ("pipeline", "unresolved-design-issues"): {
                "count": 1, "issues": [{"di_id": "DI-4", "task_id": "S6-T2"}]},
        })
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_stage())
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertIn(["pipeline", "resolve-design-issue", "DI-4"], cli.calls)

    # ── the completion gate ──────────────────────────────────────────────────

    def test_the_completion_gate_reviews_only_what_capability_complete_names(self):
        self.cfg.path("capabilities").write_text(CAPS)
        cli = fakes.FakeCli({
            ("pipeline", "capability-complete"): {
                "shipped": ["SYS-TC-1"],
                "complete": [{"id": "CAP-001", "kind": "capability",
                              "system_tests": ["SYS-TC-1"], "missing": []}],
                "pending": [{"id": "CAP-002", "missing": ["SYS-TC-4"]}]},
            ("pipeline", "drain-capability"): {"drained": True, "verdict": "adequate"},
        })
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli=cli, agents=agents)
        self.assertEqual(phases.capability_gate(rt), ["CAP-001"])
        self.assertEqual(agents.roles(), ["wf-adequacy"])
        launch = agents.launches[0]
        self.assertEqual(launch["params"]["Question"], "full-promise")
        self.assertEqual(launch["params"]["Capability"], "CAP-001")
        self.assertIn("SYS-TC-1", launch["params"]["Claimed scenarios"])
        self.assertIn("Users can patch a zone", launch["params"]["Statement"])
        self.assertIn(["pipeline", "drain-capability", "CAP-001"], cli.calls)

    def test_nothing_complete_dispatches_no_review(self):
        cli = fakes.FakeCli({("pipeline", "capability-complete"): {"complete": []}})
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli=cli, agents=agents)
        self.assertEqual(phases.capability_gate(rt), [])
        self.assertEqual(agents.roles(), [])

    def test_the_claimed_scenarios_come_from_the_capability_not_a_slice(self):
        self.cfg.path("capabilities").write_text(CAPS)
        rt = self.rt()
        self.assertEqual(phases._scenarios_for(rt, "CAP-001"), ["SYS-TC-1"])

    def test_three_inadequate_verdicts_park_the_capability(self):
        self.cfg.path("capabilities").write_text(CAPS + "    status: planned\n")
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            (cache / f"adequacy-CAP-001-full-promise-{stamp}.md").write_text(
                "# Adequacy: CAP-001 — inadequate\n")
        cli = fakes.FakeCli({
            ("pipeline", "capability-complete"): {"complete": ["CAP-001"]},
            ("pipeline", "drain-capability"): (1, {"drained": False,
                                                   "verdict": "inadequate"}),
        })
        rt = self.rt(cli=cli)
        phases.capability_gate(rt)
        self.assertIn("status: parked", self.cfg.path("capabilities").read_text())

    def test_an_inadequate_verdict_appends_the_digests_residuals_to_the_capability(self):
        self.cfg.path("capabilities").write_text(CAPS)
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        digest = cache / "adequacy-CAP-001-full-promise-20260101T000000Z.md"
        digest.write_text("# Adequacy: CAP-001 — inadequate\n")
        cli = fakes.FakeCli({
            ("pipeline", "capability-complete"): {"complete": ["CAP-001"]},
            ("pipeline", "drain-capability"): (1, {"drained": False,
                                                   "verdict": "inadequate",
                                                   "digest": str(digest)}),
        })
        rt = self.rt(cli=cli)
        phases.capability_gate(rt)
        call = next(c for c in cli.calls if c[:2] == ["pipeline", "append-residuals"])
        self.assertEqual(call[2], "CAP-001")
        self.assertEqual(call[call.index("--digest") + 1], str(digest))

    def test_a_refused_adequacy_launch_never_counts_as_an_inadequate_verdict(self):
        self.cfg.path("capabilities").write_text(CAPS)
        cli = fakes.FakeCli({("pipeline", "capability-complete"):
                             {"complete": ["CAP-001"]}})
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-adequacy")
        rt = self.rt(cli=cli, agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.capability_gate(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")

    # ── closeout ─────────────────────────────────────────────────────────────

    def closeout_cli(self):
        return fakes.FakeCli({
            ("pipeline", "complete-sprint"): {"sprint_id": "s1",
                                              "drain": {"served": ["CAP-001"]}},
        })

    def closeout_rt(self, steps=None, **kw):
        if steps:
            path = support.write_config(self.root)
            path.write_text(path.read_text().replace(
                "closeout: [wf-retrospective, ship]", f"closeout: {steps}"))
            self.cfg = driver_config.load(str(path))
        rt = self.rt(**kw)
        rt.state.start_sprint("s1", "sprint/s1")
        return rt

    def test_closeout_runs_its_steps_then_ships(self):
        git = fakes.FakeGit()
        agents = fakes.FakeAgents(self.cfg)
        rt = self.closeout_rt(cli=self.closeout_cli(), git=git, agents=agents)
        phases.closeout(rt)
        self.assertEqual(agents.roles(), ["wf-retrospective"])
        self.assertEqual(git.pushed, ["sprint/s1"])
        self.assertEqual(git.prs[0]["head"], "sprint/s1")
        self.assertEqual(git.prs[0]["base"], "main")
        self.assertEqual(git.prs[0]["title"], "s1: CAP-001")

    def test_adequacy_is_no_longer_a_closeout_step(self):
        """It fires per capability at every stage close now, on a mechanical trigger
        with no ordering to configure — so naming it here is a config error."""
        rt = self.closeout_rt("[wf-retrospective, adequacy, ship]",
                              cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "unknown_closeout_step")
        self.assertIn("adequacy", caught.exception.detail)

    def test_a_closeout_step_the_driver_cannot_run_halts_rather_than_being_skipped(self):
        rt = self.closeout_rt("[wf-retrospective, publish, ship]",
                              cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "unknown_closeout_step")
        self.assertIn("publish", caught.exception.detail)

    def test_a_closeout_list_with_anything_after_ship_halts(self):
        rt = self.closeout_rt("[ship, wf-retrospective]", cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "closeout_order")

    def test_a_restart_mid_closeout_skips_the_steps_that_already_ran(self):
        agents = fakes.FakeAgents(self.cfg)

        def die_after_retro(*a):
            raise driver_runtime.Halt("boom", "the run was killed mid-closeout")
        agents.on("wf-retrospective", lambda *a: None)
        rt = self.closeout_rt(cli=self.closeout_cli(), agents=agents)
        rt.state.step_done("wf-retrospective")
        agents2 = fakes.FakeAgents(self.cfg)
        rt2 = self.rt(cli=self.closeout_cli(), agents=agents2,
                      state=driver_state.load(self.cfg))
        rt2.state.sprint_branch = "sprint/s1"
        phases.closeout(rt2)
        self.assertEqual(agents2.roles(), [])

    def test_the_pr_body_is_the_accumulator_the_stages_wrote(self):
        """Each stage appended its own serves/checkpoint/decisions block as it merged;
        the close folds the drain and the plan in around them, in the same file."""
        self.cfg.path("pr_body").write_text(
            "## Stage 7\n\n**Serves:** CAP-001\n\n**Decisions:**\n"
            "- Assumption — a patch never creates a zone\n")
        self.cfg.path("plan").write_text("# Plan\n\n- M1: the patch path\n")
        cli = fakes.FakeCli({
            ("pipeline", "complete-sprint"): {
                "sprint_id": "s1",
                "drain": {"served": ["CAP-001"], "merged_tasks": ["S7-T1"],
                          "learnings_drained": ["L-2"]}}})
        git = fakes.FakeGit()
        rt = self.closeout_rt(cli=cli, git=git)
        phases.closeout(rt)
        body = git.prs[0]["body"]
        self.assertIn("## Stage 7", body)
        self.assertIn("a patch never creates a zone", body)
        self.assertIn("L-2", body)
        self.assertIn("S7-T1", body)
        self.assertIn("M1: the patch path", body)

    def test_ship_stages_the_telemetry_sink_with_the_close(self):
        git = fakes.FakeGit()
        rt = self.closeout_rt(cli=self.closeout_cli(), git=git)
        phases.closeout(rt)
        self.assertIn(self.cfg.rel("telemetry"), git.commits[-1][1])

    def test_closeout_leaves_the_state_ready_for_the_next_sprint(self):
        rt = self.closeout_rt(cli=self.closeout_cli())
        rt.state.stages_shipped = 3
        rt.state.stage = 9
        phases.closeout(rt)
        self.assertEqual(rt.state.phase, "sprint_start")
        self.assertIsNone(rt.state.sprint_id)
        self.assertIsNone(rt.state.stage)
        self.assertEqual(rt.state.stages_shipped, 0)


if __name__ == "__main__":
    unittest.main()
