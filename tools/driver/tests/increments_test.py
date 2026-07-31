#!/usr/bin/env python3
"""Tests for the increment loop — JIT contracts, the sub-layer frontier, the
build→review chain, batch merges, and the increment boundary.
Run: python3 tools/driver/tests/increments_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import unittest

import support  # noqa: F401

import config as driver_config
import fakes
import increments
import runtime as driver_runtime


def frontier(*, dispatch=(), approved=(), repairing=(), stage_done=False,
             increment_done=False, sprint_done=False, index=1, total=1, tasks=("T1",)):
    return {
        "increment": 1,
        "stage": {"index": index, "total": total, "tasks": list(tasks)},
        "dispatch": [{"task_id": t, "worktree": f".wf/transient/worktrees/s1-{t}"}
                     for t in dispatch],
        "ready": [], "in_flight": [], "approved": list(approved),
        "repairing": list(repairing), "escalated": [], "blocked": [],
        "terminal": {"stage_done": stage_done, "increment_done": increment_done,
                     "sprint_done": sprint_done, "halt": None},
    }


BUILD_OK = {"task_id": "T1", "verdict": "ready_for_review",
            "build_commit_sha": "bbb1111", "artifact": None, "di_id": None}
REVIEW_OK = {"task_id": "T1", "verdict": "approved", "build_commit_sha": "bbb1111"}


class IncrementTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-build", "wf-review", "wf-stage-repair"):
            (self.root / f".claude/agents/{role}.md").write_text("x\n")
        for role in ("wf-tl", "wf-designer"):
            (self.root / f".claude/skills/{role}").mkdir(parents=True)
            (self.root / f".claude/skills/{role}/SKILL.md").write_text("x\n")
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)
        self.cfg.path("design_slice").write_text(
            "## Increments\n\n### Increment 1 — the seam\n\n"
            "Goal: patch a zone.\nCheckpoint: after this, a patch round-trips.\n")

    def rt(self, cli, agents=None, git=None, **kw):
        agents = agents or fakes.FakeAgents(self.cfg)
        # a real repair leaves its issue resolved; without that the loop halts
        agents.on("wf-designer", fakes.resolve_issues(self.cfg))
        rt = fakes.runtime(self.cfg, cli=cli, agents=agents, git=git, **kw)
        rt.state.start_sprint("s1", "sprint/s1")
        return rt

    def happy_cli(self, overrides=None):
        responses = {
            # no sprint file until the Tech Lead writes one, then increment 1 is there
            ("pipeline", "increments"): [
                (2, {}),
                {"increments": [{"increment": 1, "tasks": 1, "done": False}],
                 "current": 1, "next": 1},
            ],
            ("sprint", "materialize"): {"verdict": "pass"},
            ("sprint", "check"): {"verdict": "pass"},
            ("pipeline", "compute-stages"): {"stages": [["T1"]], "total": 1},
            ("pipeline", "next"): [frontier(dispatch=["T1"]),
                                   frontier(approved=["T1"], stage_done=True,
                                            increment_done=True)],
            ("pipeline", "task-state"): {"state": "pending", "attempt_counter": 0,
                                         "build_commit": "bbb1111"},
            ("orchestrate", "preserve-uncommitted"): {},
            ("orchestrate", "inspect-build-return"): BUILD_OK,
            ("orchestrate", "inspect-review-return"): REVIEW_OK,
        }
        responses.update(overrides or {})
        return fakes.FakeCli(responses)

    # ── the happy path ───────────────────────────────────────────────────────

    def test_one_task_runs_build_review_merge_and_completes_the_increment(self):
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        git = fakes.FakeGit()
        rt = self.rt(cli, agents=agents, git=git)
        increments.run_increment(rt, 1)

        self.assertEqual(agents.roles(), ["wf-tl", "wf-build", "wf-review"])
        self.assertEqual(agents.launches[0]["params"]["Increment"], 1)
        build = agents.launches[1]
        self.assertEqual(build["task_id"], "T1")
        self.assertTrue(build["cwd"].endswith("worktrees/s1-T1"))
        self.assertEqual(build["params"]["worktree"], build["cwd"])
        review = agents.launches[2]
        self.assertEqual(review["params"]["sprint_branch"], "sprint/s1")

        verbs = cli.verbs()
        self.assertIn("sprint task", verbs)
        self.assertIn("pipeline dispatch", verbs)
        self.assertIn("pipeline approve-task", verbs)
        self.assertIn("pipeline complete-task", verbs)
        self.assertIn("pipeline stage-summary", verbs)
        self.assertEqual(git.merges, ["task/s1-T1"])
        self.assertEqual(git.removed, [str(self.root / ".wf/transient/worktrees/s1-T1")])

    def test_the_contract_envelope_is_written_into_the_worktree(self):
        cli = self.happy_cli()
        rt = self.rt(cli)
        increments.run_increment(rt, 1)
        call = next(c for c in cli.calls if c[:2] == ["sprint", "task"])
        self.assertIn("--write", call)
        target = call[call.index("--write") + 1]
        self.assertTrue(target.endswith("worktrees/s1-T1/.wf/transient/current-task.yaml"))

    # ── routing ──────────────────────────────────────────────────────────────

    def test_a_rejected_review_re_dispatches_the_build_at_the_next_attempt(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "T1", "verdict": "rejected",
                 "artifact": ".wf/transient/feedback.yaml"},
                REVIEW_OK,
            ],
        })
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli, agents=agents)
        increments.run_increment(rt, 1)
        self.assertEqual(agents.roles(),
                         ["wf-tl", "wf-build", "wf-review", "wf-build", "wf-review"])
        self.assertIn("pipeline reject-task", cli.verbs())

    def test_a_build_design_issue_parks_the_task_without_review(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "T1", "verdict": "design_issue",
                "artifact": ".wf/transient/design-issues.yaml", "di_id": "DI-1"},
            ("pipeline", "next"): [frontier(dispatch=["T1"]),
                                   frontier(repairing=["T1"], stage_done=True),
                                   frontier(approved=["T1"], stage_done=True,
                                            increment_done=True)],
        })
        agents = fakes.FakeAgents(self.cfg)
        wt = self.root / ".wf/transient/worktrees/s1-T1/.wf/transient"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "design-issues.yaml").write_text(
            'issues:\n  - id: "DI-1"\n    task_id: "T1"\n    severity: high\n'
            '    status: open\n    summary: "the contract contradicts the increment"\n')
        rt = self.rt(cli, agents=agents)
        increments.run_increment(rt, 1)
        self.assertNotIn("wf-review", agents.roles())
        self.assertIn("wf-designer", agents.roles())
        self.assertEqual(next(x for x in agents.launches
                              if x["role"] == "wf-designer")["mode"], "repair")
        verbs = cli.verbs()
        self.assertIn("pipeline record-design-issue", verbs)
        self.assertIn("pipeline resolve-design-issue", verbs)
        # the issue is promoted to the host file so the repair role can read it
        host = self.cfg.path("design_issues").read_text()
        self.assertIn("DI-1", host)

    def test_no_build_artifacts_blocks_the_task(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "T1", "verdict": "escalate_no_artifacts"},
            ("pipeline", "next"): [frontier(dispatch=["T1"]),
                                   frontier(stage_done=True, increment_done=True)],
        })
        rt = self.rt(cli)
        increments.run_increment(rt, 1)
        self.assertIn("pipeline block-task", cli.verbs())

    # ── merges ───────────────────────────────────────────────────────────────

    def conflict_cli(self):
        return self.happy_cli({
            ("pipeline", "next"): [frontier(dispatch=["T1"]),
                                   frontier(approved=["T1"], stage_done=True),
                                   frontier(stage_done=True, increment_done=True)],
        })

    def test_a_merge_conflict_is_repaired_in_place_by_stage_repair(self):
        cli = self.conflict_cli()
        git = fakes.FakeGit()
        git.conflict_on.add("task/s1-T1")
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-stage-repair", lambda *a: git.resolve_merge())
        rt = self.rt(cli, agents=agents, git=git)
        increments.run_increment(rt, 1)
        repair = next(x for x in agents.launches if x["role"] == "wf-stage-repair")
        self.assertEqual(repair["mode"], "merge")
        self.assertEqual(repair["params"]["task_id"], "T1")
        self.assertEqual(repair["params"]["task_branch"], "task/s1-T1")
        self.assertIn("shared.go", repair["params"]["conflicting_paths"])
        # the resolved merge completes the task; nothing is aborted and no design
        # issue is raised for a conflict the repair rung could resolve — even though
        # the repair's own telemetry row leaves the tree dirty
        self.assertEqual(git.dirty_paths(), [git.telemetry])
        self.assertIn("pipeline complete-task", cli.verbs())
        self.assertEqual(git.aborted, [])
        self.assertNotIn("pipeline record-design-issue", cli.verbs())

    def test_a_failed_merge_repair_aborts_and_records_a_design_issue(self):
        cli = self.conflict_cli()
        git = fakes.FakeGit()
        git.conflict_on.add("task/s1-T1")
        agents = fakes.FakeAgents(self.cfg)
        agents.exit_codes["wf-stage-repair"] = 1  # the repair could not resolve it
        rt = self.rt(cli, agents=agents, git=git)
        increments.run_increment(rt, 1)
        self.assertEqual(git.aborted, ["task/s1-T1"])
        self.assertEqual(git.dirty_paths(), [git.telemetry])   # the conflict is gone
        self.assertNotIn("pipeline complete-task", cli.verbs())
        self.assertIn("pipeline record-design-issue", cli.verbs())
        self.assertIn("shared.go", self.cfg.path("design_issues").read_text())

    # ── the boundary ─────────────────────────────────────────────────────────

    def test_a_red_stage_check_dispatches_stage_repair_with_the_checkpoint(self):
        # the stage-check repair budget is the boundary loop's own, not the
        # build→review budget: halving review.max_attempts must not move it
        path = support.write_config(self.root, stage_check="bash -c 'exit 1'")
        path.write_text(path.read_text().replace("max_attempts: 3", "max_attempts: 1"))
        self.cfg = driver_config.load(str(path))
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli, agents=agents)
        with self.assertRaises(driver_runtime.Halt):
            increments.run_increment(rt, 1)
        repairs = [x for x in agents.launches if x["role"] == "wf-stage-repair"]
        self.assertTrue(repairs)
        self.assertIn("patch round-trips", repairs[0]["params"]["Checkpoint"])
        self.assertEqual(repairs[0]["params"]["mode"], "repair")
        self.assertEqual(len(repairs), increments.STAGE_REPAIR_ATTEMPTS)
        self.assertNotEqual(increments.STAGE_REPAIR_ATTEMPTS, self.cfg.max_attempts)

    def test_the_checkpoint_reads_the_slice_templates_own_line_shape(self):
        self.cfg.path("design_slice").write_text(
            "## Increments\n\n### Increment 1 — the seam\n\n"
            "- **Goal:** a caller can patch a zone.\n"
            "- **Checkpoint:** after this increment, a patch round-trips — "
            "observed by the e2e suite\n")
        rt = self.rt(self.happy_cli())
        self.assertEqual(
            increments.checkpoint(rt, 1),
            "after this increment, a patch round-trips — observed by the e2e suite")

    def test_a_green_stage_check_closes_the_increment(self):
        cfg = driver_config.load(str(support.write_config(
            self.root, stage_check="true")))
        self.cfg = cfg
        cli = self.happy_cli()
        rt = self.rt(cli)
        increments.run_increment(rt, 1)
        self.assertIn("pipeline stage-end", cli.verbs())

    # ── contracts ────────────────────────────────────────────────────────────

    def test_a_tl_slice_defect_routes_to_the_designer_before_any_build(self):
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        # the first Tech Lead run rejects the cut; the second accepts it
        agents.on("wf-tl", fakes.raise_design_issue(self.cfg))
        agents.on("wf-tl", lambda *a: None)
        rt = self.rt(cli, agents=agents)
        increments.run_increment(rt, 1)
        self.assertEqual(agents.roles()[:3], ["wf-tl", "wf-designer", "wf-tl"])

    def write_sprint(self, *numbers):
        """A cumulative sprint file — one task per increment, as the Tech Leads left it."""
        path = self.cfg.path("sprint")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sprint_id: s1\ntasks:\n" + "".join(
            f"  - id: T{n}\n    increment: {n}\n" for n in numbers))
        return path

    def test_the_tl_envelope_names_the_task_ids_from_the_sprint_file(self):
        # a restarted driver has an empty in-memory worktree map; the durable record
        # of what ids are taken is the sprint file
        self.write_sprint(1, 2, 3)
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli, agents=agents)
        self.assertEqual(rt.worktrees, {})
        increments.prepare_contracts(rt, 1)
        tl = next(x for x in agents.launches if x["role"] == "wf-tl")
        self.assertEqual(tl["params"]["task ids already used this sprint"],
                         "T1, T2, T3")

    def test_the_tl_envelope_says_none_yet_when_no_sprint_file_exists(self):
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli, agents=agents)
        increments.prepare_contracts(rt, 1)
        tl = next(x for x in agents.launches if x["role"] == "wf-tl")
        self.assertEqual(tl["params"]["task ids already used this sprint"], "none yet")

    def test_a_slice_recut_prunes_only_the_increment_the_design_issue_names(self):
        sprint = self.write_sprint(1, 2)
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-tl", fakes.raise_design_issue(self.cfg, increment=1))
        agents.on("wf-tl", lambda *a: None)
        rt = self.rt(cli, agents=agents)
        increments.prepare_contracts(rt, 1)
        self.assertEqual([c for c in cli.calls if c[:2] == ["sprint", "prune"]],
                         [["sprint", "prune", "--increment", "1"]])
        # the merged increments in the file are facts — the whole file is never dropped
        self.assertTrue(sprint.exists())
        self.assertIn("id: T2", sprint.read_text())

    def test_a_slice_recut_naming_no_increment_prunes_every_unmerged_increment(self):
        self.write_sprint(1, 2, 3)
        cli = self.happy_cli({
            ("pipeline", "increments"): {
                "increments": [{"increment": 1, "tasks": 1, "done": True},
                               {"increment": 2, "tasks": 1, "done": False},
                               {"increment": 3, "tasks": 1, "done": False}],
                "current": 2, "next": 2},
        })
        issues_path = self.cfg.path("design_issues")
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(
            'issues:\n  - id: "DI-7"\n    task_id: null\n    scope: slice\n'
            '    status: open\n    summary: "the remainder is mis-cut"\n')
        rt = self.rt(cli)
        increments.prepare_contracts(rt, 2)
        self.assertEqual([c[-1] for c in cli.calls if c[:2] == ["sprint", "prune"]],
                         ["2", "3"])

    def test_a_red_sprint_check_routes_to_designer_repair(self):
        cli = self.happy_cli({
            ("sprint", "check"): [(1, {"verdict": "fail",
                                       "errors": [{"code": "B8", "msg": "no story"}]}),
                                  {"verdict": "pass"}],
        })
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(cli, agents=agents)
        increments.run_increment(rt, 1)
        self.assertEqual(agents.roles()[:2], ["wf-tl", "wf-designer"])
        self.assertIn("pipeline record-design-issue", cli.verbs())

    # ── parallelism and ordering ─────────────────────────────────────────────

    def test_the_whole_frontier_runs_in_parallel_worktrees(self):
        cli = self.happy_cli({
            ("pipeline", "next"): [
                frontier(dispatch=["T1", "T2"], tasks=("T1", "T2")),
                frontier(approved=["T1", "T2"], stage_done=True, increment_done=True,
                         tasks=("T1", "T2")),
            ],
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        agents = fakes.FakeAgents(self.cfg)
        git = fakes.FakeGit()
        rt = self.rt(cli, agents=agents, git=git)
        increments.run_increment(rt, 1)
        built = sorted(x["task_id"] for x in agents.launches if x["role"] == "wf-build")
        self.assertEqual(built, ["T1", "T2"])
        self.assertEqual(sorted(w[1] for w in git.worktrees),
                         ["task/s1-T1", "task/s1-T2"])
        self.assertEqual(sorted(git.merges), ["task/s1-T1", "task/s1-T2"])

    def test_the_loop_runs_increments_in_order_and_resumes_at_the_state(self):
        calls = []
        rt = self.rt(self.happy_cli())
        rt.state.increment = 2

        def record(runtime, number):
            calls.append(number)
        original = increments.run_increment
        increments.run_increment = record
        try:
            increments.run_increment_loop(rt, [1, 2, 3])
        finally:
            increments.run_increment = original
        self.assertEqual(calls, [2, 3])
        self.assertEqual(rt.state.increment, 1)

    def test_a_pipeline_halt_stops_the_increment(self):
        halted = frontier()
        halted["terminal"]["halt"] = {"reason": "dependency cycle: T1 -> T2"}
        cli = self.happy_cli({("pipeline", "next"): halted})
        rt = self.rt(cli)
        with self.assertRaises(driver_runtime.Halt):
            increments.run_increment(rt, 1)


if __name__ == "__main__":
    unittest.main()
