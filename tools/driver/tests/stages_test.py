#!/usr/bin/env python3
"""Tests for one stage — loading the cut, the frontier, the build→review chain, batch
merges, the blocked-task channel back into the next cut, and the stage close (heavy
checks, the completion gate, the drill sweep, and the ship-or-cut decision).
Run: python3 tools/driver/tests/stages_test.py
wf2-source-only — never rendered into an install target.
"""
from __future__ import annotations

import threading
import unittest
from pathlib import Path

import support  # noqa: F401

import config as driver_config
import fakes
import issues
import runtime as driver_runtime
import stages


def frontier(*, dispatch=(), approved=(), repairing=(), blocked=(), stage_done=False,
             stage=7, tasks=("S7-T1",)):
    return {
        "stage": {"id": stage, "tasks": list(tasks)},
        "dispatch": [{"task_id": t, "worktree": f".wf/transient/worktrees/s1-{t}"}
                     for t in dispatch],
        "ready": [], "in_flight": [], "approved": list(approved),
        "repairing": list(repairing), "escalated": [], "blocked": list(blocked),
        "terminal": {"stage_done": stage_done, "halt": None},
    }


BUILD_OK = {"task_id": "S7-T1", "verdict": "ready_for_review",
            "build_commit_sha": "bbb1111", "artifact": None, "di_id": None}
REVIEW_OK = {"task_id": "S7-T1", "verdict": "approved", "build_commit_sha": "bbb1111"}


class StageTest(support.TempProject):
    def setUp(self):
        super().setUp()
        self.cfg = driver_config.load(str(support.write_config(self.root)))
        (self.root / ".claude/agents").mkdir(parents=True)
        for role in ("wf-build", "wf-review", "wf-stage-repair", "wf-adequacy"):
            (self.root / f".claude/agents/{role}.md").write_text(support.ROLE_STUB)
        (self.root / ".claude/skills/wf-designer").mkdir(parents=True)
        (self.root / ".claude/skills/wf-designer/SKILL.md").write_text(support.ROLE_STUB)
        self.cfg.path("transient").mkdir(parents=True, exist_ok=True)
        self.write_stage()

    def write_stage(self, **kw):
        return support.write_stage(self.cfg, **kw)

    def rt(self, cli, agents=None, git=None, **kw):
        rt = fakes.runtime(self.cfg, cli=cli, agents=agents or fakes.FakeAgents(self.cfg),
                           git=git, **kw)
        rt.state.start_sprint("s1", "sprint/s1")
        rt.state.stage = 7
        return rt

    def happy_cli(self, overrides=None):
        responses = {
            ("pipeline", "load-stage"): {"stage": 7, "tasks": ["S7-T1"], "count": 1},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(approved=["S7-T1"], stage_done=True)],
            ("pipeline", "task-state"): {"state": "pending", "attempt_counter": 0,
                                         "build_commit": "bbb1111"},
            ("pipeline", "capability-complete"): {"complete": []},
            ("orchestrate", "preserve-uncommitted"): {},
            ("orchestrate", "inspect-build-return"): BUILD_OK,
            ("orchestrate", "inspect-review-return"): REVIEW_OK,
        }
        responses.update(overrides or {})
        return fakes.FakeCli(responses)

    # ── loading the cut ──────────────────────────────────────────────────────

    def test_one_task_runs_build_review_merge_and_closes_the_stage(self):
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        git = fakes.FakeGit()
        rt = self.rt(cli, agents=agents, git=git)
        stages.run_stage(rt)

        self.assertEqual(agents.roles(), ["wf-build", "wf-review"])
        build = agents.launches[0]
        self.assertEqual(build["task_id"], "S7-T1")
        self.assertTrue(build["cwd"].endswith("worktrees/s1-S7-T1"))
        self.assertEqual(build["params"]["worktree"], build["cwd"])
        self.assertEqual(agents.launches[1]["params"]["sprint_branch"], "sprint/s1")

        verbs = cli.verbs()
        self.assertIn("pipeline load-stage", verbs)
        self.assertIn("stage task", verbs)
        self.assertIn("pipeline dispatch", verbs)
        self.assertIn("pipeline approve-task", verbs)
        self.assertIn("pipeline complete-task", verbs)
        self.assertIn("pipeline stage-summary", verbs)
        self.assertEqual(git.merges, ["task/s1-S7-T1"])
        self.assertEqual(git.removed,
                         [str(self.root / ".wf/transient/worktrees/s1-S7-T1")])

    def test_a_build_is_handed_only_the_parameters_its_role_file_reads(self):
        """A parameter no role reads is context the dispatch pays for and the role has
        to decide to ignore. `contract` restated `paths.current_task`, which the envelope
        block already carries under that name, and `attempt` was named nowhere in the
        role at all."""
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(self.happy_cli(), agents=agents))
        self.assertEqual(set(agents.launches[0]["params"]),
                         {"task_id", "worktree", "mode"})
        self.assertEqual(set(agents.launches[1]["params"]),
                         {"task_id", "worktree", "sprint_branch"})

    def test_there_is_no_layering_step(self):
        """A stage IS the tasks with no dependency between them, so there is nothing to
        topologically sort and nothing to advance into."""
        cli = self.happy_cli()
        stages.run_stage(self.rt(cli))
        verbs = cli.verbs()
        for gone in ("pipeline compute-stages", "pipeline advance-stage",
                     "pipeline propagate-blocks", "pipeline blocked-tasks",
                     "sprint materialize", "sprint check", "sprint task"):
            self.assertNotIn(gone, verbs)

    def test_a_load_that_fails_halts(self):
        cli = self.happy_cli({("pipeline", "load-stage"): (1, {})})
        with self.assertRaises(driver_runtime.Halt) as caught:
            stages.run_stage(self.rt(cli))
        self.assertEqual(caught.exception.reason, "load_stage")

    def test_an_already_closed_stage_resumes_into_the_next_cut(self):
        """The close archives and deletes the artifact, so a run interrupted between
        that and the phase write comes back with nothing to load — and its work merged."""
        self.cfg.path("stage").unlink()
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        self.assertEqual(rt.state.phase, "designing")

    def test_the_contract_envelope_is_written_into_the_worktree(self):
        cli = self.happy_cli()
        stages.run_stage(self.rt(cli))
        call = next(c for c in cli.calls if c[:2] == ["stage", "task"])
        self.assertIn("--write", call)
        target = call[call.index("--write") + 1]
        self.assertTrue(
            target.endswith("worktrees/s1-S7-T1/.wf/transient/current-task.yaml"))

    # ── a launch the harness refused ─────────────────────────────────────────

    def test_a_refused_build_launch_pauses_instead_of_blocking_the_task(self):
        """With a refused harness every task in the frontier blocks identically, and the
        stage then halts on `stalled_frontier` — a verdict about work never done."""
        cli = self.happy_cli({("orchestrate", "inspect-build-return"):
                              {"verdict": "no_readable_return"}})
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-build")
        rt = self.rt(cli, agents=agents, git=fakes.FakeGit())
        with self.assertRaises(driver_runtime.Pause) as caught:
            stages.run_stage(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertNotIn("pipeline block-task", cli.verbs())

    def test_a_refused_review_launch_pauses_instead_of_spending_the_chains_budget(self):
        """The live dems failure on T27: a session limit killed the review mid-run, the
        untouched worktree read as `redispatch_same_attempt` — the one branch of the
        chain that never asked whether the harness had run — and all four dispatches
        went in 49 seconds."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): {
                "task_id": "S7-T1", "verdict": "redispatch_same_attempt"},
        })
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-review")
        rt = self.rt(cli, agents=agents)
        with self.assertRaises(driver_runtime.Pause) as caught:
            stages.run_stage(rt)
        self.assertEqual(caught.exception.reason, "launch_failed")
        self.assertEqual(agents.roles().count("wf-review"), 1)
        self.assertNotIn("pipeline block-task", cli.verbs())

    def test_a_review_that_ran_and_left_no_verdict_is_still_sent_back_in(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "redispatch_same_attempt"}, REVIEW_OK],
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(agents.roles().count("wf-review"), 2)
        self.assertIn("pipeline approve-task", cli.verbs())
        self.assertNotIn("pipeline block-task", cli.verbs())

    # ── routing ──────────────────────────────────────────────────────────────

    def test_a_rejected_review_re_dispatches_the_build_at_the_next_attempt(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "rejected",
                 "artifact": ".wf/transient/feedback.yaml"},
                REVIEW_OK,
            ],
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(agents.roles(),
                         ["wf-build", "wf-review", "wf-build", "wf-review"])
        self.assertIn("pipeline reject-task", cli.verbs())

    def test_the_build_is_told_which_mode_it_is_in(self):
        """Which mode a build is in is the loop's own state — it wrote the feedback, or
        retired it. Left to the role, every dispatch opens with a filesystem probe for a
        file only the driver can have put there (58 of 58 dems builds ran one)."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "rejected",
                 "artifact": ".wf/transient/feedback.yaml"},
                REVIEW_OK,
            ],
        })
        agents = fakes.FakeAgents(self.cfg)
        # A rejecting review leaves its feedback in the worktree; that file being there
        # is what the second dispatch is answering.
        feedback = self.worktree() / self.cfg.rel("feedback")
        agents.on("wf-review", lambda *a: (feedback.parent.mkdir(parents=True, exist_ok=True),
                                           feedback.write_text("failures: []\n")))
        stages.run_stage(self.rt(cli, agents=agents))
        modes = [launch["params"].get("mode") for launch in agents.launches
                 if launch["role"] == "wf-build"]
        self.assertEqual(modes, ["build", "fix"])

    # ── the drill digests a contract points at ───────────────────────────────

    def contract_writing_cli(self, body: str, extra=None):
        """A cli whose `stage task` actually writes the contract the driver asked for,
        so what the build would find in its worktree is what the test inspects."""
        def write(cli, args):
            dest = Path(args[args.index("--write") + 1])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body)
            return {}
        return self.happy_cli({("stage", "task"): write, **(extra or {})})

    def test_a_drill_digest_the_contract_points_at_is_carried_into_the_worktree(self):
        """The cache is host-level — the designer's drills wrote it in the main checkout
        and the host prunes it at stage close — but it sits under `paths.transient`,
        which is gitignored, so a fresh worktree has none of it and the contract's own
        pointer resolves nowhere. One dems build had to fall back to the main repo's
        absolute path to read its own grounding."""
        digest = self.cfg.path("drill_cache") / "sys-tc-120-doors-20260812T101500Z.md"
        digest.parent.mkdir(parents=True, exist_ok=True)
        digest.write_text("# Drill: how doors persist\n")
        rel = self.cfg.rel("drill_cache") + "/sys-tc-120-doors-20260812T101500Z.md"
        cli = self.contract_writing_cli(f"id: S7-T1\ngrounding:\n  - \"{rel}\"\n")
        stages.run_stage(self.rt(cli, agents=fakes.FakeAgents(self.cfg)))
        carried = self.worktree() / rel
        self.assertTrue(carried.is_file(),
                        f"the contract's own grounding pointer resolves nowhere: {rel}")
        self.assertEqual(carried.read_text(), "# Drill: how doors persist\n")

    def test_digests_the_contract_never_names_stay_out_of_the_worktree(self):
        """The cache holds every drill of the sprint — 19 digests, 340 KB, ~85 k tokens
        in dems. Carrying the lot would hand a build nine other tasks' reading, against
        a contract that tells it to read only what its grounding names."""
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "mine.md").write_text("mine\n")
        (cache / "someone-elses.md").write_text("theirs\n")
        rel = self.cfg.rel("drill_cache")
        cli = self.contract_writing_cli(f"id: S7-T1\ngrounding:\n  - \"{rel}/mine.md\"\n")
        stages.run_stage(self.rt(cli, agents=fakes.FakeAgents(self.cfg)))
        self.assertTrue((self.worktree() / rel / "mine.md").is_file())
        self.assertFalse((self.worktree() / rel / "someone-elses.md").exists(),
                         "a digest no pointer names was carried in")

    # ── return markers ───────────────────────────────────────────────────────
    # `feedback` and `review_ready` are presence markers: the inspectors route on the
    # file being there, nothing else. Each is read by exactly one dispatch, so the
    # driver retires it at that dispatch's boundary — a marker that outlives its
    # reader is inspected again as a verdict on work it never saw.

    def worktree(self, task_id="S7-T1"):
        return (self.root / f".wf/transient/worktrees/s1-{task_id}").resolve()

    def consume_call(self, marker, task_id="S7-T1"):
        return ["orchestrate", "consume-marker", str(self.worktree(task_id)), marker]

    def snapshot_at(self, agents, cli, role):
        """The CLI calls made up to the moment `role` was launched, one entry per
        launch — so an ordering claim is checked against the interleaving, not the
        final call list."""
        seen = []
        agents.on(role, lambda *a: seen.append(list(cli.calls)))
        return seen

    def test_the_build_must_re_earn_its_review_ready_marker(self):
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        at_build = self.snapshot_at(agents, cli, "wf-build")
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertIn(self.consume_call("review_ready"), at_build[0])

    def test_the_feedback_marker_is_retired_once_the_build_has_read_it(self):
        """The live dems failure: the build fixed the rejection but left feedback.yaml
        behind, so the next review came back `rejected` however it had judged — twice
        over an approval commit — and the attempt cap would have blocked an approved
        task."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "rejected",
                 "artifact": ".wf/transient/feedback.yaml"},
                REVIEW_OK,
            ],
        })
        agents = fakes.FakeAgents(self.cfg)
        at_build = self.snapshot_at(agents, cli, "wf-build")
        at_review = self.snapshot_at(agents, cli, "wf-review")
        stages.run_stage(self.rt(cli, agents=agents))

        call = self.consume_call("feedback")
        self.assertEqual([s.count(call) for s in at_build], [0, 1])
        self.assertEqual([s.count(call) for s in at_review], [1, 2])

    def test_a_build_that_returned_nothing_keeps_its_feedback(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "S7-T1", "verdict": "escalate_no_artifacts"},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(blocked=["S7-T1"], stage_done=True)],
        })
        stages.run_stage(self.rt(cli))
        self.assertNotIn(self.consume_call("feedback"), cli.calls)
        self.assertIn(self.consume_call("review_ready"), cli.calls)

    def test_a_build_design_issue_parks_the_task_and_the_stage_still_closes(self):
        """The issue is read by the design role when it grounds the NEXT cut — no repair
        dispatch, and the parked task does not hold the stage open."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "S7-T1", "verdict": "design_issue",
                "artifact": ".wf/transient/design-issues.yaml", "di_id": "DI-1"},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(repairing=["S7-T1"], stage_done=True)],
        })
        agents = fakes.FakeAgents(self.cfg)
        wt = self.root / ".wf/transient/worktrees/s1-S7-T1/.wf/transient"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "design-issues.yaml").write_text(
            'issues:\n  - id: "DI-1"\n    task_id: "S7-T1"\n    severity: high\n'
            '    status: open\n    summary: "the contract contradicts the flow"\n')
        rt = self.rt(cli, agents=agents)
        stages.run_stage(rt)
        self.assertEqual(agents.roles(), ["wf-build"])   # no review, no repair dispatch
        self.assertIn("pipeline record-design-issue", cli.verbs())
        self.assertIn("DI-1", self.cfg.path("design_issues").read_text())
        self.assertEqual(rt.state.phase, "designing")

    def test_two_tasks_raising_the_same_issue_id_both_survive_the_promote(self):
        """`paths.design_issues` sits under the gitignored transient tree, so a fresh
        worktree has no copy and every agent that raises one mints DI-1 off an empty
        file. Both entries must reach the host file under distinct ids: the old guard
        dropped the second as a duplicate but still mirrored it, so the run state's DI-1
        was re-pointed at T2 while the host file described T1's problem — T1's park was
        lost and one of the two could never be salvaged."""
        for task_id in ("S7-T1", "S7-T2"):
            wt = self.root / f".wf/transient/worktrees/s1-{task_id}/.wf/transient"
            wt.mkdir(parents=True, exist_ok=True)
            (wt / "design-issues.yaml").write_text(
                f'issues:\n  - id: "DI-1"\n    task_id: "{task_id}"\n'
                f'    severity: high\n    status: open\n'
                f'    summary: "{task_id} cannot build its contract"\n')
        cli = self.happy_cli({})
        rt = self.rt(cli, agents=fakes.FakeAgents(self.cfg))
        first = issues.promote(rt, self.worktree("S7-T1"), "DI-1", "S7-T1")
        second = issues.promote(rt, self.worktree("S7-T2"), "DI-1", "S7-T2")

        self.assertNotEqual(first, second)
        raised = self.cfg.path("design_issues").read_text()
        self.assertIn("S7-T1 cannot build its contract", raised)
        self.assertIn("S7-T2 cannot build its contract", raised)
        # Each mirror must name the id its own entry was filed under.
        mirrored = [c for c in cli.calls if c[:2] == ["pipeline", "record-design-issue"]]
        self.assertEqual(
            sorted((c[2], c[c.index("--task") + 1]) for c in mirrored),
            sorted([(first, "S7-T1"), (second, "S7-T2")]))

    def test_a_worktree_that_could_not_be_created_halts_instead_of_dispatching_into_it(self):
        """`worktree_add`'s exit code was discarded. With `commands.provision` unset
        nothing else touches the directory, so the contract is written into a plain dir
        and the build is dispatched with a cwd that is not a git worktree — its commit
        fails, it leaves no marker, and the task burns its whole redispatch budget before
        blocking for a reason that never names the real one."""
        cli = self.happy_cli()
        git = fakes.FakeGit()
        git.worktree_add_rc = 1
        with self.assertRaises(driver_runtime.Halt) as caught:
            stages.run_stage(self.rt(cli, git=git))
        self.assertEqual(caught.exception.reason, "worktree_failed")
        self.assertNotIn("pipeline dispatch", cli.verbs())

    def test_re_promoting_one_task_s_own_issue_files_it_once(self):
        """Renumbering a collision must not turn an idempotent re-promote into a
        duplicate: same task, same summary is the same issue arriving twice."""
        wt = self.root / ".wf/transient/worktrees/s1-S7-T1/.wf/transient"
        wt.mkdir(parents=True, exist_ok=True)
        (wt / "design-issues.yaml").write_text(
            'issues:\n  - id: "DI-1"\n    task_id: "S7-T1"\n    severity: high\n'
            '    status: open\n    summary: "the contract contradicts the flow"\n')
        rt = self.rt(self.happy_cli({}), agents=fakes.FakeAgents(self.cfg))
        first = issues.promote(rt, self.worktree("S7-T1"), "DI-1", "S7-T1")
        again = issues.promote(rt, self.worktree("S7-T1"), "DI-1", "S7-T1")
        self.assertEqual(first, again)
        raised = self.cfg.path("design_issues").read_text()
        self.assertEqual(raised.count("the contract contradicts the flow"), 1)

    def test_a_build_that_never_writes_an_artifact_blocks_on_its_own_budget(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "S7-T1", "verdict": "escalate_no_artifacts"},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(blocked=["S7-T1"], stage_done=True)],
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(agents.roles().count("wf-build"),
                         stages.REDISPATCH_ATTEMPTS + 1)
        blocked = next(c for c in cli.calls if c[:2] == ["pipeline", "block-task"])
        self.assertIn("no artifact", " ".join(str(a) for a in blocked))

    def test_a_redispatch_does_not_spend_the_review_fix_budget(self):
        """The live dems failure on T19: two builds parked and wrote nothing, the third
        was reviewed and rejected once, and the loop blocked the task reporting `review
        rejected the build at every allowed attempt` without ever dispatching a fix."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): [
                {"task_id": "S7-T1", "verdict": "escalate_no_artifacts"},
                {"task_id": "S7-T1", "verdict": "escalate_no_artifacts"},
                BUILD_OK],
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "rejected",
                 "artifact": ".wf/transient/feedback.yaml"},
                REVIEW_OK],
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(agents.roles().count("wf-build"), 4)
        self.assertIn("pipeline approve-task", cli.verbs())
        self.assertNotIn("pipeline block-task", cli.verbs())

    def test_a_build_that_returned_nothing_is_sent_back_in_before_it_blocks(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): [
                {"task_id": "S7-T1", "verdict": "escalate_no_artifacts"}, BUILD_OK],
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(agents.roles().count("wf-build"), 2)
        self.assertIn("pipeline retry-task", cli.verbs())
        self.assertNotIn("pipeline block-task", cli.verbs())

    # ── a blocked task dooms nothing ─────────────────────────────────────────

    def blocked_cli(self, **overrides):
        """One task of two blocks; the other lands. The frontier keeps reporting the
        blocked one as blocked, which does not hold the stage open."""
        responses = {
            ("pipeline", "load-stage"): {"stage": 7, "tasks": ["S7-T1", "S7-T2"],
                                         "count": 2},
            # the approved task stays approved until it merges — the frontier is read
            # again as soon as either task finishes, not only once both have
            ("pipeline", "next"): [
                frontier(dispatch=["S7-T1", "S7-T2"], tasks=("S7-T1", "S7-T2")),
                frontier(approved=["S7-T2"], blocked=["S7-T1"], stage_done=True,
                         tasks=("S7-T1", "S7-T2"))],
            ("orchestrate", "inspect-build-return"): lambda cli, args: (
                {"task_id": "S7-T1", "verdict": "escalate_no_artifacts"}
                if "S7-T1" in args else
                {"verdict": "ready_for_review", "build_commit_sha": "bbb1111"}),
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        }
        responses.update(overrides)
        return self.happy_cli(responses)

    def test_a_blocked_task_closes_the_stage_with_the_rest(self):
        """`tasks_blocked` stops existing: with no edges inside a stage the blocked task
        dooms nothing, the stage closes with what merged, and the work re-enters at the
        next cut — one dispatch away rather than a sprint away."""
        cfg = driver_config.load(str(support.write_config(self.root,
                                                          stage_check="true")))
        self.cfg = cfg
        self.write_stage(tasks=[support.task("S7-T1"), support.task("S7-T2")])
        cli = self.blocked_cli()
        git = fakes.FakeGit()
        rt = self.rt(cli, git=git)
        stages.run_stage(rt)
        self.assertEqual(git.merges, ["task/s1-S7-T2"])
        self.assertIn("pipeline stage-end", cli.verbs())
        self.assertEqual(rt.state.stages_shipped, 1)

    def test_a_block_raises_the_design_issue_that_carries_the_work_forward(self):
        """Neither block site wrote one before, which is why the blocked work had no
        channel back into the next cut at all."""
        self.write_stage(tasks=[support.task("S7-T1"), support.task("S7-T2")])
        cli = self.blocked_cli()
        rt = self.rt(cli, git=fakes.FakeGit())
        stages.run_stage(rt)
        raised = self.cfg.path("design_issues").read_text()
        self.assertIn("S7-T1", raised)
        self.assertIn("task/s1-S7-T1", raised)      # the branch the attempts are on
        self.assertIn("no artifact", raised)         # and why it blocked
        self.assertIn("pipeline record-design-issue", cli.verbs())

    def test_an_approval_after_the_redispatches_is_honoured_not_discarded(self):
        """The chain's budget is for retries, but the terminal "every pass approved"
        check spent an iteration of it too. With the shipped config the slack is 2, so
        three redispatches — the untouched-worktree case the chain explicitly retries —
        followed by a clean approval exhausted the loop, and a green build was thrown
        away as "did not settle". At review.max_attempts: 1 a single redispatch does it."""
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): [
                {"task_id": "S7-T1", "verdict": "redispatch_same_attempt"},
                {"task_id": "S7-T1", "verdict": "redispatch_same_attempt"},
                {"task_id": "S7-T1", "verdict": "redispatch_same_attempt"},
                REVIEW_OK,
            ],
        })
        rt = self.rt(cli, agents=fakes.FakeAgents(self.cfg), git=fakes.FakeGit())
        stages.run_stage(rt)
        self.assertIn("pipeline approve-task", cli.verbs())
        self.assertNotIn("pipeline block-task", cli.verbs())

    def test_a_review_that_rejects_every_attempt_blocks_and_names_its_branch(self):
        cli = self.happy_cli({
            ("orchestrate", "inspect-review-return"): {
                "task_id": "S7-T1", "verdict": "rejected",
                "artifact": ".wf/transient/feedback.yaml"},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(blocked=["S7-T1"], stage_done=True)],
        })
        rt = self.rt(cli)
        stages.run_stage(rt)
        raised = self.cfg.path("design_issues").read_text()
        self.assertIn("review rejected the build at every allowed attempt", raised)
        self.assertIn("task/s1-S7-T1", raised)

    # ── carrying a blocked attempt into its successor ────────────────────────

    def test_a_successor_task_is_cut_from_the_blocked_branch_and_told_why(self):
        """The role's resolution names the successor; that plus the branch on the issue
        is the whole mapping. Without `prior_attempt` the build's Red phase breaks
        silently — code the last attempt wrote can make a new test pass."""
        fakes.write_design_issue(
            self.cfg, di_id="DI-3", task_id="S7-T2", status="resolved",
            fix_kind="contract_amendment", task="S8-T1",
            summary="task S7-T2 blocked — review rejected it at every allowed attempt. "
                    "Its attempts are on branch task/s1-S7-T2")
        self.write_stage(stage=8, tasks=[support.task("S8-T1")])
        cli = self.happy_cli({
            ("pipeline", "load-stage"): {"stage": 8, "tasks": ["S8-T1"], "count": 1},
            ("pipeline", "next"): [frontier(dispatch=["S8-T1"], stage=8,
                                            tasks=("S8-T1",)),
                                   frontier(approved=["S8-T1"], stage_done=True,
                                            stage=8, tasks=("S8-T1",))],
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        git = fakes.FakeGit()
        rt = self.rt(cli, git=git)
        stages.run_stage(rt)
        self.assertEqual(
            git.salvaged,
            [(str(self.root / ".wf/transient/worktrees/s1-S8-T1"), "task/s1-S8-T1",
              "task/s1-S7-T2", "sprint/s1")])
        call = next(c for c in cli.calls if c[:2] == ["stage", "task"])
        self.assertIn("--prior-attempt", call)
        prior = call[call.index("--prior-attempt") + 1]
        self.assertIn("task/s1-S7-T2", prior)
        self.assertIn("review rejected", prior)

    def test_a_conflicting_salvage_falls_back_to_a_fresh_worktree_and_says_nothing(self):
        """Carrying the old work is opportunistic. On a conflict the driver starts from
        the tip — and must NOT claim a prior attempt the worktree does not hold."""
        fakes.write_design_issue(
            self.cfg, di_id="DI-3", task_id="S7-T2", status="resolved", task="S8-T1",
            summary="task S7-T2 blocked — no artifact. On branch task/s1-S7-T2")
        self.write_stage(stage=8, tasks=[support.task("S8-T1")])
        cli = self.happy_cli({
            ("pipeline", "load-stage"): {"stage": 8, "tasks": ["S8-T1"], "count": 1},
            ("pipeline", "next"): [frontier(dispatch=["S8-T1"], stage=8,
                                            tasks=("S8-T1",)),
                                   frontier(approved=["S8-T1"], stage_done=True,
                                            stage=8, tasks=("S8-T1",))],
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        git = fakes.FakeGit(rebase_conflicts=["task/s1-S7-T2"])
        stages.run_stage(self.rt(cli, git=git))
        self.assertEqual(git.salvaged, [])
        self.assertEqual(git.worktrees[0][2], "sprint/s1")
        call = next(c for c in cli.calls if c[:2] == ["stage", "task"])
        self.assertNotIn("--prior-attempt", call)

    def test_an_unresolved_block_carries_nothing(self):
        """Only the design role's resolution names the successor; an open issue has not
        been answered yet, so there is nothing to map onto."""
        fakes.write_design_issue(
            self.cfg, di_id="DI-3", task_id="S7-T2", status="open", task="S8-T1",
            summary="task S7-T2 blocked — on branch task/s1-S7-T2")
        cli = self.happy_cli()
        git = fakes.FakeGit()
        stages.run_stage(self.rt(cli, git=git))
        self.assertEqual(git.salvaged, [])

    # ── merges ───────────────────────────────────────────────────────────────

    def conflict_cli(self):
        return self.happy_cli({
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(approved=["S7-T1"], stage_done=True),
                                   frontier(repairing=["S7-T1"], stage_done=True)],
        })

    def test_a_merge_conflict_is_repaired_in_place_by_stage_repair(self):
        cli = self.conflict_cli()
        git = fakes.FakeGit()
        git.conflict_on.add("task/s1-S7-T1")
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-stage-repair", lambda *a: git.resolve_merge())
        stages.run_stage(self.rt(cli, agents=agents, git=git))
        repair = next(x for x in agents.launches if x["role"] == "wf-stage-repair")
        self.assertEqual(repair["mode"], "merge")
        self.assertEqual(repair["params"]["task_id"], "S7-T1")
        self.assertEqual(repair["params"]["task_branch"], "task/s1-S7-T1")
        self.assertIn("shared.go", repair["params"]["conflicting_paths"])
        self.assertEqual(git.dirty_paths(), [git.telemetry])
        self.assertIn("pipeline complete-task", cli.verbs())
        self.assertEqual(git.aborted, [])
        self.assertNotIn("pipeline record-design-issue", cli.verbs())

    def test_a_failed_merge_repair_aborts_and_records_a_design_issue(self):
        cli = self.conflict_cli()
        git = fakes.FakeGit()
        git.conflict_on.add("task/s1-S7-T1")
        agents = fakes.FakeAgents(self.cfg)
        agents.exit_codes["wf-stage-repair"] = 1  # the repair could not resolve it
        stages.run_stage(self.rt(cli, agents=agents, git=git))
        self.assertEqual(git.aborted, ["task/s1-S7-T1"])
        self.assertEqual(git.dirty_paths(), [git.telemetry])   # the conflict is gone
        self.assertNotIn("pipeline complete-task", cli.verbs())
        self.assertIn("pipeline record-design-issue", cli.verbs())
        self.assertIn("shared.go", self.cfg.path("design_issues").read_text())

    def test_the_whole_stage_runs_in_parallel_worktrees(self):
        self.write_stage(tasks=[support.task("S7-T1"), support.task("S7-T2")])
        cli = self.happy_cli({
            ("pipeline", "load-stage"): {"stage": 7, "tasks": ["S7-T1", "S7-T2"],
                                         "count": 2},
            ("pipeline", "next"): [
                frontier(dispatch=["S7-T1", "S7-T2"], tasks=("S7-T1", "S7-T2")),
                frontier(approved=["S7-T1", "S7-T2"], stage_done=True,
                         tasks=("S7-T1", "S7-T2")),
            ],
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        agents = fakes.FakeAgents(self.cfg)
        git = fakes.FakeGit()
        stages.run_stage(self.rt(cli, agents=agents, git=git))
        built = sorted(x["task_id"] for x in agents.launches if x["role"] == "wf-build")
        self.assertEqual(built, ["S7-T1", "S7-T2"])
        self.assertEqual(sorted(w[1] for w in git.worktrees),
                         ["task/s1-S7-T1", "task/s1-S7-T2"])
        self.assertEqual(sorted(git.merges), ["task/s1-S7-T1", "task/s1-S7-T2"])

    # ── the slots refill as tasks finish ─────────────────────────────────────

    def live_frontier(self, tasks):
        """`pipeline next` answering the way the real one does, from the calls made so
        far: a task holds a slot from its `pipeline dispatch` — not from the moment the
        driver picked it up — and `dispatch` is capped to what is free."""
        cap = int(self.cfg.driver("max_parallel"))

        def answer(cli, args):
            dispatched, approved, settled, merged = set(), set(), set(), set()
            for call in cli.calls:
                head = call[:2]
                if head == ["pipeline", "dispatch"] and "--task" in call:
                    dispatched.add(call[call.index("--task") + 1])
                elif head == ["pipeline", "approve-task"]:
                    approved.add(call[2])
                    settled.add(call[2])
                elif head == ["pipeline", "block-task"]:
                    settled.add(call[2])
                elif head == ["pipeline", "complete-task"]:
                    merged.add(call[2])
            in_flight = dispatched - settled
            pending = [t for t in tasks if t not in dispatched]
            slots = max(0, cap - len(in_flight))
            data = frontier(dispatch=pending[:slots], tasks=tasks,
                            approved=sorted(approved - merged),
                            stage_done=not (pending or in_flight))
            data["ready"] = pending[slots:]
            data["in_flight"] = [{"task_id": t} for t in sorted(in_flight)]
            return data
        return answer

    def test_a_finished_task_frees_its_slot_before_the_batch_drains(self):
        """`driver.max_parallel` is a live ceiling, not a batch size: the next task
        starts the moment one finishes. Waiting for the whole frontier to drain first
        left a ten-task stage running its tail one task wide with three slots idle."""
        tasks = ("S7-T1", "S7-T2", "S7-T3")           # cap is 2, so one waits
        self.write_stage(tasks=[support.task(t) for t in tasks])
        third_started = threading.Event()

        def hold(agents, role, params, task_id):
            if task_id == "S7-T2":
                # holds its slot until the queued task starts — which, if the loop
                # waits for the whole batch, cannot happen until this returns
                self.assertTrue(third_started.wait(timeout=10),
                                "the queued task never started while a slot was held")
            if task_id == "S7-T3":
                third_started.set()

        cli = self.happy_cli({
            ("pipeline", "load-stage"): {"stage": 7, "tasks": list(tasks), "count": 3},
            ("pipeline", "next"): self.live_frontier(tasks),
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-build", hold)
        git = fakes.FakeGit()
        stages.run_stage(self.rt(cli, agents=agents, git=git))
        built = sorted(x["task_id"] for x in agents.launches if x["role"] == "wf-build")
        self.assertEqual(built, list(tasks))          # each one built exactly once
        self.assertEqual(sorted(git.merges),
                         [f"task/s1-{t}" for t in tasks])

    def test_a_task_the_frontier_still_offers_is_not_started_twice(self):
        """A task holds no slot in the pipeline state until its thread reaches `pipeline
        dispatch` — a worktree, a provision and an envelope after the driver picked it
        up. Every frontier read in that window still offers it, and only the driver
        knows it is already running."""
        tasks = ("S7-T1", "S7-T2")
        self.write_stage(tasks=[support.task(t) for t in tasks])
        released = threading.Event()

        def envelope(cli, args):
            if "S7-T2" in args:      # still being set up when the first task finishes
                released.wait(timeout=10)
            return {}

        answer = self.live_frontier(tasks)

        def frontier_read(cli, args):
            data = answer(cli, args)
            if any(c[:2] == ["pipeline", "approve-task"] for c in cli.calls):
                released.set()
            return data

        cli = self.happy_cli({
            ("pipeline", "load-stage"): {"stage": 7, "tasks": list(tasks), "count": 2},
            ("pipeline", "next"): frontier_read,
            ("stage", "task"): envelope,
            ("orchestrate", "inspect-build-return"): {
                "verdict": "ready_for_review", "build_commit_sha": "bbb1111"},
            ("orchestrate", "inspect-review-return"): {"verdict": "approved"},
        })
        agents = fakes.FakeAgents(self.cfg)
        stages.run_stage(self.rt(cli, agents=agents, git=fakes.FakeGit()))
        built = [x["task_id"] for x in agents.launches if x["role"] == "wf-build"]
        self.assertEqual(sorted(built), list(tasks))
        self.assertEqual(len([c for c in cli.calls
                              if c[:2] == ["stage", "task"] and "S7-T2" in c]), 1)

    def test_a_pipeline_halt_stops_the_stage(self):
        halted = frontier()
        halted["terminal"]["halt"] = {"reason": "no stage loaded"}
        with self.assertRaises(driver_runtime.Halt):
            stages.run_stage(self.rt(self.happy_cli({("pipeline", "next"): halted})))

    # ── the close ────────────────────────────────────────────────────────────

    def with_stage_check(self, command):
        self.cfg = driver_config.load(str(support.write_config(
            self.root, stage_check=command)))
        return self.cfg

    def test_a_red_stage_check_dispatches_stage_repair_with_the_stage_and_checkpoint(self):
        # the stage-check repair budget is the close loop's own, not the build→review
        # budget: halving review.max_attempts must not move it
        path = support.write_config(self.root, stage_check="bash -c 'exit 1'")
        path.write_text(path.read_text().replace("max_attempts: 3", "max_attempts: 1"))
        self.cfg = driver_config.load(str(path))
        self.write_stage(checkpoint="after this stage, a patch round-trips")
        cli = self.happy_cli()
        agents = fakes.FakeAgents(self.cfg)
        with self.assertRaises(driver_runtime.Halt) as caught:
            stages.run_stage(self.rt(cli, agents=agents))
        self.assertEqual(caught.exception.reason, "stage_check_red")
        # the log path is the only account of WHY it stayed red
        self.assertIn("stage-check-s1-7.log", caught.exception.detail)
        repairs = [x for x in agents.launches if x["role"] == "wf-stage-repair"]
        self.assertEqual(len(repairs), stages.STAGE_REPAIR_ATTEMPTS)
        self.assertEqual(repairs[0]["params"]["Stage"], 7)
        self.assertIn("patch round-trips", repairs[0]["params"]["Checkpoint"])
        self.assertEqual(repairs[0]["params"]["mode"], "repair")
        self.assertNotEqual(stages.STAGE_REPAIR_ATTEMPTS, self.cfg.max_attempts)

    def test_the_checkpoint_is_one_yaml_read(self):
        self.assertEqual(stages.checkpoint({"checkpoint": "a patch round-trips"}),
                         "a patch round-trips")
        self.assertEqual(stages.checkpoint({"checkpoint": ["a patch round-trips",
                                                           "the read reflects it"]}),
                         "a patch round-trips; the read reflects it")
        self.assertEqual(stages.checkpoint({}), "")

    def test_the_heavy_checks_run_at_every_stage_close(self):
        ran = self.root / "stage-check-ran"
        self.with_stage_check(f"touch {ran}")
        stages.run_stage(self.rt(self.happy_cli()))
        self.assertTrue(ran.exists())

    def test_a_close_interrupted_by_a_red_gate_still_ships_at_its_e2e_boundary(self):
        """`merged` is what THIS invocation merged. A red stage check halts before the
        artifact is archived, so the resume comes back through the normal path, merges
        nothing, and a recomputed `landed_e2e` answers False for a stage whose e2e task
        did land — the PR is not shipped and another stage is cut instead."""
        e2e = support.task("S7-T1", covers=["CAP-001"])
        e2e["system_tests"] = [{"id": "SYS-TC-1", "description": "a patch round-trips"}]
        self.write_stage(tasks=[e2e])

        gate = self.root / "gate-passes"
        self.with_stage_check(f"test -f {gate}")
        self.write_stage(tasks=[e2e])
        rt = self.rt(self.happy_cli(), git=fakes.FakeGit())
        with self.assertRaises(driver_runtime.Halt):
            stages.run_stage(rt)

        # The gate goes green and the run resumes; nothing new merges this time.
        gate.write_text("ok")
        resumed = self.rt(self.happy_cli({
            ("pipeline", "next"): [frontier(stage_done=True)],
        }), git=fakes.FakeGit())
        resumed.state.stage_closing = rt.state.stage_closing
        resumed.state.stage_landed_e2e = rt.state.stage_landed_e2e
        stages.run_stage(resumed)
        self.assertTrue(resumed.state.stage_landed_e2e,
                        "the e2e task merged before the halt — the close must remember")

    def test_the_close_appends_the_stage_to_the_pr_body_then_archives_it(self):
        """The artifact leaves the working set at its own merge: a copy for the
        maintainer, then gone — which is what makes "no stage written" a trustworthy
        work-exhaustion signal at the next cut."""
        cli = self.happy_cli()
        rt = self.rt(cli)
        stages.run_stage(rt)
        self.assertIn("pipeline append-pr-body", cli.verbs())
        archived = next(c for c in cli.calls if c[:2] == ["archive", "add"])
        self.assertEqual(archived[2], str(self.cfg.path("stage")))
        self.assertIn("stage-7", archived)
        self.assertFalse(self.cfg.path("stage").exists())

    def test_the_close_fires_the_completion_gate(self):
        cli = self.happy_cli({
            ("pipeline", "capability-complete"): {"complete": ["CAP-001"]},
            ("pipeline", "drain-capability"): {"drained": True, "verdict": "adequate"},
        })
        agents = fakes.FakeAgents(self.cfg)
        self.cfg.path("capabilities").write_text(
            'version: 1\ncapabilities:\n  - id: "CAP-001"\n    statement: "s"\n')
        stages.run_stage(self.rt(cli, agents=agents))
        self.assertIn("wf-adequacy", agents.roles())
        self.assertIn(["pipeline", "drain-capability", "CAP-001"], cli.calls)

    def test_the_close_prunes_the_stale_drill_digests(self):
        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        stale = cache / "zones-20260808T000000Z.md"
        stale.write_text("# Drill: zones\n**Taken at:** abc1234\n"
                         "**Targets:** store/zones.go\n")
        git = fakes.FakeGit(changed={"abc1234": ["store/zones.go"]})
        stages.run_stage(self.rt(self.happy_cli(), git=git))
        self.assertFalse(stale.exists())

    # ── worktree provisioning ────────────────────────────────────────────────

    def with_provision(self, command):
        self.cfg = driver_config.load(str(support.write_config(
            self.root, provision=command)))
        return self.cfg

    def test_a_fresh_worktree_is_provisioned_before_the_build_runs(self):
        """A worktree is a bare checkout: every gitignored dependency dir the project
        needs to build is absent from it. Nothing installed them once wf-orchestrate's
        git-operations asset was deleted, so the task's first build met a tree it could
        not compile."""
        marker = self.root / "provisioned"
        self.with_provision(f"touch {marker}")
        agents = fakes.FakeAgents(self.cfg)
        agents.on("wf-build", lambda *a: self.assertTrue(
            marker.exists(), "the build ran before provisioning"))
        stages.run_stage(self.rt(self.happy_cli(), agents=agents))
        self.assertTrue(marker.exists())

    def test_provisioning_runs_inside_the_task_worktree(self):
        """It installs INTO the worktree — run from the main checkout it provisions the
        wrong tree and the task still cannot build."""
        self.with_provision("pwd > provisioned-in")
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        wt = self.cfg.worktree_base / "s1-S7-T1"
        self.assertEqual((wt / "provisioned-in").read_text().strip(), str(wt.resolve()))

    def test_no_provision_command_runs_nothing(self):
        """The default. A project needing no provisioning must not pay for a shell."""
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        self.assertEqual(rt.state.phase, "designing")

    def test_a_failed_provision_halts_instead_of_dispatching_into_a_broken_tree(self):
        """The command is the same for every worktree, so a failure is an environment
        fact, not a task fact: it will fail identically for all of them. Dispatching
        anyway spends three build attempts per task discovering that."""
        self.with_provision("exit 3")
        agents = fakes.FakeAgents(self.cfg)
        with self.assertRaises(driver_runtime.Halt) as caught:
            stages.run_stage(self.rt(self.happy_cli(), agents=agents))
        self.assertEqual(caught.exception.reason, "provision_failed")
        self.assertNotIn("wf-build", agents.roles())

    # ── the close survives an interruption after the archive ─────────────────

    def closing_cli(self, overrides=None):
        base = {("pipeline", "capability-complete"): {"complete": ["CAP-001"]},
                ("pipeline", "drain-capability"): {"drained": True,
                                                   "verdict": "adequate"}}
        base.update(overrides or {})
        return self.happy_cli(base)

    def test_a_refused_adequacy_leaves_the_close_resumable(self):
        """The completion gate dispatches an agent, so a rate limit can refuse it — and
        the archive has already deleted the stage by then. Without a marker the resume
        reads "no stage on disk" as "cut the next one", and the drill sweep, the stop
        rules and the ship decision are skipped for good."""
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-adequacy")
        rt = self.rt(self.closing_cli(), agents=agents)
        with self.assertRaises(driver_runtime.Pause):
            stages.run_stage(rt)
        self.assertFalse(self.cfg.path("stage").exists())
        self.assertTrue(rt.state.stage_closing)
        self.assertEqual(rt.state.stages_shipped, 1)

    def test_a_resumed_close_runs_the_steps_the_pause_skipped(self):
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-adequacy")
        rt = self.rt(self.closing_cli(), agents=agents)
        with self.assertRaises(driver_runtime.Pause):
            stages.run_stage(rt)

        cache = self.cfg.path("drill_cache")
        cache.mkdir(parents=True, exist_ok=True)
        stale = cache / "zones-20260808T000000Z.md"
        stale.write_text("# Drill: zones\n**Taken at:** abc1234\n"
                         "**Targets:** store/zones.go\n")

        cli = self.closing_cli()
        resumed = fakes.runtime(
            self.cfg, cli=cli, agents=fakes.FakeAgents(self.cfg),
            git=fakes.FakeGit(changed={"abc1234": ["store/zones.go"]}))
        stages.run_stage(resumed)
        self.assertIn(["pipeline", "drain-capability", "CAP-001"], cli.calls)
        self.assertFalse(stale.exists())
        self.assertEqual(resumed.state.phase, "designing")
        self.assertFalse(resumed.state.stage_closing)
        self.assertEqual(resumed.state.stages_shipped, 1)

    def test_a_resumed_close_still_ships_the_pr_the_pause_owed_it(self):
        """The ship-or-cut decision is the last thing after the archive, so an
        interrupted close is exactly how a finished sprint silently fails to ship."""
        self.e2e_stage()
        agents = fakes.FakeAgents(self.cfg)
        agents.refuse("wf-adequacy")
        rt = self.rt(self.closing_cli(), agents=agents)
        with self.assertRaises(driver_runtime.Pause):
            stages.run_stage(rt)
        resumed = fakes.runtime(self.cfg, cli=self.closing_cli(),
                                agents=fakes.FakeAgents(self.cfg))
        stages.run_stage(resumed)
        self.assertEqual(resumed.state.phase, "closeout")

    def test_a_banked_close_step_does_not_run_twice(self):
        """A second adequacy review on no new evidence shifts the park count, which is
        the same reason closeout banks its steps."""
        agents = fakes.FakeAgents(self.cfg)
        rt = self.rt(self.closing_cli(), agents=agents)
        rt.state.stage_closing = True
        rt.state.stage_landed_e2e = False
        stages._finish_close(rt)
        stages._finish_close(rt)
        self.assertEqual(agents.roles().count("wf-adequacy"), 1)

    # ── ship or cut again ────────────────────────────────────────────────────

    def e2e_stage(self, **kw):
        return self.write_stage(
            tasks=[support.task("S7-T1", system_tests=["SYS-TC-1"])], **kw)

    def test_a_stage_that_lands_a_system_test_ships_the_pr(self):
        """The PR boundary is a fact about the system: the merged tree crossed an
        end-to-end checkpoint."""
        self.e2e_stage()
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        self.assertEqual(rt.state.phase, "closeout")

    def test_a_stage_that_lands_none_cuts_the_next_one_instead(self):
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        self.assertEqual(rt.state.phase, "designing")
        self.assertEqual(rt.state.stages_shipped, 1)

    def test_an_e2e_task_that_did_not_merge_is_not_an_end_to_end_fact(self):
        """The cut's intent is not the fact — only what merged counts."""
        self.e2e_stage()
        cli = self.happy_cli({
            ("orchestrate", "inspect-build-return"): {
                "task_id": "S7-T1", "verdict": "escalate_no_artifacts"},
            ("pipeline", "next"): [frontier(dispatch=["S7-T1"]),
                                   frontier(blocked=["S7-T1"], stage_done=True)],
        })
        rt = self.rt(cli)
        stages.run_stage(rt)
        self.assertEqual(rt.state.phase, "designing")

    def test_the_stage_cap_ships_a_hardening_stretch_that_never_lands_one(self):
        """An enabler run can go many stages without a scenario, and unbounded is the
        review-load failure the cadence rule exists to prevent."""
        self.cfg = driver_config.load(str(support.write_config(
            self.root, max_stages_per_sprint=2)))
        rt = self.rt(self.happy_cli())
        rt.state.stages_shipped = 1
        stages.run_stage(rt)
        self.assertEqual(rt.state.stages_shipped, 2)
        self.assertEqual(rt.state.phase, "closeout")

    def test_a_pending_stop_ships_the_sprint_in_flight_rather_than_cutting_again(self):
        self.cfg.stop_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg.stop_file.write_text("")
        rt = self.rt(self.happy_cli())
        stages.run_stage(rt)
        self.assertEqual(rt.state.stop_pending, "manual_stop")
        self.assertEqual(rt.state.phase, "closeout")

    def test_the_close_records_the_stages_width(self):
        """Width cannot be gated — a lower bound would be wrong — so it is watched: a
        trend toward one task per stage means design dispatches are compounding."""
        import events
        self.write_stage(tasks=[support.task("S7-T1"), support.task("S7-T2")])
        cli = self.blocked_cli()
        rt = self.rt(cli, git=fakes.FakeGit(),
                     telemetry=events.Telemetry(self.cfg))
        stages.run_stage(rt)
        rows = [__import__("json").loads(x) for x in
                self.cfg.path("telemetry").read_text().splitlines() if x.strip()]
        done = next(r for r in rows if r["event"] == "stage_done")
        self.assertEqual(done["stage"], 7)
        self.assertEqual(done["width"], 2)
        self.assertEqual(done["merged"], 1)


if __name__ == "__main__":
    unittest.main()
