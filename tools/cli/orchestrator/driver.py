"""The standalone Python pipeline driver — the headless twin of the wf-orchestrate
skill. It holds no scheduling logic: it asks the brain (`wf pipeline next`) what to
dispatch, runs each task's build→review chain in its worktree, and follows the same
staged machine the skill follows — start_of_stage → running_stage → end_of_stage
(batch-merge the approved set, resolve design issues at the boundary) → end_of_sprint.

The verdict of every dispatch is read back from on-disk artifacts via the inspect
verbs, never returned from the dispatch call — the same contract the skill uses, so the
two drivers behave identically.

Every external effect goes through an injectable seam (BrainRunner, OrchestrateRunner,
StateRunner, GitOps, Dispatcher); the defaults shell out to the real CLI / git, the
test suite injects fakes so it needs no real git, gh, or SDK.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import common

from .dispatch import Dispatcher

AGENT_BUILD = "wf-build"
AGENT_SWA = "wf-swa"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Escalation(Exception):
    """A branch the driver cannot resolve autonomously (build_blocked denied, merge
    conflict, ambiguous review, max-attempts, human-gated DI, heavy-check failure).
    Carries a task id when applicable; the outer loop collects these instead of
    crashing so other tasks keep running."""

    def __init__(self, reason: str, task_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.task_id = task_id


@dataclass
class TaskOutcome:
    task_id: str
    status: str  # "approved" | "design_issue" | "escalated"
    reason: str | None = None


@dataclass
class RunResult:
    completed: list[str] = field(default_factory=list)        # merged at a stage boundary
    escalated: list[tuple[str, str]] = field(default_factory=list)
    halted: str | None = None
    shipped: bool = False

    def as_dict(self) -> dict:
        return {
            "completed": self.completed,
            "escalated": [{"task_id": t, "reason": r} for t, r in self.escalated],
            "halted": self.halted,
            "shipped": self.shipped,
        }


# ── Injectable seams ─────────────────────────────────────────────────────────

class BrainRunner:
    """Runs `wf pipeline next --format json` and parses it."""

    def __init__(self, config_path: str, wf_entry: str):
        self.config_path = config_path
        self.wf_entry = wf_entry

    def next(self) -> dict:
        proc = subprocess.run(
            [sys.executable, self.wf_entry, "pipeline", "next",
             "--format", "json", "--config", self.config_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise Escalation(f"`wf pipeline next` failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)


class OrchestrateRunner:
    """Invokes the `wf orchestrate` verbs (inspect / preserve / classify / dispatch-fix).
    No `--config` is auto-appended: the worktree-scoped verbs read the worktree's own
    config from their positional path arg; host-side verbs take `--config` from the call
    site."""

    def __init__(self, wf_entry: str):
        self.wf_entry = wf_entry

    def run(self, verb: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, self.wf_entry, "orchestrate", verb, *map(str, args)],
            capture_output=True, text=True,
        )


class StateRunner:
    """Invokes the wf run-state verbs: `wf pipeline <verb>` for mutations, `wf telemetry
    <verb>` for the session record. Soft-failing for telemetry; hard verbs raise on a
    non-zero exit."""

    _TELEMETRY = {"record_session"}

    def __init__(self, wf_entry: str, config_path: str):
        self.wf_entry = wf_entry
        self.config_path = config_path

    def run(self, subcmd: str, *args: str, soft: bool = False) -> subprocess.CompletedProcess:
        noun = "telemetry" if subcmd in self._TELEMETRY else "pipeline"
        verb = subcmd.replace("_", "-")
        proc = subprocess.run(
            [sys.executable, self.wf_entry, noun, verb, *map(str, args),
             "--config", self.config_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 and not soft:
            raise Escalation(f"wf {noun} {verb} failed: {proc.stderr.strip()}")
        return proc

    def json(self, subcmd: str, *args: str) -> dict:
        proc = self.run(subcmd, *args, "--format", "json", soft=True)
        try:
            return json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            return {}


class GitOps:
    """The git/gh side-effects (sprint branch, worktrees, merge, push, PR). Injectable
    so tests run without a real repo."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def _git(self, *args: str, cwd: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo_root, capture_output=True, text=True
        )

    def ensure_branch(self, branch: str, base: str) -> None:
        # Resume: the sprint branch already exists — no gates, no creation.
        if self._git("rev-parse", "--verify", branch).returncode == 0:
            return
        # Gates (GIT_OPERATIONS.md § Sprint branch): clean tree, base not behind
        # its remote — then create from the base, never from the current HEAD.
        if (self._git("status", "--porcelain").stdout or "").strip():
            raise Escalation(f"working tree not clean; cannot create sprint branch {branch}")
        behind = self._git("rev-list", "--count", f"{base}..origin/{base}")
        # A failing rev-list means no remote/upstream for the base — skip the gate.
        if behind.returncode == 0 and int((behind.stdout or "0").strip() or 0) > 0:
            raise Escalation(f"base branch {base} is behind its remote; "
                             f"cannot create sprint branch {branch}")
        r = self._git("checkout", "-b", branch, base)
        if r.returncode != 0:
            raise Escalation(f"could not create sprint branch {branch}: {r.stderr.strip()}")

    def add_worktree(self, worktree: str, branch: str, base: str) -> None:
        # Idempotent: a reclaimed task reuses a worktree that survived an interrupted
        # run — but only after the staleness check (GIT_OPERATIONS.md § Worktree). The
        # sprint branch must be an ancestor of the surviving worktree's HEAD; non-zero
        # exit means the sprint branch moved past its base → remove the worktree and
        # its task branch, then re-add fresh off the sprint branch. A re-dispatched
        # build restarts from zero (it re-reads its contract), so nothing in the stale
        # worktree is worth keeping.
        if Path(worktree).exists():
            if self._git("merge-base", "--is-ancestor", base, "HEAD",
                         cwd=worktree).returncode == 0:
                return  # same base → reuse as-is
            self._git("worktree", "remove", "--force", worktree)
            self._git("branch", "-D", branch)
        Path(worktree).parent.mkdir(parents=True, exist_ok=True)
        r = self._git("worktree", "add", worktree, "-b", branch, base)
        if r.returncode != 0:
            raise Escalation(f"worktree add failed for {worktree}: {r.stderr.strip()}")

    def head_sha(self, worktree: str) -> str:
        r = self._git("rev-parse", "--short", "HEAD", cwd=worktree)
        if r.returncode != 0:
            raise Escalation(f"could not read HEAD in {worktree}: {r.stderr.strip()}")
        return r.stdout.strip()

    def diff(self, worktree: str) -> str:
        return self._git("diff", "HEAD", cwd=worktree).stdout or ""

    def merge(self, branch: str, into: str) -> str:
        co = self._git("checkout", into)
        if co.returncode != 0:
            raise Escalation(f"could not checkout {into}: {co.stderr.strip()}")
        r = self._git("merge", "--no-ff", "-m", f"{branch}: merge", branch)
        if r.returncode != 0:
            self._git("merge", "--abort")
            raise Escalation(f"merge conflict merging {branch} into {into}")
        return self.head_sha(self.repo_root)

    def remove_worktree(self, worktree: str) -> None:
        self._git("worktree", "remove", "--force", worktree)

    def push(self, branch: str) -> None:
        r = self._git("push", "-u", "origin", branch)
        if r.returncode != 0:
            raise Escalation(f"push failed for {branch}: {r.stderr.strip()}")

    def open_pr(self, title: str, body: str, head: str, base: str | None = None) -> None:
        cmd = ["gh", "pr", "create", "--title", title, "--body", body, "--head", head]
        if base:
            cmd += ["--base", base]
        r = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        if r.returncode != 0:
            raise Escalation(f"gh pr create failed: {r.stderr.strip()}")


# ── The orchestrator ─────────────────────────────────────────────────────────

class Orchestrator:
    """Drives the staged machine over the brain + helpers. All collaborators are
    injectable; the defaults wire the real CLI / git."""

    def __init__(
        self,
        config_path: str,
        dispatcher: Dispatcher,
        *,
        brain: BrainRunner | None = None,
        scripts: OrchestrateRunner | None = None,
        state: StateRunner | None = None,
        git: GitOps | None = None,
        write_task=None,
        wf_entry: str | None = None,
    ):
        self.config_path = config_path
        self.dispatcher = dispatcher

        cfg = common.config_doc(config_path)
        self.cfg = cfg
        self.repo_root = str(common.project_root(config_path))
        self.wf_entry = wf_entry or str(Path(__file__).resolve().parents[1] / "wf")

        self.cap = int((cfg.get("parallel") or {}).get("max_concurrent_tasks") or 4)
        review = cfg.get("review") or {}
        self.passes = list(review.get("passes") or ["wf-review"])
        self.max_attempts = int(review.get("max_attempts") or 3)
        self.max_scope_amendments = int(review.get("max_scope_amendments") or 1)
        self.history_cap = int((cfg.get("orchestrate") or {}).get("history_cap") or 50)

        self.sprint_path = common.resolve_path(config_path, "sprint", None)

        self.brain = brain or BrainRunner(config_path, self.wf_entry)
        self.scripts = scripts or OrchestrateRunner(self.wf_entry)
        self.state = state or StateRunner(self.wf_entry, self.config_path)
        self.git = git or GitOps(self.repo_root)
        self._write_task = write_task or self._default_write_task

        self.sem = asyncio.Semaphore(self.cap)
        self.result = RunResult()
        self._started_at = _now_iso()
        self._started_stages: set[int] = set()

    # --- small helpers -----------------------------------------------------

    def _default_write_task(self, task_id: str, dest: str) -> None:
        proc = subprocess.run(
            [sys.executable, self.wf_entry, "sprint", "task", task_id,
             "--write", dest, "--config", self.config_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise Escalation(f"`wf sprint task {task_id} --write` failed: {proc.stderr.strip()}", task_id)

    def _sprint_id(self) -> str:
        return common.load_yaml(self.sprint_path, optional=True).get("sprint_id") or "sprint"

    def _sprint_branch(self) -> str:
        st = common.load_yaml(common.resolve_path(self.config_path, "pipeline_state", None), optional=True)
        return st.get("sprint_branch") or f"sprint/{self._sprint_id()}"

    def _base_branch(self) -> str:
        return (self.cfg.get("project") or {}).get("base_branch") or "main"

    @staticmethod
    def _branch_for(worktree: str) -> str:
        return Path(worktree).name

    def _worktree_for(self, tid: str) -> str:
        base = (self.cfg.get("parallel") or {}).get("worktree_base") or ".wf/transient/worktrees"
        return f"{base}/{self._sprint_id()}-{tid}"

    def _script_json(self, proc: subprocess.CompletedProcess) -> dict:
        try:
            return json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise Escalation(f"could not parse helper JSON: {exc}: {proc.stdout!r}")

    def _task_state(self, tid: str) -> dict:
        return self.state.json("task_state", tid)

    def _worktree_artifact(self, worktree: str, key: str) -> Path:
        rel = (self.cfg.get("paths") or {}).get(key) or f".wf/transient/{key}.yaml"
        return Path(worktree) / rel

    def _feedback_path(self, worktree: str) -> str:
        return str(self._worktree_artifact(worktree, "feedback"))

    def _build_envelope(self, tid: str, worktree: str) -> str:
        # No mode field: wf-build derives build-vs-fix from paths.feedback presence.
        return json.dumps({"task_id": tid, "worktree": worktree})

    def _review_envelope(self, tid: str, worktree: str, pass_agent: str, sprint_branch: str) -> str:
        # sprint_branch is review's diff base; it must travel in the envelope because the
        # worktree cannot see the gitignored host pipeline_state that records it.
        return json.dumps({"mode": "review", "task_id": tid, "worktree": worktree,
                           "pass": pass_agent, "sprint_branch": sprint_branch})

    def _swa_envelope(self) -> str:
        return json.dumps({"mode": "default"})

    # --- the staged scheduler loop ----------------------------------------

    async def run(self) -> RunResult:
        """Drive the run and record exactly one telemetry session on every exit
        path: completed when it ships, halted when it raises or returns halted,
        escalated when it finished unshipped with escalations."""
        try:
            result = await self._run()
        except Exception:
            self._record_session("halted")
            raise
        if result.shipped:
            outcome = "completed"
        elif result.halted is not None:
            outcome = "halted"
        elif result.escalated:
            outcome = "escalated"
        else:
            outcome = "completed"
        self._record_session(outcome)
        return result

    def _record_session(self, outcome: str) -> None:
        try:
            self.state.run("record_session", "--agent", "wf-orchestrate",
                           "--started-at", self._started_at, "--ended-at", _now_iso(),
                           "--outcome", outcome, soft=True)
        except Exception:  # noqa: BLE001 — telemetry never masks the run's outcome
            pass

    async def _run(self) -> RunResult:
        # Kickoff / resume safety (mirrors wf-orchestrate §0): sweep already-consumed
        # handoffs and reclaim orphaned building/reviewing slots left by an interrupted
        # run, before any preparing step — no attempt bump, an interruption is not a failure.
        self.scripts.run("sweep-transients", "--config", self.config_path)
        self.state.run("reclaim_stale", soft=True)

        # preparing (§1)
        self.state.run("transition", "--to", "preparing", soft=True)
        if not self.sprint_path.exists():
            await asyncio.to_thread(self.dispatcher.dispatch, AGENT_SWA, self._swa_envelope(), None)
            if not self.sprint_path.exists():
                raise Escalation(f"wf-swa did not produce {self.sprint_path}; cannot execute a sprint")
        sprint_branch = self._sprint_branch()
        self.git.ensure_branch(sprint_branch, self._base_branch())
        self.state.run("compute_stages")
        self.state.run("transition", "--to", "running_stage", soft=True)

        running: dict[str, asyncio.Task] = {}
        while True:
            frontier = self.brain.next()
            terminal = frontier.get("terminal") or {}

            if terminal.get("halt"):
                self.result.halted = terminal["halt"].get("reason", "halted")
                await self._drain_running(running)
                self._escalate_blocked(terminal["halt"].get("blocked") or [])
                return self.result

            if not running and terminal.get("stage_done"):
                # Resolve any parked design issues first; only finalize (batch-merge
                # the approved set) once the stage is clean. advance-stage returns
                # False on the last stage → finalize it, then go to end_of_sprint.
                if frontier.get("repairing"):
                    await self._resolve_design_issues(frontier, sprint_branch)
                    continue
                await self._finalize_stage(frontier, sprint_branch)
                if not self._advance_stage():
                    break
                continue

            self._mark_stage_started(frontier)
            for entry in frontier.get("dispatch", []):
                tid = entry["task_id"]
                if tid in running:
                    continue
                self.state.run("dispatch", "--agent", AGENT_BUILD, "--task", tid, "--attempt", "1")
                running[tid] = asyncio.create_task(self._run_task(entry, sprint_branch))

            if running:
                done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
                for dt in done:
                    outcome: TaskOutcome = dt.result()
                    running.pop(outcome.task_id, None)
                    if outcome.status == "escalated":
                        self.result.escalated.append((outcome.task_id, outcome.reason or "escalated"))
            elif not terminal.get("stage_done"):
                raise Escalation(
                    "no dispatchable or in-flight tasks but the stage is not done "
                    f"(repairing={frontier.get('repairing')}, blocked={frontier.get('blocked')})"
                )

        await self._end_of_sprint(sprint_branch)
        return self.result

    def _mark_stage_started(self, frontier: dict) -> None:
        stage = frontier.get("stage") or {}
        n = stage.get("index")
        if n is not None and n not in self._started_stages:
            self.state.run("stage_start", "--stage", str(n), soft=True)
            self._started_stages.add(n)

    async def _drain_running(self, running: dict[str, asyncio.Task]) -> None:
        if not running:
            return
        for r in await asyncio.gather(*running.values(), return_exceptions=True):
            if isinstance(r, TaskOutcome) and r.status == "escalated":
                self.result.escalated.append((r.task_id, r.reason or "escalated"))
        running.clear()

    def _escalate_blocked(self, blocked: list) -> None:
        for b in blocked:
            tid = b if isinstance(b, str) else b.get("task_id", "?")
            self.result.escalated.append((tid, "blocked at halt"))

    def _advance_stage(self) -> bool:
        return bool(self.state.json("advance_stage").get("advanced"))

    # --- the per-task build→review chain ----------------------------------

    async def _run_task(self, entry: dict, sprint_branch: str, start_attempt: int = 1) -> TaskOutcome:
        async with self.sem:
            tid = entry["task_id"]
            worktree = entry["worktree"]
            branch = self._branch_for(worktree)
            try:
                self.git.add_worktree(worktree, branch, sprint_branch)
                self._write_task(tid, str(self._worktree_artifact(worktree, "current_task")))
                return await self._build_pass_loop(tid, worktree, sprint_branch, start_attempt)
            except Escalation as exc:
                self.state.run("block_task", tid, "--reason", f"escalated: {exc.reason}", soft=True)
                return TaskOutcome(tid, "escalated", exc.reason)

    async def _build_pass_loop(self, tid: str, worktree: str, sprint_branch: str,
                               start_attempt: int = 1) -> TaskOutcome:
        attempt = max(1, start_attempt)
        while True:
            # BUILD
            await asyncio.to_thread(self.dispatcher.dispatch, AGENT_BUILD,
                                    self._build_envelope(tid, worktree), worktree)
            await asyncio.to_thread(self._preserve, worktree, tid)
            bres = await asyncio.to_thread(self._inspect_build, worktree, tid)
            bv = bres["verdict"]
            if bv == "ready_for_review":
                pass
            elif bv == "design_issue":
                di = self._record_design_issue(worktree, tid, bres.get("di_id"))
                return TaskOutcome(tid, "design_issue", f"design issue {di}")
            elif bv == "build_blocked":
                if await asyncio.to_thread(self._handle_build_blocked, tid, worktree):
                    continue
                raise Escalation("build_blocked: scope amendment denied or unavailable", tid)
            elif bv == "escalate_no_artifacts":
                raise Escalation("build returned no artifacts", tid)
            else:
                raise Escalation(f"unhandled build verdict {bv!r}", tid)

            build_sha = self.git.head_sha(worktree)

            # REVIEW PASSES (build → passes[0] → … → approve)
            back_to_build = False
            pass_idx = 0
            while pass_idx < len(self.passes):
                pass_agent = self.passes[pass_idx]
                self.state.run("dispatch", "--agent", pass_agent, "--task", tid,
                               "--attempt", str(attempt), "--pass", str(pass_idx), soft=True)
                await asyncio.to_thread(self.dispatcher.dispatch, pass_agent,
                                        self._review_envelope(tid, worktree, pass_agent, sprint_branch), worktree)
                rres = await asyncio.to_thread(self._inspect_review, worktree, tid, build_sha)
                rv = rres["verdict"]

                if rv == "approved":
                    pass_idx += 1
                    continue
                if rv == "redispatch_same_attempt":
                    await asyncio.to_thread(self.dispatcher.dispatch, pass_agent,
                                            self._review_envelope(tid, worktree, pass_agent, sprint_branch), worktree)
                    rres = await asyncio.to_thread(self._inspect_review, worktree, tid, build_sha)
                    rv = rres["verdict"]
                    if rv == "approved":
                        pass_idx += 1
                        continue
                if rv == "design_issue":
                    di = self._record_design_issue(worktree, tid, rres.get("di_id"))
                    return TaskOutcome(tid, "design_issue", f"design issue {di}")
                if rv == "rejected":
                    self.state.run("reject_task", tid, "--feedback", self._feedback_path(worktree))
                    if attempt < self.max_attempts:
                        attempt += 1
                        back_to_build = True
                        break
                    raise Escalation(f"max review attempts ({self.max_attempts}) reached", tid)
                if rv == "defer_to_build_inspector":
                    back_to_build = True
                    break
                if rv == "escalate_ambiguous":
                    raise Escalation("ambiguous review state", tid)
                raise Escalation(f"unhandled review verdict {rv!r}", tid)

            if back_to_build:
                continue

            self.state.run("approve_task", tid, "--commit", build_sha)
            return TaskOutcome(tid, "approved")

    # --- design issues (parked → resolved at the stage boundary) ----------

    def _record_design_issue(self, worktree: str, tid: str, di_id):
        """An inspector returned a `design_issue` verdict carrying <di_id>. Copy that
        entry from the worktree design_issues artifact to the host design_issues file (so
        dispatch-fix finds it) and record it in pipeline_state. Returns the DI id."""
        if not di_id:
            return None
        wt_di = common.load_yaml(self._worktree_artifact(worktree, "design_issues"), optional=True)
        entry = next((i for i in (wt_di.get("issues") or []) if i.get("id") == di_id), None) or {}
        fix_kind = entry.get("fix_kind", "unknown")
        severity = entry.get("severity", "unknown")
        host_di = common.resolve_path(self.config_path, "design_issues", None)
        host = common.load_yaml(host_di, optional=True)
        issues = host.setdefault("issues", []) if isinstance(host, dict) else []
        if not any(i.get("id") == di_id for i in issues):
            issues.append({"id": di_id, "task_id": tid, "fix_kind": fix_kind,
                           "severity": severity, "status": "open"})
            host_di.parent.mkdir(parents=True, exist_ok=True)
            import yaml as _yaml
            host_di.write_text(_yaml.safe_dump({"issues": issues}, sort_keys=False))
        self.state.run("record_design_issue", di_id, "--task", tid,
                       "--severity", severity, "--fix_kind", fix_kind, soft=True)
        return di_id

    async def _resolve_design_issues(self, frontier: dict, sprint_branch: str) -> None:
        """At the stage boundary, route each open design issue via dispatch-fix. A
        resolved fix marks the state DI resolved and re-runs the task — inline for an
        amended contract/spec, via a stage re-layer when the fixer authored a follow-up
        task (component_defect). A fixer that left the DI open but CHANGED its fix_kind
        reclassified it: re-route once to the new kind's subagent. Still open after
        that (or open with an unchanged kind) — block the task, never re-run it. A
        human-gated DI escalates the task."""
        for di in self.state.json("unresolved_design_issues").get("issues", []):
            di_id, tid = di.get("di_id"), di.get("task_id")
            resolved = False
            routed_kind = None
            for hop in range(2):  # the initial route + at most ONE re-route per DI
                proc = await asyncio.to_thread(self.scripts.run, "dispatch-fix", di_id, "--config", self.config_path)
                if proc.returncode == 1:
                    self.state.run("block_task", tid, "--reason", f"design issue {di_id} needs a human", soft=True)
                    self.result.escalated.append((tid, f"design issue {di_id} human gate"))
                    break
                if proc.returncode != 0:
                    raise Escalation(f"dispatch-fix failed for {di_id}: {proc.stderr.strip()}", tid)
                route = self._script_json(proc)
                routed_kind = route.get("fix_kind")
                await asyncio.to_thread(self.dispatcher.dispatch, route.get("subagent_type"),
                                        json.dumps(route.get("envelope") or {}), None)
                if (self._host_di(di_id).get("status") or "open") == "resolved":
                    resolved = True
                    break
                new_kind = self._host_di(di_id).get("fix_kind")
                if hop == 0 and new_kind and new_kind != routed_kind:
                    # The fixer reclassified (flipped fix_kind, left the DI open):
                    # re-run dispatch-fix so the new kind's subagent gets it.
                    continue
                # The fixer HALTed and left the DI open — re-running would reproduce it.
                reason = f"design issue {di_id} fix escalated — left open by the fixer"
                self.state.run("block_task", tid, "--reason", reason, soft=True)
                self.result.escalated.append((tid, reason))
                break
            if not resolved:
                continue
            # Route the resolved branch on the FINAL fix_kind: a fixer may correct the
            # entry's kind (e.g. contract_amendment → component_defect) and act on its
            # own classification in the same dispatch.
            final_kind = self._host_di(di_id).get("fix_kind") or routed_kind
            self.state.run("resolve_design_issue", di_id, soft=True)  # also un-parks the task
            worktree = self._worktree_for(tid)
            # A fresh build must start in build mode against the amended contract —
            # clear stale handoff artifacts left in the surviving worktree. The worktree
            # DI copy is still `open` (the fixer edits the HOST file); left in place it
            # would re-park the re-run at inspect-build-return.
            for key in ("feedback", "review_ready", "build_blocked", "design_issues"):
                try:
                    self._worktree_artifact(worktree, key).unlink()
                except OSError:
                    pass
            if final_kind == "component_defect":
                # The fixer authored a follow-up task in the sprint DAG (the repaired
                # task now depends on it). Re-layer the remaining tasks and return to
                # the loop: the follow-up dispatches first, and the repaired task
                # re-enters in a later stage — after the follow-up merges.
                self.state.run("compute_stages", "--force")
                continue
            # The fix agent amended the contract/spec; re-run the task from build, at
            # its CURRENT attempt (review.max_attempts is per-task, not per-incarnation).
            attempt = max(1, int(self.state.json("attempt_counter", tid).get("value") or 0))
            entry = {"task_id": tid, "worktree": worktree}
            self.state.run("dispatch", "--agent", AGENT_BUILD, "--task", tid,
                           "--attempt", str(attempt))
            outcome = await self._run_task(entry, sprint_branch, attempt)
            if outcome.status == "escalated":
                self.result.escalated.append((outcome.task_id, outcome.reason or "escalated"))

    def _host_di(self, di_id) -> dict:
        """Re-read this DI's entry in the HOST design-issues artifact after a fix
        dispatch: only the fix agent flips its status / fix_kind there."""
        host = common.load_yaml(
            common.resolve_path(self.config_path, "design_issues", None), optional=True)
        return next((i for i in (host.get("issues") or [])
                     if isinstance(i, dict) and i.get("id") == di_id), None) or {}

    # --- scope amendment (build_blocked) ----------------------------------

    def _handle_build_blocked(self, tid: str, worktree: str) -> bool:
        bb = common.load_yaml(self._worktree_artifact(worktree, "build_blocked"), optional=True)
        # required_files entries are canonically plain strings; tolerate {path: …} dicts.
        files = []
        for r in bb.get("required_files") or []:
            if isinstance(r, str) and r:
                files.append(r)
            elif isinstance(r, dict) and r.get("path"):
                files.append(str(r["path"]))
        if not files:
            return False
        classification = (self.scripts.run(
            "classify-amendment", "--task-id", tid, "--diff", self._write_worktree_diff(worktree)
        ).stdout or "").strip()
        if classification == "reject":
            return False
        if classification != "mechanical_follow_on" and self._scope_amendment_count(tid) >= self.max_scope_amendments:
            return False
        self._add_files_to_scope(worktree, files)
        if classification != "mechanical_follow_on":
            self.state.run("scope_amendment", tid, "--added", ",".join(files), soft=True)
        try:
            self._worktree_artifact(worktree, "build_blocked").unlink()
        except OSError:
            pass
        return True

    def _scope_amendment_count(self, tid: str) -> int:
        try:
            return int(self.state.json("scope_amendment_count", tid).get("value", 0))
        except (ValueError, AttributeError):
            return 0

    def _write_worktree_diff(self, worktree: str) -> str:
        import tempfile
        fd = tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False)
        fd.write(self.git.diff(worktree) or "")
        fd.close()
        return fd.name

    def _add_files_to_scope(self, worktree: str, files: list[str]) -> None:
        import yaml as _yaml
        ct = self._worktree_artifact(worktree, "current_task")
        doc = common.load_yaml(ct, optional=True)
        ftt = list(doc.get("files_to_touch") or [])
        for f in files:
            if f not in ftt:
                ftt.append(f)
        doc["files_to_touch"] = ftt
        ct.parent.mkdir(parents=True, exist_ok=True)
        ct.write_text(_yaml.safe_dump(doc, sort_keys=False))

    # --- helper wrappers (verdict-from-disk) ------------------------------

    def _preserve(self, worktree: str, tid: str) -> None:
        proc = self.scripts.run("preserve-uncommitted", worktree, tid)
        if proc.returncode != 0:
            raise Escalation(f"preserve-uncommitted failed: {proc.stderr.strip()}", tid)

    def _inspect_build(self, worktree: str, tid: str) -> dict:
        proc = self.scripts.run("inspect-build-return", worktree, tid)
        if proc.returncode != 0:
            raise Escalation(f"inspect-build-return failed: {proc.stderr.strip()}", tid)
        return self._script_json(proc)

    def _inspect_review(self, worktree: str, tid: str, build_sha: str) -> dict:
        proc = self.scripts.run("inspect-review-return", worktree, tid, build_sha)
        if proc.returncode != 0:
            raise Escalation(f"inspect-review-return failed: {proc.stderr.strip()}", tid)
        return self._script_json(proc)

    # --- end_of_stage / end_of_sprint -------------------------------------

    async def _finalize_stage(self, frontier: dict, sprint_branch: str) -> None:
        self.state.run("propagate_blocks", soft=True)
        for tid in frontier.get("approved", []):
            build_sha = self._task_state(tid).get("build_commit") or ""
            worktree = self._worktree_for(tid)
            merge_sha = self.git.merge(self._branch_for(worktree), sprint_branch)
            self.state.run("complete_task", tid, "--commit", build_sha, "--merge", merge_sha)
            self.git.remove_worktree(worktree)
            self.result.completed.append(tid)
        await self._heavy_checks(sprint_branch)
        n = (frontier.get("stage") or {}).get("index")
        if n is not None:
            self.state.run("stage_summary", "--stage", str(n), soft=True)
            self.state.run("stage_end", "--stage", str(n), soft=True)
            self.state.run("archive_history", "--cap", str(self.history_cap), soft=True)

    async def _heavy_checks(self, sprint_branch: str) -> None:
        cmd = (self.cfg.get("commands") or {}).get("stage_check")
        if not cmd:
            return
        if await self._run_check(cmd):
            return
        if not await self._stage_fix_cycle(cmd, sprint_branch):
            raise Escalation("stage heavy-check failed and could not be fixed")

    async def _run_check(self, cmd) -> bool:
        argv = shlex.split(cmd) if isinstance(cmd, str) else cmd
        proc = await asyncio.to_thread(subprocess.run, argv, cwd=self.repo_root, capture_output=True)
        return proc.returncode == 0

    async def _stage_fix_cycle(self, cmd, sprint_branch: str) -> bool:
        fix_id = f"STAGE-FIX-{self._sprint_id()}"
        worktree = self._worktree_for(fix_id)
        branch = self._branch_for(worktree)
        self.git.add_worktree(worktree, branch, sprint_branch)
        try:
            self._write_fix_contract(worktree, fix_id, cmd)
            attempt = 1
            while True:
                await asyncio.to_thread(self.dispatcher.dispatch, AGENT_BUILD,
                                        self._build_envelope(fix_id, worktree), worktree)
                await asyncio.to_thread(self._preserve, worktree, fix_id)
                bv = (await asyncio.to_thread(self._inspect_build, worktree, fix_id))["verdict"]
                if bv != "ready_for_review":
                    # build_blocked / design_issue / escalate_no_artifacts on a stage-fix
                    # task is not auto-recoverable here — give up so the boundary escalates.
                    return False
                build_sha = self.git.head_sha(worktree)
                await asyncio.to_thread(self.dispatcher.dispatch, self.passes[0],
                                        self._review_envelope(fix_id, worktree, self.passes[0], sprint_branch), worktree)
                rv = (await asyncio.to_thread(self._inspect_review, worktree, fix_id, build_sha))["verdict"]
                if rv == "approved":
                    self.git.merge(branch, sprint_branch)
                    if await self._run_check(cmd):
                        return True
                    if attempt < self.max_attempts:
                        attempt += 1
                        continue
                    return False
                if rv == "rejected" and attempt < self.max_attempts:
                    attempt += 1
                    continue
                return False
        finally:
            self.git.remove_worktree(worktree)

    def _write_fix_contract(self, worktree: str, fix_id: str, cmd) -> None:
        """Write the stage-fix task contract (task-contract.md shape) and the rejection
        feedback (feedback.yaml.tmpl shape) the fix build addresses."""
        import yaml as _yaml
        cmd_str = cmd if isinstance(cmd, str) else " ".join(map(str, cmd))
        ct = self._worktree_artifact(worktree, "current_task")
        fb = self._worktree_artifact(worktree, "feedback")
        ct.parent.mkdir(parents=True, exist_ok=True)
        ct.write_text(_yaml.safe_dump({
            "id": fix_id,
            "title": "Fix the failing stage heavy-check",
            "covers": [],
            "files_to_touch": [],
            "acceptance_criteria": [
                {"id": "STAGE-FIX.AC-1",
                 "check": f"`{cmd_str}` exits 0 on the sprint branch"}],
            "out_of_scope": ["any change not needed to make the stage check pass"],
            "implementation_notes": [f"the failing stage check command: `{cmd_str}`"],
        }, sort_keys=False))
        fb.parent.mkdir(parents=True, exist_ok=True)
        fb.write_text(_yaml.safe_dump({
            "task_id": fix_id,
            "failures": [{
                "type": "acceptance_criteria_unmet",
                "file": "(repo-wide stage check)",
                "detail": f"`{cmd_str}` exited non-zero on the sprint branch",
                "required_action": "make the stage check pass",
            }],
        }, sort_keys=False))

    async def _end_of_sprint(self, sprint_branch: str) -> None:
        self.state.run("transition", "--to", "end_of_sprint", soft=True)
        for step in (self.cfg.get("closeout") or ["wf-retrospective", "ship"]):
            if step == "ship":
                self._ship(sprint_branch)
            else:
                try:
                    await asyncio.to_thread(
                        self.dispatcher.dispatch, step,
                        json.dumps({"mode": step, "sprint_branch": sprint_branch}), None)
                except Exception:  # noqa: BLE001 — a closeout agent failure never blocks ship
                    pass

    def _ship(self, sprint_branch: str) -> None:
        sprint_doc = common.load_yaml(self.sprint_path, optional=True)
        goal = (sprint_doc.get("sprint") or {}).get("summary") or self._sprint_id()
        body = self._pr_body()
        self.git.push(sprint_branch)
        self.git.open_pr(title=f"{self._sprint_id()}: {goal}", body=body,
                         head=sprint_branch, base=self._base_branch())
        self.state.run("complete_sprint", soft=True)
        self.result.shipped = True

    def _pr_body(self) -> str:
        """GIT_OPERATIONS.md § Ship: completed tasks, escalated/blocked tasks, and
        open design issues — simple markdown lists, empty sections omitted."""
        sections: list[list[str]] = []
        if self.result.completed:
            sections.append(["Completed tasks:"] + [f"- {t}" for t in self.result.completed])
        if self.result.escalated:
            sections.append(["Escalated/blocked tasks:"]
                            + [f"- {t}: {r}" for t, r in self.result.escalated])
        open_dis = self.state.json("unresolved_design_issues").get("issues") or []
        if open_dis:
            sections.append(["Design issues:"]
                            + [f"- {d.get('di_id')} (task {d.get('task_id')})" for d in open_dis])
        return "\n\n".join("\n".join(s) for s in sections)


# ── CLI entrypoint — `wf pipeline run` routes here ────────────────────────────

def main(rest: list[str]) -> int:
    """Launch the live orchestrator with the Claude SDK dispatcher."""
    args = common.base_parser("pipeline run").parse_args(rest)
    from .sdk_adapter import ClaudeSdkDispatcher

    repo_root = common.project_root(args.config)
    dispatcher = ClaudeSdkDispatcher(agents_dir=str(Path(repo_root) / ".claude" / "agents"))
    orch = Orchestrator(args.config, dispatcher)
    try:
        result = asyncio.run(orch.run())
    except Escalation as exc:
        common.die(f"orchestrator halted: {exc.reason}", code=1)
    common.emit(result.as_dict(), args.format)
    return 0 if result.halted is None else 1
