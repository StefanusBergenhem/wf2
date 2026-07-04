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

import yaml  # noqa: E402

import common  # noqa: E402, F401
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
    """Records mutations; tracks approved/completed/blocked + design issues and the
    current stage; answers .json() queries."""

    def __init__(self):
        self.calls = []
        self.approved = set()
        self.completed = set()
        self.blocked = set()
        self.rejected = []
        self.recorded_dis = []
        self.dis = {}          # di_id -> {"task_id": ..., "status": open|resolved}
        self.resolved_dis = []
        self.attempt_counters = {}   # task_id -> recorded attempt (bumped on reject)
        self.stage = 1
        self.total_stages = 1

    def run(self, subcmd, *args, soft=False):
        self.calls.append((subcmd, args))
        if subcmd == "approve_task":
            self.approved.add(args[0])
        elif subcmd == "complete_task":
            self.completed.add(args[0])
        elif subcmd == "reject_task":
            self.rejected.append(args[0])
            self.attempt_counters[args[0]] = self.attempt_counters.get(args[0], 0) + 1
        elif subcmd == "block_task":
            self.blocked.add(args[0])
        elif subcmd == "record_design_issue":
            self.recorded_dis.append(args[0])
            tid = args[args.index("--task") + 1] if "--task" in args else None
            self.dis[args[0]] = {"task_id": tid, "status": "open"}
        elif subcmd == "resolve_design_issue":
            self.resolved_dis.append(args[0])
            self.dis.setdefault(args[0], {})["status"] = "resolved"
        return _proc()

    def json(self, subcmd, *args):
        if subcmd == "advance_stage":
            if self.stage < self.total_stages:
                self.stage += 1
                return {"advanced": True}
            return {"advanced": False}
        if subcmd == "task_state":
            return {"build_commit": "bsha"}
        if subcmd == "unresolved_design_issues":
            return {"issues": [
                {"di_id": k, "task_id": v.get("task_id"), "status": "open"}
                for k, v in self.dis.items() if v.get("status") == "open"]}
        if subcmd == "scope_amendment_count":
            return {"value": 0}
        if subcmd == "attempt_counter":
            return {"value": self.attempt_counters.get(args[0], 0)}
        return {}


class FakeBrain:
    """Staged frontier driven by FakeState.approved: one stage, all the given tasks."""

    def __init__(self, state, tasks):
        self.state = state
        self.tasks = tasks

    def next(self):
        remaining = [t for t in self.tasks if t not in self.state.approved]
        approved = sorted(self.state.approved)
        dispatch = [{"task_id": t,
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
    They default to a steady 'ready_for_review' / 'approved'. ``classify`` is the
    classify-amendment verdict; dispatch-fix routes every DI to wf-swa (exit 0)."""

    def __init__(self, review=None, build=None, classify="feature"):
        self.review = review or {}
        self.build = build or {}
        self.classify = classify

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
        if verb == "classify-amendment":
            return _proc(self.classify + "\n")
        if verb == "dispatch-fix":
            # Faithful to the real helper: an unknown or already-resolved DI in the
            # HOST design-issues artifact is exit 2 (no routing decision to make).
            di_id = args[0]
            if "--config" in args:
                cfg = Path(args[args.index("--config") + 1])
                host = cfg.parent / "transient" / "design-issues.yaml"
                doc = yaml.safe_load(host.read_text()) if host.exists() else {}
                entry = next((i for i in (doc or {}).get("issues") or []
                              if i.get("id") == di_id), None)
                if entry is None or entry.get("status") in ("resolved", "overridden"):
                    return _proc("", rc=2)
            return _proc(json.dumps({"di_id": di_id, "subagent_type": "wf-swa",
                                     "human_gate": False,
                                     "envelope": {"mode": "fix", "di_id": di_id}}))
        return _proc()


class FakeGit:
    def __init__(self):
        self.merges = []
        self.pushes = []
        self.prs = []

    def ensure_branch(self, b, base):
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
        self.prs.append((title, head, base, body))


class ScriptedGit(driver.GitOps):
    """Real GitOps logic over a scripted `_git`: the first matching command prefix
    returns its CompletedProcess; anything unscripted succeeds empty. Records calls."""

    def __init__(self, script):
        super().__init__("/nonexistent")
        self.script = script  # list of (args_prefix_tuple, CompletedProcess)
        self.calls = []

    def _git(self, *args, cwd=None):
        self.calls.append(args)
        for prefix, proc in self.script:
            if args[: len(prefix)] == prefix:
                return proc
        return _proc()


class StagedDIBrain:
    """Stage-aware frontier over FakeState: a task with an open DI parks as repairing,
    a blocked task drops out, approved tasks settle the stage. ``worktrees`` maps a
    task id to the (absolute) worktree the dispatch entry should carry."""

    def __init__(self, state, stages, worktrees):
        self.state = state
        self.stages = stages
        self.worktrees = worktrees
        state.total_stages = len(stages)

    def next(self):
        cur = self.state.stage
        tasks = list(self.stages[cur - 1])
        open_di = {v.get("task_id") for v in self.state.dis.values()
                   if v.get("status") == "open"}
        settled = self.state.approved | self.state.completed | self.state.blocked
        repairing = [t for t in tasks if t in open_di and t not in settled]
        pending = [t for t in tasks if t not in settled and t not in open_di]
        dispatch = [{"task_id": t, "worktree": self.worktrees[t]}
                    for t in pending]
        stage_done = not pending
        return {
            "stage": {"index": cur, "total": len(self.stages), "tasks": tasks},
            "dispatch": dispatch, "ready": [], "in_flight": [],
            "approved": sorted(t for t in tasks if t in self.state.approved),
            "repairing": repairing, "escalated": [],
            "blocked": sorted(t for t in tasks if t in self.state.blocked),
            "terminal": {"stage_done": stage_done,
                         "sprint_done": stage_done and cur >= len(self.stages),
                         "halt": None},
        }


class HaltBrain:
    """Immediately reports a structural halt."""

    def next(self):
        return {
            "stage": None, "dispatch": [], "ready": [], "in_flight": [],
            "approved": [], "repairing": [], "escalated": [], "blocked": [],
            "terminal": {"stage_done": False, "sprint_done": False,
                         "halt": {"reason": "test halt", "blocked": []}},
        }


def make_orch(proj, tasks, review=None, build=None, classify="feature",
              brain=None, dispatcher=None):
    state = FakeState()
    git = FakeGit()
    dispatcher = dispatcher or FakeDispatcher()
    orch = driver.Orchestrator(
        str(proj / ".wf" / "config.yaml"),
        dispatcher,
        brain=brain(state) if brain else FakeBrain(state, tasks),
        scripts=FakeScripts(review, build, classify),
        state=state,
        git=git,
        write_task=lambda tid, dest: None,
    )
    return orch, state, git, dispatcher


def seed_worktree(proj, name, di=None, build_blocked=None, current_task=None):
    """Create a fake worktree dir with the given artifacts; returns its path."""
    wt = proj / "worktrees" / name
    (wt / ".wf" / "transient").mkdir(parents=True, exist_ok=True)
    if di is not None:
        (wt / ".wf" / "transient" / "design-issues.yaml").write_text(yaml.safe_dump(di))
    if build_blocked is not None:
        (wt / ".wf" / "transient" / "build-blocked.yaml").write_text(yaml.safe_dump(build_blocked))
    if current_task is not None:
        (wt / ".wf" / "transient" / "current-task.yaml").write_text(yaml.safe_dump(current_task))
    return wt


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
    outcome = asyncio.run(orch._build_pass_loop("T1", ".wf/transient/worktrees/sprint-T1", "sprint/test"))

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
    outcome = asyncio.run(orch._build_pass_loop("T1", ".wf/transient/worktrees/sprint-T1", "sprint/test"))

    if outcome.status == "design_issue" and state.recorded_dis == ["DI-2"]:
        ok("review DI: review-boundary design_issue recorded + parks the task")
    else:
        bad("review DI", f"status={outcome.status} recorded={state.recorded_dis}")
    if "T1" not in state.approved:
        ok("review DI: task not approved on a review design issue")
    else:
        bad("review DI not approved", state.approved)


def test_review_envelope_carries_sprint_branch():
    # wf-review runs in a worktree that cannot see the gitignored host pipeline_state,
    # so its diff base (the sprint branch) must travel in the dispatch envelope.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(proj, ["T1"])
    asyncio.run(orch.run())

    review_calls = dispatcher.calls_for_agent("wf-review")
    if not review_calls:
        bad("review envelope sprint_branch", "no wf-review dispatch recorded")
        return
    env = json.loads(review_calls[0].envelope)
    if env.get("sprint_branch"):
        ok("review envelope carries sprint_branch (worktree diff base)")
    else:
        bad("review envelope sprint_branch", f"envelope missing sprint_branch: {env}")


def test_build_blocked_string_required_files_amends():
    # The canonical build_blocked artifact lists required_files as plain strings; a
    # mechanical_follow_on classification widens the scope, removes the artifact, and
    # re-dispatches the build.
    proj = make_project(["T1"])
    wt = seed_worktree(
        proj, "sprint-T1",
        build_blocked={"task_id": "T1", "required_files": ["src/extra.py"],
                       "reason": "needs the helper"},
        current_task={"task_id": "T1", "files_to_touch": ["src/main.py"]})
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"], build={"T1": ["build_blocked"]}, classify="mechanical_follow_on")
    outcome = asyncio.run(orch._run_task({"task_id": "T1", "worktree": str(wt)}, "sprint/test"))

    if outcome.status == "approved":
        ok("build_blocked: string required_files → amended, task completes")
    else:
        bad("build_blocked amend outcome", f"{outcome.status} {outcome.reason}")
    ct = yaml.safe_load((wt / ".wf" / "transient" / "current-task.yaml").read_text())
    if "src/extra.py" in (ct.get("files_to_touch") or []):
        ok("build_blocked: required file appended to files_to_touch")
    else:
        bad("build_blocked files_to_touch", ct)
    if not (wt / ".wf" / "transient" / "build-blocked.yaml").exists():
        ok("build_blocked: artifact removed after the amendment")
    else:
        bad("build_blocked artifact removal", "still present")
    if dispatcher.agents_dispatched().count("wf-build") == 2:
        ok("build_blocked: build re-dispatched after the amendment")
    else:
        bad("build_blocked re-dispatch", dispatcher.agents_dispatched())


def test_build_blocked_reject_escalates():
    proj = make_project(["T1"])
    wt = seed_worktree(
        proj, "sprint-T1",
        build_blocked={"task_id": "T1", "required_files": ["src/extra.py"],
                       "reason": "wants a feature"},
        current_task={"task_id": "T1", "files_to_touch": ["src/main.py"]})
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"], build={"T1": ["build_blocked"]}, classify="reject")
    outcome = asyncio.run(orch._run_task({"task_id": "T1", "worktree": str(wt)}, "sprint/test"))

    if outcome.status == "escalated" and "T1" in state.blocked:
        ok("build_blocked: reject classification escalates the task")
    else:
        bad("build_blocked reject", f"status={outcome.status} blocked={state.blocked}")
    if dispatcher.agents_dispatched().count("wf-build") == 1:
        ok("build_blocked: no re-dispatch on reject")
    else:
        bad("build_blocked reject re-dispatch", dispatcher.agents_dispatched())


def _resolving_fixer(proj):
    """Dispatcher callback simulating a fix agent that resolves its DI in the HOST
    design-issues artifact."""
    def fixer(call):
        if call.agent_name not in ("wf-swa", "wf-sa"):
            return
        env = json.loads(call.envelope)
        if env.get("mode") != "fix":
            return
        host = proj / ".wf" / "transient" / "design-issues.yaml"
        doc = yaml.safe_load(host.read_text()) or {}
        for issue in doc.get("issues") or []:
            if issue.get("id") == env.get("di_id"):
                issue["status"] = "resolved"
        host.write_text(yaml.safe_dump(doc))
    return fixer


def test_design_issues_across_two_stages():
    # Regression: a DI in stage 1 and another in stage 2, both fixer-resolved, must
    # complete without halting (the state DI is marked resolved so the stage-2
    # boundary does not re-route stage 1's issue).
    proj = make_project(["T1", "T2"])
    wt1 = seed_worktree(proj, "sprint-T1", di={"issues": [
        {"id": "DI-1", "task_id": "T1", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    wt2 = seed_worktree(proj, "sprint-T2", di={"issues": [
        {"id": "DI-2", "task_id": "T2", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    wts = {"T1": str(wt1), "T2": str(wt2)}
    orch, state, git, dispatcher = make_orch(
        proj, ["T1", "T2"],
        build={"T1": [{"verdict": "design_issue", "di_id": "DI-1"}],
               "T2": [{"verdict": "design_issue", "di_id": "DI-2"}]},
        brain=lambda st: StagedDIBrain(st, [["T1"], ["T2"]], wts),
        dispatcher=FakeDispatcher(on_dispatch=_resolving_fixer(proj)))
    try:
        result = asyncio.run(orch.run())
    except Exception as exc:  # noqa: BLE001 — the regression under test is a halt
        bad("two-stage DI: run halted", repr(exc))
        return

    if sorted(result.completed) == ["T1", "T2"] and result.halted is None:
        ok("two-stage DI: both tasks complete, run does not halt")
    else:
        bad("two-stage DI completion", result.as_dict())
    if state.resolved_dis == ["DI-1", "DI-2"]:
        ok("two-stage DI: both DIs marked resolved in pipeline state")
    else:
        bad("two-stage DI resolve calls", state.resolved_dis)
    if not result.escalated:
        ok("two-stage DI: nothing escalated")
    else:
        bad("two-stage DI escalations", result.escalated)


def test_fixer_leaves_di_open_escalates():
    # A fix agent that HALTs leaves the DI open in the host artifact: the task must
    # be blocked + escalated, NOT re-run (which would reproduce the same DI forever).
    proj = make_project(["T1"])
    wt1 = seed_worktree(proj, "sprint-T1", di={"issues": [
        {"id": "DI-1", "task_id": "T1", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"],
        build={"T1": [{"verdict": "design_issue", "di_id": "DI-1"}]},
        brain=lambda st: StagedDIBrain(st, [["T1"]], {"T1": str(wt1)}))
    result = asyncio.run(orch.run())

    if any(t == "T1" for t, _ in result.escalated) and "T1" in state.blocked:
        ok("fixer-open DI: task blocked + escalated")
    else:
        bad("fixer-open DI escalation", f"escalated={result.escalated} blocked={state.blocked}")
    if dispatcher.agents_dispatched().count("wf-build") == 1:
        ok("fixer-open DI: task NOT re-dispatched")
    else:
        bad("fixer-open DI re-dispatch", dispatcher.agents_dispatched())
    if state.dis.get("DI-1", {}).get("status") == "open":
        ok("fixer-open DI: state DI left open")
    else:
        bad("fixer-open DI state", state.dis)


def test_ensure_branch_gates():
    # Resume: the sprint branch already exists → skip creation, run no gates.
    g = ScriptedGit([(("rev-parse", "--verify", "sprint/s1"), _proc(rc=0))])
    g.ensure_branch("sprint/s1", "main")
    if not any(c[0] in ("checkout", "status") for c in g.calls):
        ok("ensure_branch: existing branch → resume, no gates, no creation")
    else:
        bad("ensure_branch resume", g.calls)

    # Gate 1: dirty working tree → Escalation, no branch created.
    g = ScriptedGit([
        (("rev-parse", "--verify"), _proc(rc=1)),
        (("status", "--porcelain"), _proc(" M x.py\n")),
    ])
    try:
        g.ensure_branch("sprint/s1", "main")
        bad("ensure_branch dirty tree", "no Escalation raised")
    except driver.Escalation:
        if not any(c[0] == "checkout" for c in g.calls):
            ok("ensure_branch: dirty tree → Escalation, no branch created")
        else:
            bad("ensure_branch dirty tree created branch", g.calls)

    # Gate 2: base behind its remote → Escalation.
    g = ScriptedGit([
        (("rev-parse", "--verify"), _proc(rc=1)),
        (("status", "--porcelain"), _proc("")),
        (("rev-list", "--count", "main..origin/main"), _proc("2\n")),
    ])
    try:
        g.ensure_branch("sprint/s1", "main")
        bad("ensure_branch base behind", "no Escalation raised")
    except driver.Escalation as exc:
        if "behind its remote" in exc.reason:
            ok("ensure_branch: base behind its remote → Escalation")
        else:
            bad("ensure_branch base behind reason", exc.reason)

    # No upstream: rev-list fails → skip the gate cleanly, create from the base.
    g = ScriptedGit([
        (("rev-parse", "--verify"), _proc(rc=1)),
        (("status", "--porcelain"), _proc("")),
        (("rev-list",), _proc("", rc=128)),
    ])
    g.ensure_branch("sprint/s1", "main")
    if ("checkout", "-b", "sprint/s1", "main") in g.calls:
        ok("ensure_branch: no upstream → gate skipped, branch created from base")
    else:
        bad("ensure_branch no upstream", g.calls)

    # Clean + up to date with the remote → create from the base (not current HEAD).
    g = ScriptedGit([
        (("rev-parse", "--verify"), _proc(rc=1)),
        (("status", "--porcelain"), _proc("")),
        (("rev-list", "--count", "main..origin/main"), _proc("0\n")),
    ])
    g.ensure_branch("sprint/s1", "main")
    if ("checkout", "-b", "sprint/s1", "main") in g.calls:
        ok("ensure_branch: clean + current → created with `checkout -b <branch> <base>`")
    else:
        bad("ensure_branch clean create", g.calls)


def test_di_rerun_at_current_attempt():
    # A fix-resolved task re-runs at its CURRENT attempt (review.max_attempts is
    # per-task, not per-incarnation) — never restarted at 1.
    proj = make_project(["T1"])
    wt1 = seed_worktree(proj, "sprint-T1", di={"issues": [
        {"id": "DI-1", "task_id": "T1", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"],
        build={"T1": [{"verdict": "design_issue", "di_id": "DI-1"}]},
        brain=lambda st: StagedDIBrain(st, [["T1"]], {"T1": str(wt1)}),
        dispatcher=FakeDispatcher(on_dispatch=_resolving_fixer(proj)))
    state.attempt_counters["T1"] = 2  # two review rejections already recorded
    result = asyncio.run(orch.run())

    if result.completed == ["T1"]:
        ok("DI attempt: fix-resolved task completes")
    else:
        bad("DI attempt completion", result.as_dict())
    build_disp = [a for s, a in state.calls if s == "dispatch" and "wf-build" in a]
    last = build_disp[-1] if build_disp else ()
    if last and last[last.index("--attempt") + 1] == "2":
        ok("DI attempt: re-dispatch recorded at the task's current attempt (2)")
    else:
        bad("DI attempt re-dispatch", build_disp)
    review_disp = [a for s, a in state.calls if s == "dispatch" and "wf-review" in a]
    last_r = review_disp[-1] if review_disp else ()
    if last_r and last_r[last_r.index("--attempt") + 1] == "2":
        ok("DI attempt: the re-run's review loop starts from the current attempt")
    else:
        bad("DI attempt review loop", review_disp)


def test_di_rerun_clears_stale_worktree_artifacts():
    # A fresh build after a resolved DI must start in BUILD mode against the amended
    # contract — stale handoff artifacts (feedback, review_ready, build_blocked) in
    # the surviving worktree are removed before the re-dispatch.
    proj = make_project(["T1"])
    wt1 = seed_worktree(proj, "sprint-T1", di={"issues": [
        {"id": "DI-1", "task_id": "T1", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    stale = {
        "feedback": wt1 / ".wf" / "transient" / "feedback.yaml",
        "review_ready": wt1 / ".wf" / "transient" / "review-ready.yaml",
        "build_blocked": wt1 / ".wf" / "transient" / "build-blocked.yaml",
    }
    for p in stale.values():
        p.write_text("task_id: T1\n")
    orch, state, git, dispatcher = make_orch(
        proj, ["T1"],
        build={"T1": [{"verdict": "design_issue", "di_id": "DI-1"}]},
        brain=lambda st: StagedDIBrain(st, [["T1"]], {"T1": str(wt1)}),
        dispatcher=FakeDispatcher(on_dispatch=_resolving_fixer(proj)))
    # point the driver's computed worktree at the seeded one
    orch.cfg.setdefault("parallel", {})["worktree_base"] = str(proj / "worktrees")
    result = asyncio.run(orch.run())

    leftover = [k for k, p in stale.items() if p.exists()]
    if not leftover:
        ok("DI re-run: stale worktree handoff artifacts cleared before re-dispatch")
    else:
        bad("DI re-run stale artifacts", leftover)
    if result.completed == ["T1"]:
        ok("DI re-run: task completes after the cleanup")
    else:
        bad("DI re-run completion", result.as_dict())


def test_ship_pr_body_sections():
    # GIT_OPERATIONS.md § Ship: the PR body lists completed tasks, escalated/blocked
    # tasks, and open design issues — empty sections are omitted.
    proj = make_project(["T1", "T2"])
    wt1 = seed_worktree(proj, "sprint-T1")
    wt2 = seed_worktree(proj, "sprint-T2", di={"issues": [
        {"id": "DI-9", "task_id": "T2", "fix_kind": "contract_amendment",
         "severity": "high", "status": "open"}]})
    # no resolving fixer: DI-9 stays open → T2 blocked + escalated, DI unresolved
    orch, state, git, dispatcher = make_orch(
        proj, ["T1", "T2"],
        build={"T2": [{"verdict": "design_issue", "di_id": "DI-9"}]},
        brain=lambda st: StagedDIBrain(st, [["T1", "T2"]], {"T1": str(wt1), "T2": str(wt2)}))
    asyncio.run(orch.run())

    if not git.prs:
        bad("PR body", "no PR opened")
        return
    body = git.prs[0][3]
    if "Completed tasks:" in body and "- T1" in body:
        ok("PR body: completed tasks listed")
    else:
        bad("PR body completed", body)
    if "Escalated" in body and "T2" in body:
        ok("PR body: escalated/blocked tasks listed with reasons")
    else:
        bad("PR body escalated", body)
    if "Design issues:" in body and "DI-9" in body:
        ok("PR body: open design issues listed")
    else:
        bad("PR body design issues", body)


def test_ship_pr_body_omits_empty_sections():
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(proj, ["T1"])
    asyncio.run(orch.run())

    body = git.prs[0][3] if git.prs else ""
    if "Completed tasks:" in body and "Escalated" not in body and "Design issues:" not in body:
        ok("PR body: empty escalated/design-issue sections omitted")
    else:
        bad("PR body empty sections", body)


def test_build_envelope_has_no_mode():
    # wf-build derives build-vs-fix from paths.feedback presence; the envelope
    # carries no mode field.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(proj, ["T1"])
    asyncio.run(orch.run())

    calls = dispatcher.calls_for_agent("wf-build")
    env = json.loads(calls[0].envelope) if calls else {}
    if env.get("task_id") == "T1" and "mode" not in env:
        ok("build envelope: no dead mode field")
    else:
        bad("build envelope mode", env)


def test_stage_fix_artifacts_schema():
    # The stage-fix contract is schema-shaped (task-contract.md) and its feedback
    # file follows feedback.yaml.tmpl.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(proj, ["T1"])
    wt = proj / "worktrees" / "stage-fix"
    orch._write_fix_contract(str(wt), "STAGE-FIX-sprint", "make check")

    ct = yaml.safe_load((wt / ".wf" / "transient" / "current-task.yaml").read_text())
    acs = ct.get("acceptance_criteria") or []
    if (ct.get("id") == "STAGE-FIX-sprint" and acs
            and acs[0].get("id") == "STAGE-FIX.AC-1"
            and "make check" in acs[0].get("check", "")
            and "exits 0" in acs[0].get("check", "")):
        ok("stage-fix contract: schema-shaped acceptance_criteria with id + check")
    else:
        bad("stage-fix contract ACs", ct)
    if (ct.get("covers") == [] and ct.get("files_to_touch") == []
            and ct.get("out_of_scope")
            and any("make check" in str(n) for n in ct.get("implementation_notes") or [])):
        ok("stage-fix contract: covers/files_to_touch/out_of_scope/implementation_notes present")
    else:
        bad("stage-fix contract fields", ct)

    fb = yaml.safe_load((wt / ".wf" / "transient" / "feedback.yaml").read_text())
    fails = fb.get("failures") or []
    f0 = fails[0] if fails else {}
    if (fb.get("task_id") == "STAGE-FIX-sprint"
            and f0.get("type") == "acceptance_criteria_unmet"
            and "make check" in f0.get("detail", "")
            and f0.get("required_action") == "make the stage check pass"
            and "file" in f0):
        ok("stage-fix feedback: follows feedback.yaml.tmpl (task_id + typed failure)")
    else:
        bad("stage-fix feedback shape", fb)


def test_record_session_on_halt():
    # Telemetry fires on EVERY exit path — a halted run records outcome=halted.
    proj = make_project(["T1"])
    orch, state, git, dispatcher = make_orch(proj, ["T1"], brain=lambda st: HaltBrain())
    result = asyncio.run(orch.run())

    recs = [args for sub, args in state.calls if sub == "record_session"]
    if result.halted and len(recs) == 1 and "halted" in recs[0]:
        ok("telemetry: halted run records one session with outcome=halted")
    else:
        bad("telemetry halt record", f"halted={result.halted} recs={recs}")


if __name__ == "__main__":
    test_happy_path()
    test_reject_then_rebuild()
    test_build_design_issue()
    test_review_design_issue()
    test_review_envelope_carries_sprint_branch()
    test_build_blocked_string_required_files_amends()
    test_build_blocked_reject_escalates()
    test_design_issues_across_two_stages()
    test_fixer_leaves_di_open_escalates()
    test_ensure_branch_gates()
    test_di_rerun_at_current_attempt()
    test_di_rerun_clears_stale_worktree_artifacts()
    test_ship_pr_body_sections()
    test_ship_pr_body_omits_empty_sections()
    test_build_envelope_has_no_mode()
    test_stage_fix_artifacts_schema()
    test_record_session_on_halt()
    print(f"\n  driver: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
