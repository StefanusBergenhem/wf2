#!/usr/bin/env python3
"""Tests for the staged Python driver. Injects fakes for every seam (brain, helpers,
state, git, dispatcher) so it needs no real CLI, git, or SDK. Run:
  <venv>/bin/python tools/cli/orchestrator/driver_test.py   (exit 0 = all pass)
wf2-source-only — never rendered into an install target."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLI = HERE.parent
sys.path.insert(0, str(CLI))

import common  # noqa: E402
from orchestrator import driver  # noqa: E402
from orchestrator.dispatch import FakeDispatcher  # noqa: E402

PASS = 0
FAIL = 0


def ok(msg):
    global PASS
    PASS += 1
    print(f"  ok   - {msg}")


def bad(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL - {msg}\n         {detail}")


def _proc(stdout="", rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def make_project(tasks):
    proj = Path(tempfile.mkdtemp())
    (proj / ".wf" / "transient").mkdir(parents=True)
    (proj / ".wf" / "config.yaml").write_text(
        "version: 1\n"
        "project: {base_branch: main}\n"
        "paths:\n"
        "  sprint: .wf/transient/sprint.yaml\n"
        "  pipeline_state: .wf/transient/pipeline-state.yaml\n"
        "  design_issues: .wf/transient/design-issues.yaml\n"
        "  current_task: .wf/transient/current-task.yaml\n"
        "  review_ready: .wf/transient/review-ready.yaml\n"
        "  feedback: .wf/transient/feedback.yaml\n"
        "  build_blocked: .wf/transient/build-blocked.yaml\n"
        "parallel: {max_concurrent_tasks: 4, worktree_base: .wf/transient/worktrees}\n"
        "review: {passes: [wf-review], max_attempts: 3, max_scope_amendments: 1}\n"
        "commands: {preflight: '', stage_check: ''}\n"
        "closeout: [wf-retrospective, ship]\n"
        "orchestrate: {history_cap: 50}\n"
    )
    (proj / ".wf" / "transient" / "sprint.yaml").write_text(
        "sprint: {summary: test}\ntasks:\n" + "".join(f"  - {{id: {t}}}\n" for t in tasks)
    )
    return proj


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeState:
    """Records mutations; tracks approved/completed; answers .json() queries."""

    def __init__(self):
        self.calls = []
        self.approved = set()
        self.completed = set()
        self.rejected = []
        self.recorded_dis = []

    def run(self, subcmd, *args, soft=False):
        self.calls.append((subcmd, args))
        if subcmd == "approve_task":
            self.approved.add(args[0])
        elif subcmd == "complete_task":
            self.completed.add(args[0])
        elif subcmd == "reject_task":
            self.rejected.append(args[0])
        elif subcmd == "record_design_issue":
            self.recorded_dis.append(args[0])
        return _proc()

    def json(self, subcmd, *args):
        if subcmd == "advance_stage":
            return {"advanced": False}  # single-stage fixture → last stage
        if subcmd == "task_state":
            return {"build_commit": "bsha"}
        if subcmd == "unresolved_design_issues":
            return {"issues": []}
        if subcmd == "scope_amendment_count":
            return {"value": 0}
        return {}


class FakeBrain:
    """Staged frontier driven by FakeState.approved: one stage, all the given tasks."""

    def __init__(self, state, tasks):
        self.state = state
        self.tasks = tasks

    def next(self):
        remaining = [t for t in self.tasks if t not in self.state.approved]
        approved = sorted(self.state.approved)
        dispatch = [{"task_id": t, "mode": "build",
                     "worktree": f".wf/transient/worktrees/sprint-{t}"} for t in remaining]
        stage_done = not remaining
        return {
            "stage": {"index": 1, "total": 1, "tasks": list(self.tasks)},
            "dispatch": dispatch, "ready": [], "in_flight": [], "approved": approved,
            "repairing": [], "escalated": [], "blocked": [],
            "terminal": {"stage_done": stage_done, "sprint_done": stage_done, "halt": None},
        }


class FakeScripts:
    """Returns scripted helper verdicts. ``build[tid]`` / ``review[tid]`` are per-task
    verdict queues; each entry is a bare verdict string or a dict ``{verdict, di_id}``.
    They default to a steady 'ready_for_review' / 'approved'."""

    def __init__(self, review=None, build=None):
        self.review = review or {}
        self.build = build or {}

    @staticmethod
    def _norm(entry, default):
        if entry is None:
            return {"verdict": default}
        if isinstance(entry, str):
            return {"verdict": entry}
        return dict(entry)

    def run(self, verb, *args):
        if verb == "preserve-uncommitted":
            return _proc("clean")
        if verb == "inspect-build-return":
            tid = args[1]
            q = self.build.get(tid)
            v = self._norm(q.pop(0) if q else None, "ready_for_review")
            return _proc(json.dumps({"task_id": tid, "verdict": v["verdict"],
                                     "artifact": v.get("artifact"), "di_id": v.get("di_id")}))
        if verb == "inspect-review-return":
            tid = args[1]
            q = self.review.get(tid)
            v = self._norm(q.pop(0) if q else None, "approved")
            return _proc(json.dumps({"task_id": tid, "verdict": v["verdict"], "head_sha": "h",
                                     "build_commit_sha": args[2], "artifact": None,
                                     "di_id": v.get("di_id")}))
        return _proc()


class FakeGit:
    def __init__(self):
        self.merges = []
        self.pushes = []
        self.prs = []

    def ensure_branch(self, b):
        pass

    def add_worktree(self, wt, br, base):
        pass

    def remove_worktree(self, wt):
        pass

    def head_sha(self, wt):
        return "bsha"

    def diff(self, wt):
        return ""

    def merge(self, branch, into):
        self.merges.append((branch, into))
        return "msha"

    def push(self, branch):
        self.pushes.append(branch)

    def open_pr(self, title, body, head, base=None):
        self.prs.append((title, head, base))


def make_orch(proj, tasks, review=None, build=None):
    state = FakeState()
    git = FakeGit()
    dispatcher = FakeDispatcher()
    orch = driver.Orchestrator(
        str(proj / ".wf" / "config.yaml"),
        dispatcher,
        brain=FakeBrain(state, tasks),
        scripts=FakeScripts(review, build),
        state=state,
        git=git,
        write_task=lambda tid, dest: None,
    )
    return orch, state, git, dispatcher


# ── tests ──────────────────────────────────────────────────────────────────

def test_happy_path():
    proj = make_project(["T1", "T2"])
    orch, state, git, dispatcher = make_orch(proj, ["T1", "T2"])
    result = asyncio.run(orch.run())

    if sorted(result.completed) == ["T1", "T2"]:
        ok("happy: both tasks completed")
    else:
        bad("happy completed", result.completed)
    if state.approved == {"T1", "T2"}:
        ok("happy: both tasks approved before the boundary")
    else:
        bad("happy approved", state.approved)
    # batch merge at the boundary: both merged, both completed in state
    if len(git.merges) == 2 and state.completed == {"T1", "T2"}:
        ok("happy: approved set batch-merged at the stage boundary")
    else:
        bad("happy merges", f"merges={git.merges} completed={state.completed}")
    if result.shipped and git.pushes and git.prs:
        ok("happy: shipped (push + PR)")
    else:
        bad("happy ship", f"shipped={result.shipped} pushes={git.pushes} prs={git.prs}")
    if git.prs and git.prs[0][2] == "main":
        ok("happy: PR opened against the base branch")
    else:
        bad("happy PR base", git.prs)
    # the N-pass chain ran build + the one review pass per task, then retrospective
    agents = dispatcher.agents_dispatched()
    if agents.count("wf-build") >= 2 and agents.count("wf-review") >= 2:
        ok("happy: build + review pass dispatched per task")
    else:
        bad("happy dispatch", agents)
    if "wf-retrospective" in agents:
        ok("happy: closeout dispatched wf-retrospective")
    else:
        bad("happy closeout", agents)


def test_reject_then_rebuild():
    proj = make_project(["T1"])
    # T1's first review rejects, the second approves → build runs twice.
    orch, state, git, dispatcher = make_orch(proj, ["T1"], review={"T1": ["rejected", "approved"]})
    result = asyncio.run(orch.run())

    if state.rejected == ["T1"]:
        ok("reject: reject_task recorded once")
    else:
        bad("reject reject_task", state.rejected)
    if dispatcher.agents_dispatched().count("wf-build") == 2:
        ok("reject: build re-dispatched after rejection (fix mode)")
    else:
        bad("reject rebuild", dispatcher.agents_dispatched())
    if result.completed == ["T1"] and len(git.merges) == 1:
        ok("reject: task completes + merges after the fix")
    else:
        bad("reject complete", f"completed={result.completed} merges={git.merges}")


def test_build_design_issue():
    # A design issue surfaced at the BUILD boundary parks the task — recorded with its
    # di_id, and never sent on to review.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"], build={"T1": [{"verdict": "design_issue", "di_id": "DI-1"}]})
    outcome = asyncio.run(orch._build_pass_loop("T1", ".wf/transient/worktrees/sprint-T1"))

    if outcome.status == "design_issue":
        ok("build DI: build-boundary design_issue parks the task")
    else:
        bad("build DI status", outcome.status)
    if state.recorded_dis == ["DI-1"]:
        ok("build DI: recorded with its di_id")
    else:
        bad("build DI recorded", state.recorded_dis)
    if "wf-review" not in dispatcher.agents_dispatched():
        ok("build DI: no review dispatched on a build design issue")
    else:
        bad("build DI no review", dispatcher.agents_dispatched())


def test_review_design_issue():
    # A design issue surfaced at the REVIEW boundary parks the task too — recorded, not
    # approved.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"], review={"T1": [{"verdict": "design_issue", "di_id": "DI-2"}]})
    outcome = asyncio.run(orch._build_pass_loop("T1", ".wf/transient/worktrees/sprint-T1"))

    if outcome.status == "design_issue" and state.recorded_dis == ["DI-2"]:
        ok("review DI: review-boundary design_issue recorded + parks the task")
    else:
        bad("review DI", f"status={outcome.status} recorded={state.recorded_dis}")
    if "T1" not in state.approved:
        ok("review DI: task not approved on a review design issue")
    else:
        bad("review DI not approved", state.approved)


if __name__ == "__main__":
    test_happy_path()
    test_reject_then_rebuild()
    test_build_design_issue()
    test_review_design_issue()
    print(f"\n  driver: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
