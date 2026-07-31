#!/usr/bin/env python3
"""Tests for the per-sprint phase machine: sprint_start → designing → increment_loop
→ closeout, its halts, and its escalation pause.
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

SLICE_OK = {"verdict": "pass", "serves": ["CAP-001", "L-002"],
            "increments": [{"n": 1, "title": "seam"}, {"n": 2, "title": "http"}],
            "errors": []}


class PhaseTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        for role in ("wf-designer", "wf-tl"):
            (self.root / f".claude/skills/{role}").mkdir(parents=True)
            (self.root / f".claude/skills/{role}/SKILL.md").write_text("x\n")
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-discover", "wf-adequacy", "wf-retrospective"):
            (self.root / f".claude/agents/{role}.md").write_text("x\n")
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)

    def rt(self, **kw):
        return fakes.runtime(self.cfg, **kw)

    def write_slice(self):
        self.cfg.path("design_slice").write_text(
            "**Serves:** CAP-001\n\n## Increments\n\n### Increment 1 — seam\n"
            "Checkpoint: after this, a patch round-trips.\n")

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

    def test_any_other_dirt_alongside_the_telemetry_sink_still_halts(self):
        git = fakes.FakeGit(dirty=[self.cfg.rel("telemetry"), "backend/zones.go"])
        rt = self.rt(git=git)
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.sprint_start(rt)
        self.assertEqual(caught.exception.reason, "dirty_tree")
        self.assertIn("backend/zones.go", caught.exception.detail)
        self.assertEqual(git.commits, [])

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

    def test_designing_gates_on_slice_check_and_enters_the_increment_loop(self):
        cli = fakes.FakeCli({("slice", "check"): SLICE_OK})
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-designer", lambda *a: self.write_slice())
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        self.assertEqual(agents.launches[0]["role"], "wf-designer")
        self.assertEqual(agents.launches[0]["mode"], "originate")
        self.assertEqual(rt.state.phase, "increment_loop")

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

    def test_no_slice_written_is_work_exhaustion(self):
        rt = self.rt()
        with self.assertRaises(driver_runtime.Pause) as caught:
            phases.designing(rt)
        self.assertEqual(caught.exception.reason, "work_exhaustion")

    def test_a_red_slice_check_routes_to_designer_repair_then_proceeds(self):
        cli = fakes.FakeCli({("slice", "check"): [
            (1, {"verdict": "fail", "errors": [{"code": "A9", "msg": "bad"}],
                 "increments": []}),
            SLICE_OK,
        ]})
        agents = fakes.FakeAgents(self.cfg)
        resolve = fakes.resolve_issues(self.cfg, fix_kind="slice_recut")

        def originate(*a):
            self.write_slice()
        agents.on("wf-designer", originate)
        agents.on("wf-designer", resolve)  # the repair run leaves the issue resolved
        rt = self.rt(cli=cli, agents=agents)
        phases.designing(rt)
        modes = [x["mode"] for x in agents.launches]
        self.assertEqual(modes, ["originate", "repair"])
        self.assertIn("pipeline record-design-issue", cli.verbs())
        self.assertEqual(rt.state.phase, "increment_loop")

    # ── closeout ─────────────────────────────────────────────────────────────

    def test_closeout_runs_its_steps_then_adequacy_drain_and_ship(self):
        self.write_slice()
        self.cfg.path("capabilities").write_text(
            'version: 1\ncapabilities:\n  - id: "CAP-001"\n'
            '    statement: "Users can patch a zone."\n')
        cli = fakes.FakeCli({
            ("slice", "check"): SLICE_OK,
            ("pipeline", "drain-capability"): {"drained": True, "verdict": "adequate"},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })
        git = fakes.FakeGit()
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli=cli, git=git, agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        self.assertEqual(agents.roles(), ["wf-retrospective", "wf-adequacy"])
        self.assertEqual(agents.launches[-1]["params"]["Question"], "full-promise")
        self.assertIn("pipeline complete-sprint", cli.verbs())
        self.assertEqual(git.pushed, ["sprint/s1"])
        self.assertEqual(git.prs[0]["head"], "sprint/s1")
        self.assertEqual(git.prs[0]["base"], "main")

    def closeout_cli(self):
        return fakes.FakeCli({
            ("slice", "check"): SLICE_OK,
            ("pipeline", "drain-capability"): {"drained": True, "verdict": "adequate"},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })

    def closeout_rt(self, steps, **kw):
        path = support.write_config(self.root)
        path.write_text(path.read_text().replace(
            "closeout: [wf-retrospective, adequacy, ship]", f"closeout: {steps}"))
        self.cfg = driver_config.load(str(path))
        rt = self.rt(**kw)
        rt.state.start_sprint("s1", "sprint/s1")
        return rt

    def test_a_closeout_step_the_driver_cannot_run_halts_rather_than_being_skipped(self):
        self.write_slice()
        rt = self.closeout_rt("[wf-retrospective, adequate, ship]",
                              cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "unknown_closeout_step")
        self.assertIn("adequate", caught.exception.detail)

    def test_a_closeout_list_that_ships_before_adequacy_halts(self):
        # ship archives the slice and drains the working set; an adequacy pass after
        # it reviews a sprint that is already closed
        self.write_slice()
        rt = self.closeout_rt("[wf-retrospective, ship, adequacy]",
                              cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "closeout_order")

    def test_a_closeout_list_that_ships_without_adequacy_halts(self):
        self.write_slice()
        rt = self.closeout_rt("[wf-retrospective, ship]", cli=self.closeout_cli())
        with self.assertRaises(driver_runtime.Halt) as caught:
            phases.closeout(rt)
        self.assertEqual(caught.exception.reason, "closeout_order")

    def test_a_restart_mid_closeout_skips_the_steps_that_already_ran(self):
        self.write_slice()
        self.cfg.path("capabilities").write_text(
            'version: 1\ncapabilities:\n  - id: "CAP-001"\n    statement: "s"\n')
        agents = fakes.FakeAgents(self.cfg)

        def die_after_retro(*a):
            raise driver_runtime.Halt("boom", "the run was killed mid-closeout")
        agents.on("wf-adequacy", die_after_retro)
        rt = self.rt(cli=self.closeout_cli(), agents=agents)
        rt.state.start_sprint("s1", "sprint/s1")
        with self.assertRaises(driver_runtime.Halt):
            phases.closeout(rt)
        self.assertEqual(rt.state.closeout_done, ["wf-retrospective"])

        # the restart re-enters closeout with the retrospective already banked
        agents2 = fakes.FakeAgents(self.cfg)
        rt2 = self.rt(cli=self.closeout_cli(), agents=agents2,
                      state=driver_state.load(self.cfg))
        phases.closeout(rt2)
        self.assertEqual(agents2.roles(), ["wf-adequacy"])

    def test_three_inadequate_verdicts_park_the_capability(self):
        self.write_slice()
        self.cfg.path("capabilities").write_text(
            'version: 1\ncapabilities:\n  - id: "CAP-001"\n'
            '    statement: "Users can patch a zone."\n    status: planned\n')
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
            (cache / f"adequacy-CAP-001-full-promise-{stamp}.md").write_text(
                "# Adequacy: CAP-001 — inadequate\n")
        cli = fakes.FakeCli({
            ("slice", "check"): SLICE_OK,
            ("pipeline", "drain-capability"): (1, {"drained": False,
                                                   "verdict": "inadequate"}),
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })
        rt = self.rt(cli=cli)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        self.assertIn("status: parked", self.cfg.path("capabilities").read_text())

    def test_ship_folds_the_slices_decision_log_into_the_pr_body(self):
        # the decision report lives in the slice, and complete-sprint archives the
        # slice — so it is read before the close, not after
        self.cfg.path("design_slice").write_text(
            "# Design-slice — the seam\n\n**Serves:** CAP-001\n\n"
            "## Decision log\n\n"
            "<!-- Ships in the sprint PR body. -->\n\n"
            "- **Assumption** — CAP-001 read as one caller at a time.\n\n"
            "## Soundness\n\n- Cohesion: pass.\n")
        cli = fakes.FakeCli({
            ("slice", "check"): {"verdict": "pass", "serves": [], "increments": []},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1",
                                              "drain": {"learnings_drained": ["L-2"]}},
        })
        git = fakes.FakeGit()
        rt = self.rt(cli=cli, git=git)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        body = git.prs[0]["body"]
        self.assertIn("CAP-001 read as one caller at a time", body)
        self.assertNotIn("Ships in the sprint PR body", body)   # comments are not prose
        self.assertNotIn("Cohesion", body)                      # only its own section
        self.assertIn("L-2", body)

    def test_ship_stages_the_telemetry_sink_with_the_close(self):
        self.write_slice()
        cli = fakes.FakeCli({
            ("slice", "check"): {"verdict": "pass", "serves": [], "increments": []},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })
        git = fakes.FakeGit()
        rt = self.rt(cli=cli, git=git)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        self.assertIn(self.cfg.rel("telemetry"), git.commits[-1][1])

    def test_an_inadequate_verdict_appends_the_digests_residuals_to_the_capability(self):
        self.write_slice()
        self.cfg.path("capabilities").write_text(
            'version: 1\ncapabilities:\n  - id: "CAP-001"\n    statement: "s"\n')
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        digest = cache / "adequacy-CAP-001-full-promise-20260101T000000Z.md"
        digest.write_text("# Adequacy: CAP-001 — inadequate\n")
        cli = fakes.FakeCli({
            ("slice", "check"): SLICE_OK,
            ("pipeline", "drain-capability"): (1, {"drained": False,
                                                   "verdict": "inadequate",
                                                   "digest": str(digest)}),
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })
        rt = self.rt(cli=cli)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        call = next(c for c in cli.calls if c[:2] == ["pipeline", "append-residuals"])
        self.assertEqual(call[2], "CAP-001")
        self.assertEqual(call[call.index("--digest") + 1], str(digest))

    def test_closeout_leaves_the_state_ready_for_the_next_sprint(self):
        self.write_slice()
        cli = fakes.FakeCli({
            ("slice", "check"): {"verdict": "pass", "serves": [], "increments": []},
            ("pipeline", "complete-sprint"): {"sprint_id": "s1", "drain": {}},
        })
        rt = self.rt(cli=cli)
        rt.state.start_sprint("s1", "sprint/s1")
        phases.closeout(rt)
        self.assertEqual(rt.state.phase, "sprint_start")
        self.assertIsNone(rt.state.sprint_id)


if __name__ == "__main__":
    unittest.main()
